"""
Gallowglass Jupyter kernel — A1 architecture (Python harness backend).

A Jupyter kernel that evaluates Gallowglass cells in-process via the
Python harness (``dev.harness.eval.bevaluate``). Sits next to
``bootstrap/mcp_server.py`` and shares the same prelude-loading and
snippet-compile machinery.

Architecture
------------

    Jupyter client (notebook / lab)
            │  ZeroMQ + Jupyter messaging protocol
            ▼
    GallowglassKernel (ipykernel.kernelbase.Kernel subclass)
            │
            ▼
    GallowglassEvaluator  ← pure Python, testable in isolation
            │
            ▼
    bootstrap.{lexer, parser, scope, codegen}
            │
            ▼
    dev.harness.eval.bevaluate
            │
            ▼
    PLAN values rendered as Jupyter display data

The evaluator is intentionally separated from the Kernel class so it
can be unit-tested without spinning up an ipykernel instance.

Cell mode
---------

Each cell is interpreted as either:

* an **expression**, in which case the cell's value is the displayed
  result; or
* a **program fragment** (one or more top-level declarations), in
  which case the declarations are accumulated into the notebook's
  module source and no value is displayed.

Detection: try wrapping the cell as ``let _cell_N = <code>`` and
parsing. If that parses, the cell is an expression. Otherwise the
cell is parsed as a program fragment and appended to the accumulated
source on success.

State
-----

The notebook acts as a single module (default name ``Notebook``).
Accumulated declarations from prior cells stay in scope; each new
cell sees them. Recompile happens on every cell — the compile cost
is dominated by the prelude (cached in a ``PreludeSnapshot`` after
the first cell) plus the cumulative cell source, which stays small
in practice.

Limitations (Phase G+ open work)
--------------------------------

* Result formatting is text/plain only. ``Nat`` renders as decimal;
  other PLAN values render as a structural debug-ish form. A real
  ``Show`` typeclass-driven renderer is M14.5 on the roadmap.
* The Python harness has a recursion ceiling; cells whose evaluation
  exceeds that surface as ``RecursionError``.
* No tab completion or doc inspection yet — the MCP server's
  ``infer_type`` / ``render_fragment`` would be the natural
  backends for those.

Running
-------

Install the kernelspec::

    python -m bootstrap.jupyter_kernel install

Launch a notebook with the ``Gallowglass`` kernel selected. The
kernel binary is invoked as::

    python -m bootstrap.jupyter_kernel
"""

from __future__ import annotations

import os
import sys
import traceback
from dataclasses import dataclass
from typing import Any

from bootstrap.lexer import lex
from bootstrap.parser import parse, ParseError
from bootstrap.scope import resolve, ScopeError
from bootstrap.codegen import Compiler, CodegenError
from bootstrap.typecheck import TypecheckError

from dev.harness.plan import P, is_nat, is_pin, is_law, is_app
from dev.harness.eval import bevaluate, register_prelude_jets

# Prelude loading + typecheck-capture both reuse the MCP server's
# snapshot machinery — both tools want the same "compile prelude once,
# thread through every subsequent build" shape. Importing functions
# (not module state) means we do not share MCP's process-global cache.
from bootstrap.mcp_server import (
    load_prelude, PreludeSnapshot, _typecheck_capture,
)
from bootstrap.value_render import (
    render_typed, render_typed_html, render_decl_summary_html,
)


DEFAULT_MODULE = 'Notebook'


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------

@dataclass
class CellResult:
    """Result of evaluating one cell.

    ``value_text`` is the ``text/plain`` rendering. ``value_html`` is
    an optional ``text/html`` rendering — populated whenever the
    kernel can produce a colourised version (the type-driven path
    and decl summaries can; structural fallbacks set it to ``None``
    so the Jupyter client renders the plain-text form unchanged).

    Exactly one of ``value_text`` / ``error`` is populated for non-
    silent cells. ``decls_only`` cells with no contributed decls
    have all of ``value_text`` / ``value_html`` / ``error`` as
    ``None``.
    """
    value_text: str | None = None
    value_html: str | None = None
    error: dict | None = None
    decls_only: bool = False


