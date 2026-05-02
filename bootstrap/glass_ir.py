"""
Gallowglass Glass IR renderer — SPEC-CONFORMING AST RENDERER.

This module renders resolved AST declarations as Glass IR fragments
per `spec/01-glass-ir.md`: FQ names, pin hashes, explicit
dictionaries, round-trippable back to PLAN.

The raw-PLAN-value debug dump that used to live alongside the spec
renderer was moved to `bootstrap/glass_ir_debug.py` in AUDIT.md C5
to honour the gospel principle that `Show` (spec) and `Debug`
(developer) representations stay distinct.  If you want a textual
dump of compiled PLAN values for tracing the codegen, import from
`glass_ir_debug` instead.

Public API:
    render_fragment(decl, pin_id, ...) -> str        # Glass IR fragment
    render_decl(decl, module, ...) -> str
    render_module(resolved, manifest, ...) -> str    # Full module
    render_expr(expr, ...) -> str
    render_pattern(pat) -> str
    collect_decl_deps / collect_pin_deps / verify_roundtrip — analysis
"""

from __future__ import annotations
from bootstrap import ast


# ===================================================================
# AST-based Glass IR fragment rendering (M17)
# ===================================================================

def _qual_name(qn: ast.QualName) -> str:
    """Render a QualName as a dotted FQ string."""
    return '.'.join(qn.parts)


# ---------------------------------------------------------------------------
# Expression rendering
# ---------------------------------------------------------------------------

def render_expr(expr, indent: int = 0) -> str:
    """Render a resolved AST expression as Glass IR source text."""
    if isinstance(expr, ast.ExprNat):
        return str(expr.value)

    if isinstance(expr, ast.ExprText):
        if isinstance(expr.value, str):
            return f'"{expr.value}"'
        # Interpolation fragments
        parts = []
        for frag in expr.value:
            if isinstance(frag, str):
                parts.append(frag)
            else:
                parts.append(f'#{{{frag[1]}}}')
        return f'"{"".join(parts)}"'

    if isinstance(expr, ast.ExprVar):
        return _qual_name(expr.name)

    if isinstance(expr, ast.ExprApp):
        # Collect application spine
        spine = []
        cur = expr
        while isinstance(cur, ast.ExprApp):
            spine.append(cur.arg)
            cur = cur.fun
        spine.append(cur)
        spine.reverse()
        parts = [_wrap_atom(render_expr(e, indent)) for e in spine]
        return ' '.join(parts)

    if isinstance(expr, ast.ExprLam):
        params = ' '.join(render_pattern(p) for p in expr.params)
        body = render_expr(expr.body, indent)
        return f'\u03bb {params} \u2192 {body}'

    if isinstance(expr, ast.ExprLet):
        pat = render_pattern(expr.pattern)
        rhs = render_expr(expr.rhs, indent + 1)
        body = render_expr(expr.body, indent)
        pad = '  ' * indent
        return f'let {pat} = {rhs} in\n{pad}{body}'

    if isinstance(expr, ast.ExprMatch):
        scrut = render_expr(expr.scrutinee, indent)
        pad = '  ' * (indent + 1)
        arms = []
        for pat, guard, body in expr.arms:
            arm_str = f'{pad}| {render_pattern(pat)}'
            if guard is not None:
                arm_str += f' if {render_expr(guard, indent + 1)}'
            arm_str += f' \u2192 {render_expr(body, indent + 1)}'
            arms.append(arm_str)
        return f'match {scrut} {{\n' + '\n'.join(arms) + '\n' + '  ' * indent + '}'

    if isinstance(expr, ast.ExprIf):
        c = render_expr(expr.cond, indent)
        t = render_expr(expr.then_, indent)
        e = render_expr(expr.else_, indent)
        return f'if {c} then {t} else {e}'

    if isinstance(expr, ast.ExprFix):
        return f'fix {render_expr(expr.lam, indent)}'

    if isinstance(expr, ast.ExprTuple):
        elems = ', '.join(render_expr(e, indent) for e in expr.elems)
        return f'({elems})'

    if isinstance(expr, ast.ExprList):
        elems = ', '.join(render_expr(e, indent) for e in expr.elems)
        return f'[{elems}]'

    if isinstance(expr, ast.ExprPin):
        rhs = render_expr(expr.rhs, indent)
        body = render_expr(expr.body, indent) if expr.body else ''
        result = f'@{expr.name} = {rhs}'
        if body:
            result += f'\n{"  " * indent}{body}'
        return result

    if isinstance(expr, ast.ExprDo):
        rhs = render_expr(expr.rhs, indent)
        body = render_expr(expr.body, indent)
        return f'{expr.name} \u2190 {rhs}\n{"  " * indent}{body}'

    if isinstance(expr, ast.ExprOp):
        lhs = render_expr(expr.lhs, indent)
        rhs = render_expr(expr.rhs, indent)
        return f'{lhs} {expr.op} {rhs}'

    if isinstance(expr, ast.ExprWith):
        e = render_expr(expr.expr, indent)
        d = render_expr(expr.dict_, indent)
        extras = ' '.join(render_expr(a, indent) for a in expr.extra_args)
        result = f'{e} [{d}]'
        if extras:
            result += f' {extras}'
        return result

    if isinstance(expr, ast.ExprRecord):
        fields = ', '.join(f'{n} = {render_expr(v, indent)}'
                           for n, v in expr.fields)
        return f'{{ {fields} }}'

    if isinstance(expr, ast.ExprRecordUpdate):
        base = render_expr(expr.base, indent)
        fields = ', '.join(f'{n} = {render_expr(v, indent)}'
                           for n, v in expr.fields)
        return f'{base} {{ {fields} }}'

    if isinstance(expr, ast.ExprHandle):
        comp = render_expr(expr.comp, indent)
        pad = '  ' * (indent + 1)
        arms = []
        for arm in expr.arms:
            arms.append(f'{pad}| {render_handler_arm(arm, indent + 1)}')
        return f'handle {comp} {{\n' + '\n'.join(arms) + '\n' + '  ' * indent + '}'

    # Fallback
    return f'<expr:{type(expr).__name__}>'


