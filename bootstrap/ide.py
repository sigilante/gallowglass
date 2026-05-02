"""
IDE-facing queries over the bootstrap compiler.

This module is the small surface area an editor / language-server / MCP
adapter consumes. It does no I/O of its own; callers feed source text or an
already-parsed program and pass the per-expression type map produced by
``typecheck_with_types``.

Public API:
    type_at_position(program, expr_types, line, col, filename=None)
        Return the inferred type of the innermost expression at (line, col),
        or None if no expression covers that position.
    type_at_offset(source, module, filename, line, col, ...)
        Convenience: lex/parse/resolve/typecheck a buffer and return the
        type at (line, col), with errors propagated.

Position semantics: ``Loc`` carries only start positions (file, 1-based line,
1-based col), so the "innermost expression at the cursor" is approximated as
the expression whose start ``Loc`` is the latest position ≤ the cursor on
the same file. This matches LSP-style hover well in practice — the cursor
typically lands on or just past an identifier — but gives no guarantee when
the cursor is past the textual end of the deepest expression.
"""

from __future__ import annotations
from typing import Any

from bootstrap import ast


def _walk_exprs(expr, out: list) -> None:
    """Append every Expr node reachable from ``expr`` to ``out``."""
    if expr is None or not hasattr(expr, 'loc'):
        return
    out.append(expr)
    if isinstance(expr, ast.ExprApp):
        _walk_exprs(expr.fun, out)
        _walk_exprs(expr.arg, out)
    elif isinstance(expr, ast.ExprLam):
        _walk_exprs(expr.body, out)
    elif isinstance(expr, ast.ExprLet):
        _walk_exprs(expr.rhs, out)
        _walk_exprs(expr.body, out)
    elif isinstance(expr, ast.ExprPin):
        _walk_exprs(expr.rhs, out)
        if getattr(expr, 'body', None) is not None:
            _walk_exprs(expr.body, out)
    elif isinstance(expr, ast.ExprMatch):
        _walk_exprs(expr.scrutinee, out)
        for _pat, guard, body in expr.arms:
            if guard is not None:
                _walk_exprs(guard, out)
            _walk_exprs(body, out)
    elif isinstance(expr, ast.ExprIf):
        _walk_exprs(expr.cond, out)
        _walk_exprs(expr.then_, out)
        _walk_exprs(expr.else_, out)
    elif isinstance(expr, ast.ExprFix):
        _walk_exprs(expr.lam, out)
    elif isinstance(expr, ast.ExprTuple):
        for e in expr.elems:
            _walk_exprs(e, out)
    elif isinstance(expr, ast.ExprList):
        for e in expr.elems:
            _walk_exprs(e, out)
    elif isinstance(expr, ast.ExprDo):
        _walk_exprs(expr.rhs, out)
        _walk_exprs(expr.body, out)
    elif isinstance(expr, ast.ExprOp):
        _walk_exprs(expr.lhs, out)
        _walk_exprs(expr.rhs, out)
    elif isinstance(expr, ast.ExprWith):
        _walk_exprs(expr.expr, out)
        _walk_exprs(expr.dict_, out)
        for a in expr.extra_args:
            _walk_exprs(a, out)
    elif isinstance(expr, ast.ExprRecord):
        for _, e in expr.fields:
            _walk_exprs(e, out)
    elif isinstance(expr, ast.ExprRecordUpdate):
        _walk_exprs(expr.base, out)
        for _, e in expr.fields:
            _walk_exprs(e, out)
    elif isinstance(expr, ast.ExprHandle):
        _walk_exprs(expr.comp, out)
        for arm in expr.arms:
            if isinstance(arm, (ast.HandlerReturn, ast.HandlerOp)):
                _walk_exprs(arm.body, out)


def _collect_program_exprs(program) -> list:
    out: list = []
    for decl in program.decls:
        if isinstance(decl, ast.DeclLet):
            _walk_exprs(decl.body, out)
    return out


def type_at_position(
    program,
    expr_types: dict[int, Any],
    line: int,
    col: int,
    filename: str | None = None,
) -> Any | None:
    """Return the type of the innermost expression at (line, col), or None.

    ``expr_types`` is the side-table from ``typecheck_with_types``. Locations
    must be 1-based. ``filename``, if given, restricts matches to expressions
    whose ``Loc.file`` matches it.
    """
    best: tuple[int, int, Any] | None = None
    for expr in _collect_program_exprs(program):
        loc = expr.loc
        if filename is not None and loc.file != filename:
            continue
        if loc.line > line or (loc.line == line and loc.col > col):
            continue
        if id(expr) not in expr_types:
            continue
        key = (loc.line, loc.col)
        if best is None or key > (best[0], best[1]):
            best = (loc.line, loc.col, expr)
    if best is None:
        return None
    return expr_types[id(best[2])]


def type_at_offset(
    source: str,
    module: str,
    filename: str,
    line: int,
    col: int,
    prior_type_env: dict | None = None,
    prior_type_constructors: dict | None = None,
) -> Any | None:
    """Convenience entry point: lex/parse/resolve/typecheck ``source`` and
    return the type at (line, col), or None.

    Errors from any pipeline stage propagate; the caller is responsible for
    catching ``ParseError``/``ScopeError``/``TypecheckError`` and rendering
    them. This helper is intentionally narrow — for a real IDE/MCP server
    you will want to drive the pipeline directly so you can recover from
    parse failures (e.g. emit type info for the parts that did parse).
    """
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.typecheck import typecheck_with_types

    prog = parse(lex(source, filename), filename)
    resolved, env = resolve(prog, module, {}, filename)
    _, expr_types = typecheck_with_types(
        resolved, env, module, filename,
        prior_type_env=prior_type_env,
        prior_type_constructors=prior_type_constructors,
    )
    return type_at_position(resolved, expr_types, line, col, filename=filename)