# ---------------------------------------------------------------------------
# Evaluator — Jupyter-protocol-free, fully testable
# ---------------------------------------------------------------------------

class GallowglassEvaluator:
    """Stateful Gallowglass evaluator.

    Owns the prelude snapshot and the notebook's accumulated
    declarations. ``eval_cell(source)`` is the single entry point;
    it returns a ``CellResult`` describing what to display.
    """

    def __init__(self, *, module: str = DEFAULT_MODULE,
                 prelude: PreludeSnapshot | None = None):
        self.module = module
        self.prelude = prelude if prelude is not None else load_prelude()
        # Register Core.Nat / Core.Text / Core.List jets. Without this,
        # calls to `Nat.mul`/etc. recurse through the evaluator (~7K
        # Python frames per user-level recursion); with jets they
        # dispatch to native Python in one frame.
        register_prelude_jets(self.prelude.compiled)
        # Accumulator of source from prior cells. Declarations only —
        # expression-mode cells are wrapped on the fly and never enter
        # this string.
        self._accumulated_source: str = ''
        # Counter for synthesised expression-cell names. Monotonic;
        # never decremented, so each cell has a unique identifier even
        # across re-evaluations.
        self._cell_counter: int = 0
        # Accumulated type env: prelude types + every successfully
        # compiled declaration so far. Updated by _eval_program_fragment.
        self._type_env: dict = dict(self.prelude.type_env)

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def eval_cell(self, source: str) -> CellResult:
        """Lex, parse, compile, and evaluate one cell.

        Returns a ``CellResult``. Exceptions raised by the bootstrap
        pipeline are caught and converted to structured errors;
        unexpected exceptions propagate (they're bugs).

        Three attempts in order:

        1. **Show-aware expression mode.** Wrap as
           ``let _cell_N = <code>; let _show_N : Text = display _cell_N``
           where ``display`` is a constrained ``Show a => a → Text``
           function. If typecheck succeeds, the cell's value has a
           Show instance and we evaluate ``_show_N`` (the rendered
           Text). This is the path the user wants — pretty output.

        2. **Plain expression mode.** If (1) fails to typecheck (no
           Show instance for the cell's type, e.g. functions), fall
           back to evaluating ``_cell_N`` directly and rendering
           structurally.

        3. **Program-fragment mode.** If both expression-mode parses
           fail, treat the cell as one or more top-level decls and
           append to the accumulator.
        """
        if not source.strip():
            return CellResult(decls_only=True)

        self._cell_counter += 1
        cell_name = f'_cell_{self._cell_counter}'

        # Attempt 1: pure expression. Wrap as ``let _cell_N = <code>``
        # and compile against the existing accumulator. Type-driven
        # render gives constructor names + per-type colours.
        wrapped = self._wrap_as_expression(source, cell_name)
        expr_error: dict | None = None
        try:
            forced, cell_type, type_env, con_info = (
                self._eval_expression(wrapped, cell_name)
            )
        except _NotAnExpression:
            pass
        except _CellError as e:
            # Expression mode parsed but pipeline failed. Save the
            # error in case the split / program paths also fail —
            # we'll surface it as the most-informative diagnosis.
            expr_error = e.envelope
        else:
            text, html_form = self._format_value_both(
                forced, cell_type, type_env, con_info,
            )
            return CellResult(value_text=text, value_html=html_form)

        # Attempt 2: decls + trailing expression. Try splitting the
        # cell at line boundaries (largest decl-prefix first) and
        # parsing the prefix as a program with the suffix as the
        # cell's expression. This is the common Jupyter pattern
        # ``let foo = ...\nfoo + 1`` that pure expression mode
        # can't handle (let-in requires an explicit `in`) and pure
        # program mode parses wrong (the lambda body absorbs the
        # trailing expression as application).
        split = self._try_decls_with_trailing_expression(source, cell_name)
        if split is not None:
            return split

        # Attempt 3: program mode (decls only). The cell contributes
        # declarations to the accumulator. Display a one-line summary
        # per new declaration so the user can confirm what the cell
        # defined — without this the cell looks like a no-op.
        try:
            summaries = self._eval_program_fragment(source)
        except _CellError as e:
            # All three modes failed. Prefer the expression-mode
            # error if it was a real diagnostic — that's the path
            # most likely to match what the user intended for a
            # multi-line cell with trailing values.
            return CellResult(error=expr_error or e.envelope)

        if summaries:
            return CellResult(
                decls_only=True,
                value_text='\n'.join(summaries),
                value_html=render_decl_summary_html(summaries),
            )
        return CellResult(decls_only=True)

    def reset(self) -> None:
        """Clear accumulated declarations; keep the prelude snapshot.

        The cell counter is not reset — synthesised names stay unique
        across the kernel's whole lifetime so log output and any
        external reference remains stable.
        """
        self._accumulated_source = ''
        self._type_env = dict(self.prelude.type_env)

    def query_type(self, name: str) -> str | None:
        """Return the pretty-printed type scheme for *name*, or None.

        Tries the notebook-qualified FQ name first, then searches for
        any binding whose short name matches (for prelude names).
        """
        from bootstrap.typecheck import pp_scheme
        fq_direct = f'{self.module}.{name}'
        if fq_direct in self._type_env:
            return pp_scheme(self._type_env[fq_direct])
        for fq, scheme in self._type_env.items():
            if fq.rsplit('.', 1)[-1] == name:
                return pp_scheme(scheme)
        return None

    def names_in_scope(self) -> list[str]:
        """Return sorted unqualified names in the current type env.

        Excludes internal synthesised names (those starting with ``_``).
        Used by the REPL's tab completer.
        """
        seen: set[str] = set()
        for fq in self._type_env:
            short = fq.rsplit('.', 1)[-1]
            if not short.startswith('_'):
                seen.add(short)
        return sorted(seen)

    # ------------------------------------------------------------------
    # Expression mode
    # ------------------------------------------------------------------

    def _wrap_as_expression(self, source: str, cell_name: str) -> str:
        """Wrap the cell as ``<accumulated>\\nlet <cell_name> = <source>``.

        The wrap is consumed by attempt-1 only; the accumulator is
        not modified unless attempt-2 succeeds.
        """
        prefix = self._accumulated_source
        if prefix and not prefix.endswith('\n'):
            prefix = prefix + '\n'
        return f'{prefix}let {cell_name} = {source}\n'

    def _eval_expression(self, wrapped_source: str, cell_name: str) -> tuple:
        """Compile ``wrapped_source`` and evaluate the cell binding.

        Returns ``(forced_value, cell_type, type_env, con_info)`` so
        the caller can render the value via the type-driven path.
        ``cell_type`` may be ``None`` if the typechecker couldn't pin
        a concrete type to the cell binding (rare but defensive).

        Raises ``_NotAnExpression`` if the wrap fails to parse — that's
        the signal to fall back to program mode. Other pipeline errors
        raise ``_CellError`` with a structured envelope.

        Pipeline includes typechecking so class-method calls (``show``,
        ``eq``, ``compare``, …) get their dictionary args inserted at
        codegen time. Without the typecheck, ``show 42`` would surface
        as a codegen ``unbound variable`` error.
        """
        filename = f'<cell {cell_name}>'
        try:
            tokens = lex(wrapped_source, filename)
            program = parse(tokens, filename)
        except ParseError as e:
            # Could be either "the cell isn't an expression" (legitimate)
            # or "the cell is malformed" (real error). We can't tell
            # without trying program-mode parse, so raise the
            # not-an-expression sentinel and let the caller decide.
            raise _NotAnExpression() from e

        # From here on, errors are real — the wrap parsed, so we
        # genuinely tried to compile and something went wrong.
        try:
            resolved, env = resolve(program, self.module,
                                    self.prelude.module_envs, filename)
            type_env, expr_types = self._typecheck(resolved, env, filename)
            compiler, compiled = self._compile_with_prelude(resolved, expr_types)
        except (ScopeError, TypecheckError, CodegenError) as e:
            raise _CellError(_error_envelope(_stage_for(e), e)) from e

        fq = f'{self.module}.{cell_name}'
        if fq not in compiled:
            # Wrap parsed but produced no binding for the synthesised
            # name. Treat as not-an-expression so program mode can try.
            raise _NotAnExpression()

        # Cell type comes from the let-binding's scheme. Strip the
        # ∀-quantification to expose the monotype the renderer wants.
        cell_type = self._cell_type(type_env, fq)
        forced = self._force(compiled[fq])
        return forced, cell_type, type_env, compiler.con_info

    # ------------------------------------------------------------------
    # Mixed mode: decls + trailing expression
    # ------------------------------------------------------------------

    def _try_decls_with_trailing_expression(self, source: str,
                                             cell_name: str) -> CellResult | None:
        """Split the cell at a line boundary into a leading
        declaration block and a trailing expression. Tries the
        largest-prefix-first so we accumulate as many decls as
        possible — important for cells that define a function and
        then call it (``let f = … ; f x``).

        Returns ``None`` when no split parses both halves cleanly.
        On success, the new decls are committed to the accumulator
        and the result of the trailing expression becomes the
        cell's value.

        The "decls + trailing expression" pattern is what Jupyter
        users intuitively type. Pure expression mode can't handle
        it (the inner ``let`` would need an explicit ``in``); pure
        program mode parses the trailing expression as part of the
        last decl's body via the parser's greedy lambda-body rule
        and surfaces a typecheck error like "cannot unify Nat with
        ...".  This split path is the dedicated fix.
        """
        lines = source.split('\n')
        if len(lines) < 2:
            # Single-line cells can't be "decls + trailing".
            return None

        # Build the existing-accumulator prefix once; each split
        # candidate appends its own decls onto it.
        accum_prefix = self._accumulated_source
        if accum_prefix and not accum_prefix.endswith('\n'):
            accum_prefix += '\n'

        # Walk split points from the largest decl-prefix downward.
        # Skip blank lines: we want the split right *before* the
        # first non-empty line that starts a trailing expression.
        for split_idx in range(len(lines) - 1, 0, -1):
            prefix = '\n'.join(lines[:split_idx]).strip()
            suffix = '\n'.join(lines[split_idx:]).strip()
            if not prefix or not suffix:
                continue

            candidate_decls = accum_prefix + prefix + '\n'
            wrapped_expr = (
                f'{candidate_decls}let {cell_name} = {suffix}\n'
            )
            try:
                forced, cell_type, type_env, con_info = (
                    self._eval_expression(wrapped_expr, cell_name)
                )
            except (_NotAnExpression, _CellError):
                continue

            # Both halves typechecked and the trailing expression
            # forced cleanly. Commit the new decls and return the
            # value as the cell's result. Decl summaries are
            # *not* included in the displayed output for this mode
            # — Jupyter convention is "assignments are silent,
            # the trailing expression echoes."
            self._accumulated_source = candidate_decls
            text, html_form = self._format_value_both(
                forced, cell_type, type_env, con_info,
            )
            return CellResult(value_text=text, value_html=html_form)

        return None

    # ------------------------------------------------------------------
    # Program mode
    # ------------------------------------------------------------------

    def _eval_program_fragment(self, source: str) -> list[str]:
        """Append ``source`` to the accumulator and recompile.

        Returns a list of human-readable summaries for the new top-
        level declarations contributed by this cell — one per
        ``DeclLet`` (``name : Type``), ``DeclType`` (``type T``), or
        ``DeclUse`` (``use Mod``). Other declaration kinds are
        silently included only if their effect is observable
        (currently none — instances/effects don't surface here).

        On success, the accumulator commits the new source. On
        failure, the accumulator stays as it was — partial states
        don't leak into subsequent cells.
        """
        candidate = self._accumulated_source
        if candidate and not candidate.endswith('\n'):
            candidate = candidate + '\n'
        candidate = candidate + source
        if not candidate.endswith('\n'):
            candidate = candidate + '\n'

        filename = f'<cell {self._cell_counter}>'
        try:
            tokens = lex(candidate, filename)
            program = parse(tokens, filename)
            resolved, env = resolve(program, self.module,
                                    self.prelude.module_envs, filename)
            type_env, expr_types = self._typecheck(resolved, env, filename)
            self._compile_with_prelude(resolved, expr_types)
        except (ParseError, ScopeError, TypecheckError, CodegenError) as e:
            raise _CellError(_error_envelope(_stage_for(e), e)) from e

        # All phases succeeded — commit the new source and type env.
        self._accumulated_source = candidate
        self._type_env = type_env
        return self._summarise_cell_decls(source, type_env)

    def _summarise_cell_decls(self, source: str, type_env: dict) -> list[str]:
        """Re-parse the cell source alone to identify which decls
        the cell contributed, then look each one up in ``type_env``
        for its scheme so the display can carry type info.

        Re-parsing is cheap (one cell's worth of source) and avoids
        threading new-vs-old decl tracking through the whole
        accumulator pipeline. Failures fall through silently — if
        the cell parsed in-context-of-accumulator but doesn't parse
        standalone (e.g. it relies on a prior `use`), we just don't
        show a summary.
        """
        from bootstrap.ast import DeclLet, DeclType, DeclUse
        from bootstrap.typecheck import pp_scheme

        try:
            tokens = lex(source, '<cell summary>')
            program = parse(tokens, '<cell summary>')
        except ParseError:
            return []

        summaries: list[str] = []
        for decl in program.decls:
            if isinstance(decl, DeclLet):
                fq = f'{self.module}.{decl.name}'
                scheme = type_env.get(fq)
                if scheme is not None:
                    summaries.append(f'{decl.name} : {pp_scheme(scheme)}')
                else:
                    summaries.append(decl.name)
            elif isinstance(decl, DeclType):
                ctor_names = ' | '.join(c.name for c in decl.constructors)
                summaries.append(f'type {decl.name} = {ctor_names}')
            elif isinstance(decl, DeclUse):
                mod = '.'.join(decl.module_path)
                summaries.append(f'use {mod}')
            # Other decl kinds (instance, class, effect, mod) are
            # observable through their effect on later cells; we
            # don't surface a summary line for them yet.
        return summaries

    # ------------------------------------------------------------------
    # Compile + evaluate helpers
    # ------------------------------------------------------------------

    def _typecheck(self, resolved, env, filename: str) -> tuple:
        """Typecheck against the prelude priors.

        Returns ``(type_env, expr_types)``:

        * ``type_env`` (FQ-name → :class:`Scheme`) — used by the
          type-driven value renderer to look up constructor schemes
          and substitute field types from a constructor's
          instantiation.
        * ``expr_types`` (``id(expr)`` → MonoType) — used by codegen
          to insert dictionary arguments at constrained call sites;
          without it, class-method calls surface as ``unbound
          variable`` codegen errors.

        Both maps include the prelude priors merged in, so the
        renderer can resolve types defined in any ``Core.*`` module.
        """
        type_env, _tc, expr_types = _typecheck_capture(
            resolved, env, self.module, filename,
            prior_te=self.prelude.type_env,
            prior_tc=self.prelude.type_constructors,
            record_expr_types=True,
        )
        return type_env, expr_types or {}

    def _compile_with_prelude(self, resolved, expr_types: dict) -> tuple:
        """Run codegen against the prelude priors. Returns
        ``(compiler, compiled)`` so the caller can read
        ``compiler.con_info`` for the type-driven renderer's
        constructor lookups (the prelude's con_info already merged
        in via ``pre_con_info``).

        Mirrors ``bootstrap.mcp_server._compile_snippet``'s codegen
        block but skips the pin-wrapping and pin-id collection — the
        Jupyter kernel only needs runtime values, not pin manifests.
        """
        compiler = Compiler(
            module=self.module,
            pre_compiled=self.prelude.compiled,
            pre_class_methods=self.prelude.class_methods,
            pre_class_defaults=self.prelude.class_defaults,
            pre_class_constraints=self.prelude.class_constraints,
            pre_con_info=self.prelude.con_info,
            expr_types=expr_types,
        )
        compiled = compiler.compile(resolved)
        return compiler, compiled

    def _cell_type(self, type_env: dict, fq: str):
        """Extract the cell binding's monotype from its scheme.

        Returns ``None`` if no scheme is recorded — the renderer
        falls back to structural in that case rather than crashing.
        Strips the scheme's universal quantifier to get a monotype
        the type-driven renderer can match against.
        """
        scheme = type_env.get(fq)
        if scheme is None:
            return None
        body = getattr(scheme, 'body', None)
        return body

    def _force(self, val: Any) -> Any:
        """Reduce a PLAN value to head normal form via the BPLAN harness.

        Bumps Python's recursion limit for the duration of the call
        so deeper user-defined recursion gets a reasonable shot
        before tripping ``RecursionError``. The harness's own depth
        guard (``BEVALUATE_DEPTH_LIMIT``, 100K) is the actual upper
        bound; this is just to keep Python's smaller default from
        being the binding constraint first.
        """
        # Each user-level recursion in PLAN unrolls into many Python
        # frames inside bevaluate (the kal walker descends through
        # several layers per reduction). Empirically a tail-recursive
        # factorial uses ~7K Python frames per recursion level — so
        # we bump high enough that ~30 levels of user recursion stay
        # below the ceiling. Beyond that, the BPLAN harness's own
        # depth guard (BEVALUATE_DEPTH_LIMIT, 100K) is the next wall.
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old_limit, 200_000))
        try:
            return bevaluate(val)
        except RecursionError as e:
            raise _CellError(_error_envelope('runtime', e)) from e
        finally:
            sys.setrecursionlimit(old_limit)

    # ------------------------------------------------------------------
    # Result formatting
    # ------------------------------------------------------------------

    def _format_value(self, val: Any, cell_type: Any = None,
                      type_env: dict | None = None,
                      con_info: dict | None = None) -> str:
        """Render a PLAN value as text/plain. (See
        ``_format_value_both`` for the HTML-emitting sibling.)
        Kept for callers that only want the plain rendering."""
        text, _html = self._format_value_both(val, cell_type, type_env, con_info)
        return text

    def _format_value_both(self, val: Any, cell_type: Any,
                           type_env: dict | None,
                           con_info: dict | None) -> tuple[str, str | None]:
        """Render a value as ``(text/plain, text/html)``.

        With type info, both renderings come from
        :mod:`bootstrap.value_render`: constructor names recovered
        from ``con_info``, field types substituted from each
        constructor's scheme, primitives in their canonical literal
        forms. The HTML rendering wraps tokens in inline-styled
        ``<span>`` elements so notebook output gets colour and
        weight cues for constructors / types / numbers / strings.

        Without type info, falls back to a structural render.
        ``value_html`` is ``None`` in that case so the Jupyter
        client just shows the plain text — no point colouring a
        debug-form ``(f arg)``.
        """
        if cell_type is not None and type_env is not None and con_info is not None:
            try:
                text = render_typed(val, cell_type,
                                    type_env=type_env, con_info=con_info)
                html_form = render_typed_html(val, cell_type,
                                              type_env=type_env, con_info=con_info)
                return text, html_form
            except Exception:  # noqa: BLE001
                # Renderer hiccup — fall back to structural plain
                # text rather than letting a display bug eat the
                # cell result.
                pass
        return _render(val, depth=0), None