def _wrap_atom(s: str) -> str:
    """Wrap non-atomic expressions in parens for application spine."""
    if ' ' in s and not s.startswith('(') and not s.startswith('[') \
            and not s.startswith('{') and not s.startswith('"'):
        return f'({s})'
    return s


def render_handler_arm(arm, indent: int) -> str:
    """Render a handler arm."""
    if isinstance(arm, ast.HandlerReturn):
        pat = render_pattern(arm.pattern)
        return f'return {pat} \u2192 {render_expr(arm.body, indent)}'
    if isinstance(arm, ast.HandlerOp):
        args = ' '.join(render_pattern(p) for p in arm.arg_pats)
        once = 'once ' if arm.once else ''
        return f'{once}{arm.op_name} {args} {arm.resume} \u2192 {render_expr(arm.body, indent)}'
    return f'<arm:{type(arm).__name__}>'


# ---------------------------------------------------------------------------
# Pattern rendering
# ---------------------------------------------------------------------------

def render_pattern(pat) -> str:
    """Render a resolved AST pattern."""
    if isinstance(pat, ast.PatWild):
        return '_'
    if isinstance(pat, ast.PatVar):
        return pat.name
    if isinstance(pat, ast.PatNat):
        return str(pat.value)
    if isinstance(pat, ast.PatText):
        return f'"{pat.value}"'
    if isinstance(pat, ast.PatCon):
        name = _qual_name(pat.name)
        if not pat.args:
            return name
        args = ' '.join(_wrap_atom(render_pattern(a)) for a in pat.args)
        return f'{name} {args}'
    if isinstance(pat, ast.PatTuple):
        elems = ', '.join(render_pattern(p) for p in pat.pats)
        return f'({elems})'
    if isinstance(pat, ast.PatCons):
        h = render_pattern(pat.head)
        t = render_pattern(pat.tail)
        return f'{h} :: {t}'
    return f'<pat:{type(pat).__name__}>'


# ---------------------------------------------------------------------------
# Declaration rendering
# ---------------------------------------------------------------------------

