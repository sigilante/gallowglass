"""
Gallowglass MCP server (full-v0).

Stdio MCP server exposing four tools an LLM consumer can call to compile,
inspect, and reason about Gallowglass source through structured calls. The
prelude (8 Core modules) is loaded once at server startup — parsed, typed,
compiled, and pin-hashed — and threaded as priors into every per-call
snippet build. Snippets see the full prelude available for ``use`` imports
without paying the prelude build cost on every request.

Tools (decisions per the planning conversation):

    compile_snippet(source: str, module: str = "Snippet")
        Lex / parse / resolve / typecheck / codegen / pin / render. Returns
        ``{ ir, pins }``: the full Glass IR text for the snippet's module
        (with FQ names, type annotations, effect rows, contracts, source
        Locs, pin hashes) and a manifest of ``fq_name → pin_hash``.

    infer_type(source: str, line: int, col: int, module: str = "Snippet")
        Wraps ``bootstrap.ide.type_at_position``. Returns ``{ type }`` with
        the inferred type at the cursor position, or ``{ type: null }`` if
        no expression covers it. (Loc lines and columns are 1-based.)

    explain_effect_row(source: str, fn_name: str, module: str = "Snippet")
        Walks the FQ name's outermost ``TArr`` chain, finds the codomain
        effect row, and returns ``{ effects, pure, full_type }``. The
        ``effects`` list names each effect; ``pure`` is true iff the row
        is empty (and the function is therefore pure).

    render_fragment(source: str, fn_name: str, budget: int | None = None,
                    module: str = "Snippet")
        Wraps ``glass_ir.render_fragment``. Returns ``{ ir, deps }`` for a
        single definition with its pin-hash dep list. When ``budget`` is
        an int, the fragment is truncated to fit (cheapest cut first:
        deepest ``@![pin#...]`` lines, then a body marker).

Module-level state: ``_PRELUDE`` is a lazily-built ``PreludeSnapshot``
cached for the process lifetime. Subsequent calls reuse it. Re-importing
or restarting the server rebuilds it.

No Reaver dependency — this server stops at Glass IR + pin hashes; it does
not produce Plan Asm text or invoke the Reaver runtime.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

from bootstrap import ast
from bootstrap.lexer import lex
from bootstrap.parser import parse, ParseError
from bootstrap.scope import resolve, ScopeError, Env
from bootstrap.typecheck import (
    TypeChecker, TypecheckError,
    pp_type, pp_scheme,
    TArr, TComp, TRow,
)
from bootstrap.codegen import Compiler, CodegenError
from bootstrap.glass_ir import (
    render_module, render_fragment as gi_render_fragment,
    collect_pin_deps,
)
from bootstrap.pin import compute_pin_id
from dev.harness.plan import P
from bootstrap.ide import type_at_position
from bootstrap.build import PRELUDE_MODULES, BuildError


PRELUDE_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', 'prelude', 'src', 'Core'
))


# ---------------------------------------------------------------------------
# Prelude snapshot — built once, reused for every snippet call.
# ---------------------------------------------------------------------------

@dataclass
class PreludeSnapshot:
    """Captured state from a one-shot prelude compile.

    Threaded as priors into every snippet build so the prelude is only
    parsed/typed/compiled/hashed once per server lifetime.
    """
    module_envs: dict[str, Env]
    compiled: dict[str, Any]              # fq -> pin-wrapped PVal
    pin_ids: dict[str, str]               # fq -> blake3 hex
    type_env: dict                        # fq -> Scheme
    type_constructors: dict               # type_fq -> [(con_fq, arity)]
    class_methods: dict
    class_defaults: dict
    class_constraints: dict
    con_info: dict
    manifest: dict                        # for collect_pin_deps lookups


_PRELUDE: PreludeSnapshot | None = None


def _typecheck_capture(
    program, env, module: str, filename: str,
    prior_te: dict | None = None,
    prior_tc: dict | None = None,
    record_expr_types: bool = False,
) -> tuple[dict, dict, dict | None]:
    """Run a TypeChecker and capture both the type_env and the
    type_constructors registry — the public ``typecheck()`` entry only
    returns the env. Mirrors ``typecheck_with_types`` for the recording
    side-table when ``record_expr_types`` is True.
    """
    tc = TypeChecker(module, filename)
    if prior_te:
        tc.type_env.update(prior_te)
    if prior_tc:
        tc.type_constructors.update(prior_tc)
    if record_expr_types:
        tc.expr_types = {}
    tc.check(program, env)
    expr_types = None
    if record_expr_types:
        expr_types = {k: tc._zonk(v) for k, v in tc.expr_types.items()}
    return tc.type_env, tc.type_constructors, expr_types


def load_prelude(prelude_dir: str = PRELUDE_DIR) -> PreludeSnapshot:
    """Compile the 8 Core prelude modules once, capturing the priors a
    snippet build needs to thread through.

    The traversal mirrors ``bootstrap.build.build_modules`` but stops at
    pin-hashing; no Plan Asm text is emitted and no Reaver code is touched.
    """
    sources: list[tuple[str, str]] = []
    for mod in PRELUDE_MODULES:
        short = mod.split('.')[-1]
        path = os.path.join(prelude_dir, f'{short}.gls')
        with open(path) as f:
            sources.append((mod, f.read()))

    module_envs: dict[str, Env] = {}
    all_compiled: dict[str, Any] = {}
    type_env: dict = {}
    type_constructors: dict = {}
    class_methods: dict = {}
    class_defaults: dict = {}
    class_constraints: dict = {}
    con_info: dict = {}

    for mod_name, src_text in sources:
        filename = f'<{mod_name}>'
        prog = parse(lex(src_text, filename), filename)
        resolved, env = resolve(prog, mod_name, module_envs, filename)
        module_envs[mod_name] = env

        # Class metadata accumulates from each module's resolver env.
        class_methods.update(env.class_methods)

        # Typecheck — capture both env and constructors so we can thread them
        # into snippet builds.
        new_te, new_tc, _ = _typecheck_capture(
            resolved, env, mod_name, filename,
            prior_te=type_env,
            prior_tc=type_constructors,
        )
        type_env.update(new_te)
        type_constructors.update(new_tc)

        # Codegen — pin-wrap every value so the snippet sees a content-
        # addressed prelude.
        compiler = Compiler(
            module=mod_name,
            pre_compiled=all_compiled,
            pre_class_methods=class_methods,
            pre_class_defaults=class_defaults,
            pre_class_constraints=class_constraints,
            pre_con_info=con_info,
        )
        compiled = compiler.compile(resolved)
        compiled = {k: P(v) for k, v in compiled.items()}
        all_compiled.update(compiled)
        class_defaults.update(compiler._class_defaults)
        class_constraints.update(compiler._class_constraints)
        con_info.update(compiler.con_info)

    pin_ids = {fq: compute_pin_id(v) for fq, v in all_compiled.items()}
    manifest = {'module': 'Core', 'pins': pin_ids}

    return PreludeSnapshot(
        module_envs=module_envs,
        compiled=all_compiled,
        pin_ids=pin_ids,
        type_env=type_env,
        type_constructors=type_constructors,
        class_methods=class_methods,
        class_defaults=class_defaults,
        class_constraints=class_constraints,
        con_info=con_info,
        manifest=manifest,
    )


def get_prelude() -> PreludeSnapshot:
    """Lazy-init the cached prelude. Subsequent calls reuse the snapshot."""
    global _PRELUDE
    if _PRELUDE is None:
        _PRELUDE = load_prelude()
    return _PRELUDE


# ---------------------------------------------------------------------------
# Snippet compile — runs one module against the cached prelude priors.
# ---------------------------------------------------------------------------

@dataclass
class SnippetBuild:
    """Result of compiling one snippet module against the prelude."""
    module: str
    resolved: Any
    type_env: dict                  # snippet's own + prelude's
    type_constructors: dict
    expr_types: dict[int, Any]      # for type-at-position queries
    compiled: dict[str, Any]        # snippet-only fq → pin-wrapped PVal
    pin_ids: dict[str, str]         # snippet-only
    manifest: dict                  # combined snippet + prelude pins


def _compile_snippet(
    source: str,
    module: str,
    prelude: PreludeSnapshot,
) -> SnippetBuild:
    """Run the full pipeline against the prelude priors. Errors propagate;
    the caller maps them to the structured envelope.
    """
    filename = f'<{module}>'
    prog = parse(lex(source, filename), filename)
    resolved, env = resolve(prog, module, prelude.module_envs, filename)

    snippet_te, snippet_tc, expr_types = _typecheck_capture(
        resolved, env, module, filename,
        prior_te=prelude.type_env,
        prior_tc=prelude.type_constructors,
        record_expr_types=True,
    )

    compiler = Compiler(
        module=module,
        pre_compiled=prelude.compiled,
        pre_class_methods=prelude.class_methods,
        pre_class_defaults=prelude.class_defaults,
        pre_class_constraints=prelude.class_constraints,
        pre_con_info=prelude.con_info,
    )
    snippet_compiled_raw = compiler.compile(resolved)
    snippet_compiled = {k: P(v) for k, v in snippet_compiled_raw.items()}
    snippet_pins = {fq: compute_pin_id(v) for fq, v in snippet_compiled.items()}

    combined_pins = dict(prelude.pin_ids)
    combined_pins.update(snippet_pins)
    manifest = {'module': module, 'pins': combined_pins}

    return SnippetBuild(
        module=module,
        resolved=resolved,
        type_env=snippet_te,
        type_constructors=snippet_tc,
        expr_types=expr_types or {},
        compiled=snippet_compiled,
        pin_ids=snippet_pins,
        manifest=manifest,
    )


# ---------------------------------------------------------------------------
# Error envelope — one shape across all tools.
# ---------------------------------------------------------------------------

def _loc_dict(err: Exception) -> dict | None:
    loc = getattr(err, 'loc', None)
    if loc is None:
        return None
    return {'file': loc.file, 'line': loc.line, 'col': loc.col}


def _error_envelope(stage: str, err: Exception) -> dict:
    return {
        'error': {
            'stage': stage,
            'message': str(err),
            'loc': _loc_dict(err),
        }
    }


def _stage_for(err: Exception) -> str:
    if isinstance(err, ParseError):
        return 'parse'
    if isinstance(err, ScopeError):
        return 'scope'
    if isinstance(err, TypecheckError):
        return 'typecheck'
    if isinstance(err, (CodegenError, BuildError)):
        return 'codegen'
    return 'internal'


def _run_safely(fn, *args, **kwargs) -> dict:
    """Catch the four user-reachable bootstrap errors plus a generic fallback,
    and return a structured envelope. Anything else is re-raised — those are
    bugs the server should surface, not paper over.
    """
    try:
        return fn(*args, **kwargs)
    except (ParseError, ScopeError, TypecheckError, CodegenError, BuildError) as e:
        return _error_envelope(_stage_for(e), e)


# ---------------------------------------------------------------------------
# Tool implementations — pure dict-in / dict-out functions.
# ---------------------------------------------------------------------------

def tool_compile_snippet(args: dict) -> dict:
    source = args['source']
    module = args.get('module', 'Snippet')
    return _run_safely(_do_compile_snippet, source, module)


def _do_compile_snippet(source: str, module: str) -> dict:
    prelude = get_prelude()
    build = _compile_snippet(source, module, prelude)
    ir = render_module(build.resolved, module,
                       manifest=build.manifest,
                       type_env=build.type_env)
    return {'ir': ir, 'pins': dict(build.pin_ids)}


def tool_infer_type(args: dict) -> dict:
    source = args['source']
    line = int(args['line'])
    col = int(args['col'])
    module = args.get('module', 'Snippet')
    return _run_safely(_do_infer_type, source, module, line, col)


def _do_infer_type(source: str, module: str, line: int, col: int) -> dict:
    prelude = get_prelude()
    build = _compile_snippet(source, module, prelude)
    filename = f'<{module}>'
    ty = type_at_position(build.resolved, build.expr_types, line, col,
                          filename=filename)
    return {'type': pp_type(ty) if ty is not None else None}


def tool_explain_effect_row(args: dict) -> dict:
    source = args['source']
    fn_name = args['fn_name']
    module = args.get('module', 'Snippet')
    return _run_safely(_do_explain_effect_row, source, module, fn_name)


def _do_explain_effect_row(source: str, module: str, fn_name: str) -> dict:
    prelude = get_prelude()
    build = _compile_snippet(source, module, prelude)
    fq = f'{module}.{fn_name}'
    if fq not in build.type_env:
        return {
            'error': {
                'stage': 'lookup',
                'message': f"definition '{fq}' not found in module '{module}'",
                'loc': None,
            }
        }
    scheme = build.type_env[fq]
    full_type = pp_scheme(scheme)
    effects, pure = _extract_effect_row(scheme.body)
    return {
        'effects': effects,
        'pure': pure,
        'full_type': full_type,
    }


def _extract_effect_row(ty: Any) -> tuple[list[str], bool]:
    """Walk to the rightmost codomain and return its effect row's named
    effects (sorted) and whether the row is empty (i.e. the function is
    pure). For a non-effectful type the row is the empty closed row;
    'pure' is True iff there are zero named effects in that row.

    Treats ``a → b → {Eff} c`` as having effects from the innermost arrow's
    codomain only — that's where the row lives in Gallowglass's surface
    syntax. For curried functions the outer arrows have implicit pure rows.
    """
    cur = ty
    while isinstance(cur, TArr):
        cur = cur.cod
    if isinstance(cur, TComp):
        row = cur.row
        if isinstance(row, TRow):
            names = sorted(row.effects.keys())
            return names, len(names) == 0
    return [], True


def tool_render_fragment(args: dict) -> dict:
    source = args['source']
    fn_name = args['fn_name']
    module = args.get('module', 'Snippet')
    budget = args.get('budget', None)
    return _run_safely(_do_render_fragment, source, module, fn_name, budget)


def _do_render_fragment(source: str, module: str, fn_name: str,
                        budget: int | None) -> dict:
    prelude = get_prelude()
    build = _compile_snippet(source, module, prelude)
    fq = f'{module}.{fn_name}'
    decl = _find_decl(build.resolved, fn_name)
    if decl is None:
        return {
            'error': {
                'stage': 'lookup',
                'message': f"definition '{fq}' not found in module '{module}'",
                'loc': None,
            }
        }
    pin_id = build.pin_ids.get(fq)
    deps = collect_pin_deps(fq, decl, module, build.manifest)
    ir = gi_render_fragment(
        fq, decl, pin_id=pin_id, module=module, deps=deps,
        budget=budget if budget is not None else 4096,
        type_env=build.type_env,
    )
    if budget is not None:
        ir = _enforce_budget(ir, budget)
    return {'ir': ir, 'deps': dict(deps)}


def _find_decl(resolved, name: str):
    for decl in resolved.decls:
        if isinstance(decl, ast.DeclLet) and decl.name == name:
            return decl
    return None


def _count_tokens(text: str) -> int:
    """Whitespace-split token count — coarse but stable."""
    return len(text.split())


def _enforce_budget(ir: str, budget: int) -> str:
    """Truncate a Glass IR fragment to fit within ``budget`` whitespace
    tokens. Strategy: if it already fits, return as-is. Otherwise drop the
    deepest dep ``@![pin#...]`` lines first (the LLM can re-fetch any of
    them with another ``render_fragment`` call), then truncate the body
    with a marker.
    """
    if _count_tokens(ir) <= budget:
        return ir

    lines = ir.splitlines()
    dep_idx = [i for i, l in enumerate(lines) if l.startswith('@![pin#')]
    # Drop deps from the bottom up (deepest in the printed dep list first).
    for i in reversed(dep_idx):
        if _count_tokens('\n'.join(lines[:i] + lines[i+1:])) <= budget:
            del lines[i]
            return '\n'.join(lines)
        del lines[i]

    # Still over budget after dropping all deps — truncate body line-by-line.
    truncated = list(lines)
    while truncated and _count_tokens('\n'.join(truncated)) > budget:
        truncated.pop()
    truncated.append(f'-- ⟨truncated to fit {budget}-token budget⟩')
    return '\n'.join(truncated)


# ---------------------------------------------------------------------------
# MCP wiring — stdio transport, tool registration.
# ---------------------------------------------------------------------------

TOOL_DEFS = [
    {
        'name': 'compile_snippet',
        'description': (
            'Compile a Gallowglass source snippet against the cached '
            'prelude. Returns Glass IR text with FQ names, type annotations, '
            'effect rows, contracts, source Locs, pin hashes — plus a '
            'manifest of fq_name -> pin_hash for the snippet.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'source': {'type': 'string',
                           'description': 'Gallowglass source text'},
                'module': {'type': 'string',
                           'description': "Module name (default 'Snippet')",
                           'default': 'Snippet'},
            },
            'required': ['source'],
        },
    },
    {
        'name': 'infer_type',
        'description': (
            'Return the inferred type of the innermost expression at '
            '(line, col), 1-based. Null if no expression covers the cursor.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'source': {'type': 'string'},
                'line': {'type': 'integer'},
                'col': {'type': 'integer'},
                'module': {'type': 'string', 'default': 'Snippet'},
            },
            'required': ['source', 'line', 'col'],
        },
    },
    {
        'name': 'explain_effect_row',
        'description': (
            'Return the effect row of a top-level definition. '
            'Lists named effects, marks pure if the row is empty, '
            'and returns the full pretty-printed scheme.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'source': {'type': 'string'},
                'fn_name': {'type': 'string',
                            'description': 'Bare name (no module prefix)'},
                'module': {'type': 'string', 'default': 'Snippet'},
            },
            'required': ['source', 'fn_name'],
        },
    },
    {
        'name': 'render_fragment',
        'description': (
            'Render one definition as a Glass IR fragment with its '
            'pin-anchored dep list. With an optional budget, truncates '
            "to fit (drops deepest deps first, then body)."
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'source': {'type': 'string'},
                'fn_name': {'type': 'string'},
                'module': {'type': 'string', 'default': 'Snippet'},
                'budget': {
                    'type': ['integer', 'null'],
                    'description': (
                        'Optional whitespace-token budget. '
                        'Null = no enforcement.'
                    ),
                    'default': None,
                },
            },
            'required': ['source', 'fn_name'],
        },
    },
]


_TOOL_FUNCS = {
    'compile_snippet': tool_compile_snippet,
    'infer_type': tool_infer_type,
    'explain_effect_row': tool_explain_effect_row,
    'render_fragment': tool_render_fragment,
}


async def _serve_stdio() -> None:
    """Run the MCP server over stdio. Imports the SDK lazily so that
    ``import bootstrap.mcp_server`` works in environments without ``mcp``
    installed (e.g. unit-test runs that exercise only the tool functions).
    """
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.server.models import InitializationOptions
    from mcp.server import NotificationOptions
    from mcp.types import Tool, TextContent

    # Force prelude to be built before we accept any requests so the first
    # tool call doesn't pay the build cost.
    get_prelude()

    server = Server('gallowglass')

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return [Tool(**td) for td in TOOL_DEFS]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
        fn = _TOOL_FUNCS.get(name)
        if fn is None:
            payload = {'error': {'stage': 'dispatch',
                                 'message': f"unknown tool '{name}'",
                                 'loc': None}}
        else:
            payload = fn(arguments or {})
        return [TextContent(type='text', text=json.dumps(payload))]

    async with stdio_server() as (reader, writer):
        await server.run(
            reader, writer,
            InitializationOptions(
                server_name='gallowglass',
                server_version='0.0.1',
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    asyncio.run(_serve_stdio())


if __name__ == '__main__':
    main()