# ---------------------------------------------------------------------------
# Internal sentinels
# ---------------------------------------------------------------------------

class _NotAnExpression(Exception):
    """The cell doesn't parse as an expression. Try program mode."""


class _CellError(Exception):
    """A pipeline phase raised an error. Carries the structured
    envelope the kernel surfaces to Jupyter."""

    def __init__(self, envelope: dict):
        self.envelope = envelope
        super().__init__(envelope.get('message') or 'cell error')


# ---------------------------------------------------------------------------
# Error helpers — mirror MCP's shape for cross-tool consistency
# ---------------------------------------------------------------------------

def _stage_for(err: Exception) -> str:
    if isinstance(err, ParseError):
        return 'parse'
    if isinstance(err, ScopeError):
        return 'scope'
    if isinstance(err, TypecheckError):
        return 'typecheck'
    if isinstance(err, CodegenError):
        return 'codegen'
    if isinstance(err, RecursionError):
        return 'runtime'
    return 'internal'


def _error_envelope(stage: str, err: Exception) -> dict:
    loc = getattr(err, 'loc', None)
    return {
        'stage': stage,
        'message': str(err),
        'type': type(err).__name__,
        'loc': (
            {'file': loc.file, 'line': loc.line, 'col': loc.col}
            if loc is not None else None
        ),
    }