def _check_type_env_module(type_env: dict | None, module: str) -> None:
    """Guard against the silent footgun where typecheck() was called with one
    module name and a renderer with another. The TypeEnv keys are FQ names
    ``Module.decl``; if none of them share the renderer's module prefix, the
    annotations would silently disappear. Raise instead.
    """
    if not type_env:
        return
    prefix = f'{module}.'
    if any(k.startswith(prefix) for k in type_env):
        return
    # No keys for this module. Find the first qualified key (skip the bare
    # built-ins True/False/Unit etc.) to give a useful diagnostic.
    observed_prefixes = sorted({
        k.rsplit('.', 1)[0] for k in type_env if '.' in k
    })
    if observed_prefixes:
        hint = "modules in type_env: " + ', '.join(
            f"'{p}'" for p in observed_prefixes)
    else:
        hint = "type_env has no qualified keys"
    raise ValueError(
        f"render: type_env has no keys for module '{module}' ({hint}). "
        f"Pass the same module name to typecheck() and the renderer."
    )


def render_decl(decl, module: str, pin_ids: dict | None = None,
                type_env: dict | None = None) -> str:
    """Render a resolved AST declaration as Glass IR.

    Args:
        decl: Resolved AST declaration (DeclLet, DeclType, etc.)
        module: Module name for FQ naming
        pin_ids: Optional dict of fq_name -> pin_id hex string
        type_env: Optional dict of fq_name -> Scheme for type annotations
    """
    pin_ids = pin_ids or {}

    if isinstance(decl, ast.DeclLet):
        fq = f'{module}.{decl.name}'
        pin_ann = f' [pin#{pin_ids[fq][:8]}]' if fq in pin_ids else ''
        type_ann = ''
        if type_env and fq in type_env:
            from bootstrap.typecheck import pp_scheme
            type_ann = f' : {pp_scheme(type_env[fq])}'
        body = render_expr(decl.body, 1)
        return f'let {fq}{pin_ann}{type_ann}\n  = {body}'

    if isinstance(decl, ast.DeclType):
        fq = f'{module}.{decl.name}'
        params = ' '.join(decl.params) if decl.params else ''
        param_str = f' {params}' if params else ''
        ctors = []
        for ctor in decl.constructors:
            name = f'{module}.{ctor.name}'
            if ctor.arg_types:
                args = ' '.join(str(f) for f in ctor.arg_types)
                ctors.append(f'  | {name} {args}')
            else:
                ctors.append(f'  | {name}')
        body = '\n'.join(ctors) if ctors else ''
        return f'type {fq}{param_str} =\n{body}'

    if isinstance(decl, ast.DeclClass):
        fq = f'{module}.{decl.name}'
        params = ' '.join(decl.params)
        members = []
        for m in decl.members:
            if isinstance(m, ast.ClassMember):
                members.append(f'  {m.name}')
            elif isinstance(m, ast.ClassLaw):
                members.append(f'  {m.name} (default)')
        return f'class {fq} {params} {{\n' + '\n'.join(members) + '\n}'

    if isinstance(decl, ast.DeclInst):
        class_name = '.'.join(decl.class_name.parts) if isinstance(decl.class_name, ast.QualName) else str(decl.class_name)
        type_args = ' '.join(str(a) for a in decl.type_args)
        return f'instance {class_name} {type_args}'

    if isinstance(decl, ast.DeclUse):
        # Not valid in Glass IR — note it as a comment
        mod = '.'.join(decl.module_path)
        return f'-- (use {mod} elided — Glass IR uses FQ names)'

    if isinstance(decl, ast.DeclTypeAlias):
        return f'-- (type alias {decl.name} elided)'

    # Fallback
    return f'-- <decl:{type(decl).__name__}>'


# ---------------------------------------------------------------------------
# Fragment rendering
# ---------------------------------------------------------------------------

def render_fragment(
    fq_name: str,
    decl,
    pin_id: str | None = None,
    module: str = '',
    deps: dict | None = None,
    budget: int = 4096,
    type_env: dict | None = None,
) -> str:
    """Render a single definition as a Glass IR fragment.

    Args:
        fq_name: Fully-qualified name of the definition
        decl: Resolved AST declaration
        pin_id: PinId hex string for this definition (optional)
        module: Module name
        deps: Dict of fq_name -> pin_id for dependencies (optional)
        budget: Token budget for the fragment header
        type_env: Optional dict of fq_name -> Scheme for type annotations

    Returns:
        Glass IR fragment text.
    """
    if module:
        _check_type_env_module(type_env, module)
    lines = []

    # Metadata header
    snapshot_hash = pin_id[:8] if pin_id else '00000000'
    lines.append(f'-- Snapshot: pin#{snapshot_hash}')
    lines.append(f'-- Source: {fq_name}')
    lines.append(f'-- Budget: {budget} tokens')
    lines.append('')

    # Pin declarations for dependencies
    if deps:
        for dep_fq, dep_pin_id in sorted(deps.items()):
            lines.append(f'@![pin#{dep_pin_id[:8]}] {dep_fq}')
        lines.append('')

    # Body declaration
    pin_ids = {fq_name: pin_id} if pin_id else {}
    lines.append(render_decl(decl, module, pin_ids, type_env=type_env))

    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# Dependency collection