# ---------------------------------------------------------------------------
# Value rendering (text/plain)
# ---------------------------------------------------------------------------

_RENDER_MAX_DEPTH = 32


def _render(val: Any, depth: int) -> str:
    """Best-effort structural renderer.

    Bounded recursion depth keeps a malformed App tree from blowing
    out the stack while we're trying to *show* an error to the user.
    """
    if depth >= _RENDER_MAX_DEPTH:
        return '...'
    if is_nat(val):
        return str(val)
    if is_pin(val):
        return f'<pin {_render(val.val, depth + 1)}>'
    if is_law(val):
        name_repr = _decode_name_safe(val.name)
        return f'<law arity={val.arity} name={name_repr!r}>'
    if is_app(val):
        return f'({_render(val.fun, depth + 1)} {_render(val.arg, depth + 1)})'
    return repr(val)


def _decode_name_safe(name_nat: Any) -> str:
    """Best-effort: decode a strNat back to its UTF-8 form."""
    if not is_nat(name_nat) or name_nat == 0:
        return ''
    try:
        n = int(name_nat)
        raw = n.to_bytes((n.bit_length() + 7) // 8, 'little')
        return raw.decode('utf-8')
    except (UnicodeDecodeError, OverflowError):
        return f'<{name_nat}>'


# ---------------------------------------------------------------------------
# Jupyter kernel
# ---------------------------------------------------------------------------

# `ipykernel` is imported lazily inside ``_kernel_main`` so importing this
# module for tests doesn't require the Jupyter stack on the path.

def _kernel_main() -> None:
    """Launch the kernel via ``ipykernel.kernelapp.IPKernelApp``.

    Invoked when the user runs ``python -m bootstrap.jupyter_kernel``.
    """
    from ipykernel.kernelapp import IPKernelApp
    from ipykernel.kernelbase import Kernel

    class GallowglassKernel(Kernel):
        implementation = 'Gallowglass'
        implementation_version = '0.1.0'
        language_info = {
            'name': 'gallowglass',
            'mimetype': 'text/x-gallowglass',
            'file_extension': '.gls',
            'pygments_lexer': 'haskell',  # close enough for syntax highlighting
        }
        banner = (
            'Gallowglass kernel (Marduk/PLAN backend). '
            'Declarations accumulate; final expressions render as cell results.'
        )

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._evaluator = GallowglassEvaluator()

        def do_execute(self, code, silent, store_history=True,
                       user_expressions=None, allow_stdin=False, *,
                       cell_id=None):
            try:
                result = self._evaluator.eval_cell(code)
            except Exception as e:  # noqa: BLE001
                # Truly unexpected — surface as a kernel error rather than
                # silently dropping. Anything we can name structurally is
                # caught inside the evaluator.
                tb = traceback.format_exception(type(e), e, e.__traceback__)
                if not silent:
                    self.send_response(self.iopub_socket, 'stream', {
                        'name': 'stderr',
                        'text': ''.join(tb),
                    })
                return {
                    'status': 'error',
                    'execution_count': self.execution_count,
                    'ename': type(e).__name__,
                    'evalue': str(e),
                    'traceback': tb,
                }

            if result.error is not None:
                if not silent:
                    self.send_response(self.iopub_socket, 'stream', {
                        'name': 'stderr',
                        'text': _format_error_for_stream(result.error),
                    })
                return {
                    'status': 'error',
                    'execution_count': self.execution_count,
                    'ename': result.error.get('type', 'Error'),
                    'evalue': result.error.get('message', ''),
                    'traceback': [_format_error_for_stream(result.error)],
                }

            if result.value_text is not None and not silent:
                # Always emit text/plain; emit text/html when the
                # renderer produced one. Jupyter clients prefer the
                # richer MIME type when both are present, falling
                # back to text/plain for terminal/JSON contexts.
                data = {'text/plain': result.value_text}
                if result.value_html is not None:
                    data['text/html'] = result.value_html
                self.send_response(self.iopub_socket, 'execute_result', {
                    'execution_count': self.execution_count,
                    'data': data,
                    'metadata': {},
                })

            return {
                'status': 'ok',
                'execution_count': self.execution_count,
                'payload': [],
                'user_expressions': {},
            }

    IPKernelApp.launch_instance(kernel_class=GallowglassKernel)


def _format_error_for_stream(envelope: dict) -> str:
    """Render an error envelope as a single string suitable for the
    Jupyter ``stream`` channel.

    The shape mirrors the existing diagnostic format:
    ``<file>:<line>:<col>: error: <message>`` with a stage prefix.
    """
    stage = envelope.get('stage', 'error')
    msg = envelope.get('message', '')
    loc = envelope.get('loc')
    if loc is not None:
        prefix = f'{loc["file"]}:{loc["line"]}:{loc["col"]}: '
    else:
        prefix = ''
    return f'{prefix}{stage} error: {msg}\n'


# ---------------------------------------------------------------------------
# Kernelspec installation
# ---------------------------------------------------------------------------

def _install_kernelspec(user: bool = True, prefix: str | None = None) -> str:
    """Register the Gallowglass kernelspec with Jupyter.

    Returns the install path. Uses ``ipykernel``'s ``install`` helper
    in turn, since the kernel.json is tiny and synthesised here.

    The kernelspec bakes the repository root into ``env.PYTHONPATH``
    so the kernel can be launched from any working directory (e.g.
    Jupyter sets the kernel's cwd to the notebook's directory, which
    typically isn't the repo root).
    """
    import json
    import tempfile
    from jupyter_client.kernelspec import KernelSpecManager

    # Repo root = parent of the directory containing this file.
    repo_root = os.path.abspath(os.path.join(
        os.path.dirname(__file__), '..',
    ))

    spec = {
        'argv': [sys.executable, '-m', 'gallowglass_kernel', '-f', '{connection_file}'],
        'display_name': 'Gallowglass',
        'language': 'gallowglass',
        'env': {
            # Without this the kernel dies on launch with
            # `ModuleNotFoundError: No module named 'bootstrap'`
            # when Jupyter spawns it from any cwd that isn't the
            # repo root.
            'PYTHONPATH': repo_root,
        },
        'metadata': {
            'description': 'Gallowglass — LLM-first language compiling to PLAN',
            'gallowglass_repo': repo_root,
        },
    }

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, 'kernel.json')
        with open(path, 'w') as f:
            json.dump(spec, f, indent=2)
        ksm = KernelSpecManager()
        installed = ksm.install_kernel_spec(td, kernel_name='gallowglass',
                                             user=user, prefix=prefix)
    return installed


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def _cli_main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == 'install':
        installed = _install_kernelspec(user=True)
        print(f'Installed Gallowglass kernelspec at {installed}')
        return 0
    # Default: launch the kernel.
    _kernel_main()
    return 0


if __name__ == '__main__':
    sys.exit(_cli_main(sys.argv))