# ---------------------------------------------------------------------------

def collect_expr_refs(expr, refs: set) -> None:
    """Walk an expression AST and collect all FQ name references."""
    if isinstance(expr, ast.ExprVar):
        refs.add(_qual_name(expr.name))
    elif isinstance(expr, ast.ExprApp):
        collect_expr_refs(expr.fun, refs)
        collect_expr_refs(expr.arg, refs)
    elif isinstance(expr, ast.ExprLam):
        collect_expr_refs(expr.body, refs)
    elif isinstance(expr, ast.ExprLet):
        collect_expr_refs(expr.rhs, refs)
        collect_expr_refs(expr.body, refs)
    elif isinstance(expr, ast.ExprMatch):
        collect_expr_refs(expr.scrutinee, refs)
        for pat, guard, body in expr.arms:
            _collect_pat_refs(pat, refs)
            if guard is not None:
                collect_expr_refs(guard, refs)
            collect_expr_refs(body, refs)
    elif isinstance(expr, ast.ExprIf):
        collect_expr_refs(expr.cond, refs)
        collect_expr_refs(expr.then_, refs)
        collect_expr_refs(expr.else_, refs)
    elif isinstance(expr, ast.ExprFix):
        collect_expr_refs(expr.lam, refs)
    elif isinstance(expr, ast.ExprTuple):
        for e in expr.elems:
            collect_expr_refs(e, refs)
    elif isinstance(expr, ast.ExprList):
        for e in expr.elems:
            collect_expr_refs(e, refs)
    elif isinstance(expr, ast.ExprPin):
        collect_expr_refs(expr.rhs, refs)
        if expr.body:
            collect_expr_refs(expr.body, refs)
    elif isinstance(expr, ast.ExprDo):
        collect_expr_refs(expr.rhs, refs)
        collect_expr_refs(expr.body, refs)
    elif isinstance(expr, ast.ExprOp):
        collect_expr_refs(expr.lhs, refs)
        collect_expr_refs(expr.rhs, refs)
    elif isinstance(expr, ast.ExprWith):
        collect_expr_refs(expr.expr, refs)
        collect_expr_refs(expr.dict_, refs)
        for a in expr.extra_args:
            collect_expr_refs(a, refs)
    elif isinstance(expr, ast.ExprRecord):
        for _, e in expr.fields:
            collect_expr_refs(e, refs)
    elif isinstance(expr, ast.ExprRecordUpdate):
        collect_expr_refs(expr.base, refs)
        for _, e in expr.fields:
            collect_expr_refs(e, refs)
    elif isinstance(expr, ast.ExprHandle):
        collect_expr_refs(expr.comp, refs)
        for arm in expr.arms:
            if isinstance(arm, (ast.HandlerReturn, ast.HandlerOp)):
                collect_expr_refs(arm.body, refs)


def _collect_pat_refs(pat, refs: set) -> None:
    """Collect FQ constructor references from patterns."""
    if isinstance(pat, ast.PatCon):
        refs.add(_qual_name(pat.name))
        for a in pat.args:
            _collect_pat_refs(a, refs)
    elif isinstance(pat, ast.PatTuple):
        for p in pat.pats:
            _collect_pat_refs(p, refs)
    elif isinstance(pat, ast.PatCons):
        _collect_pat_refs(pat.head, refs)
        _collect_pat_refs(pat.tail, refs)


def collect_decl_deps(decl, module: str) -> set[str]:
    """Collect all FQ names referenced by a declaration that are outside the module."""
    refs = set()
    if isinstance(decl, ast.DeclLet):
        collect_expr_refs(decl.body, refs)
    elif isinstance(decl, ast.DeclInst):
        for m in decl.members:
            if hasattr(m, 'body') and m.body is not None:
                collect_expr_refs(m.body, refs)
    prefix = module + '.'
    return {r for r in refs if not r.startswith(prefix)}


def collect_pin_deps(
    fq_name: str,
    decl,
    module: str,
    manifest: dict,
) -> dict[str, str]:
    """Find cross-module dependencies and their PinIds.

    Args:
        fq_name: FQ name of the definition
        decl: Resolved AST declaration
        module: Module name
        manifest: Dict with 'pins' key mapping FQ names to PinId hex

    Returns:
        Dict of dep_fq_name -> pin_id for all cross-module references.
    """
    pin_map = manifest.get('pins', {}) if manifest else {}
    ext_refs = collect_decl_deps(decl, module)
    return {ref: pin_map[ref] for ref in sorted(ext_refs) if ref in pin_map}


def render_scc_group(
    scc_names: list[str],
    decls: list,
    group_pin_id: str | None,
    module: str,
    pin_ids: dict | None = None,
) -> str:
    """Render a mutually recursive SCC group as a GroupedPin block.

    Args:
        scc_names: FQ names of the group members (canonical order)
        decls: Resolved AST declarations for each member
        group_pin_id: PinId of the shared SCC pin (optional)
        module: Module name
        pin_ids: Optional dict of fq_name -> pin_id for individual members

    Returns:
        Glass IR GroupedPin block text.
    """
    pin_ids = pin_ids or {}
    group_hash = group_pin_id[:8] if group_pin_id else '00000000'
    lines = [f'@![pin#{group_hash}] {{']
    for name, decl in zip(scc_names, decls):
        rendered = render_decl(decl, module, pin_ids)
        # Indent each line of the rendered declaration
        for line in rendered.split('\n'):
            lines.append(f'  {line}')
        lines.append('')
    lines.append('}')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Round-trip verification
# ---------------------------------------------------------------------------

def verify_roundtrip(
    resolved_program,
    compiled: dict,
    module: str,
) -> list[tuple[bool, str]]:
    """Verify the round-trip property for all let declarations in a program.

    Checks that the resolved AST (from which Glass IR is rendered)
    compiles to the same PLAN output as the original compilation.

    This is the bootstrap-level round-trip: AST → compile → compare.
    Full Glass IR round-trip (render → parse → compile → compare)
    requires the self-hosting compiler's Glass IR parser.

    Args:
        resolved_program: The resolved Program AST
        compiled: Dict of fq_name -> PLAN value from original compilation
        module: Module name

    Returns:
        List of (success: bool, message: str) pairs.
    """
    from bootstrap.codegen import compile_program

    try:
        recompiled = compile_program(resolved_program, module)
    except Exception as e:
        return [(False, f'{module}: recompilation failed: {e}')]

    results = []
    for decl in resolved_program.decls:
        if isinstance(decl, ast.DeclLet):
            fq = f'{module}.{decl.name}'
            if fq not in compiled:
                continue
            if fq not in recompiled:
                results.append((False, f'{fq}: not in recompiled output'))
                continue
            if recompiled[fq] == compiled[fq]:
                results.append((True, f'{fq}: round-trip OK'))
            else:
                results.append((False, (
                    f'{fq}: PLAN mismatch\n'
                    f'  original:   {compiled[fq]}\n'
                    f'  recompiled: {recompiled[fq]}'
                )))
    return results


def render_module(
    resolved,
    module: str,
    manifest: dict | None = None,
    type_env: dict | None = None,
) -> str:
    """Render all declarations in a resolved program as Glass IR.

    Args:
        resolved: Resolved Program AST
        module: Module name
        manifest: Optional manifest dict with 'pins' key
        type_env: Optional dict of fq_name -> Scheme for type annotations

    Returns:
        Glass IR text for the entire module.
    """
    _check_type_env_module(type_env, module)
    pin_ids = manifest.get('pins', {}) if manifest else {}
    parts = []
    for decl in resolved.decls:
        rendered = render_decl(decl, module, pin_ids, type_env=type_env)
        parts.append(rendered)
    return '\n\n'.join(parts) + '\n'
