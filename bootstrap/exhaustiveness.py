"""Pattern match exhaustiveness checker (Maranget usefulness algorithm).

Implements the pattern matrix approach from:
  Maranget, "Warnings for pattern matching," JFP 2007.

Integrated at typecheck time (after type inference, before codegen).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from bootstrap.ast import (
    PatWild, PatVar, PatCon, PatNat, PatText, PatTuple, PatAs,
)


# ------------------------------------------------------------------ #
# Abstract pattern representation                                     #
# ------------------------------------------------------------------ #

@dataclass
class PWild:
    """Wildcard / variable — matches everything."""
    pass

@dataclass
class PCon:
    """Constructor pattern with sub-patterns."""
    name: str          # FQ constructor name
    arity: int         # number of fields
    args: list         # list of abstract patterns (len == arity)

@dataclass
class PLit:
    """Literal pattern (Nat or Text value)."""
    value: Any         # int for Nat, str for Text
    ty: str            # 'Nat' or 'Text'


# ------------------------------------------------------------------ #
# Context for the checker                                             #
# ------------------------------------------------------------------ #

@dataclass
class CheckCtx:
    """Context passed through the usefulness algorithm."""
    type_constructors: dict[str, list[tuple[str, int]]]
    deref: Callable     # TypeChecker.deref
    col_types: list     # MonoType for each column position
    _con_to_type: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not self._con_to_type:
            for ty_name, cons in self.type_constructors.items():
                for con_name, _ in cons:
                    self._con_to_type[con_name] = ty_name

    def type_for_con(self, con_name: str) -> str | None:
        """Reverse lookup: constructor name → type name."""
        return self._con_to_type.get(con_name)


# ------------------------------------------------------------------ #
# AST pattern → abstract pattern conversion                           #
# ------------------------------------------------------------------ #

def pat_to_abstract(pat: Any) -> Any:
    """Convert an AST pattern node to an abstract pattern."""
    if isinstance(pat, (PatWild, PatVar)):
        return PWild()
    if isinstance(pat, PatCon):
        args = [pat_to_abstract(a) for a in pat.args]
        return PCon(str(pat.name), len(pat.args), args)
    if isinstance(pat, PatNat):
        return PLit(pat.value, 'Nat')
    if isinstance(pat, PatText):
        return PLit(pat.value, 'Text')
    if isinstance(pat, PatTuple):
        args = [pat_to_abstract(p) for p in pat.pats]
        return PCon('__Tuple__', len(pat.pats), args)
    if isinstance(pat, PatAs):
        return pat_to_abstract(pat.pat)
    # Fallback: treat unknown patterns as wildcard
    return PWild()


# ------------------------------------------------------------------ #
# Pattern matrix helpers                                              #
# ------------------------------------------------------------------ #

# A matrix row is a list of abstract patterns.
# The matrix is a list of rows.

def _head(row: list) -> Any:
    """First element of a pattern row."""
    return row[0]

def _tail(row: list) -> list:
    """All elements after the first."""
    return row[1:]


def _sigma(matrix: list[list]) -> set[str]:
    """Constructors appearing in the first column.

    Returns set of (name) for PCon, or ('__Lit__:value') for PLit.
    """
    result: set[str] = set()
    for row in matrix:
        p = _head(row)
        if isinstance(p, PCon):
            result.add(p.name)
        elif isinstance(p, PLit):
            result.add(f'__Lit__:{p.value}')
    return result


def _specialize(matrix: list[list], con_name: str, arity: int) -> list[list]:
    """Specialize the matrix by constructor `con_name` of given arity.

    - Rows with matching PCon: replace first col with sub-patterns + rest.
    - Rows with PWild: replace first col with `arity` wildcards + rest.
    - Rows with different PCon or PLit: dropped.
    """
    result: list[list] = []
    for row in matrix:
        p = _head(row)
        rest = _tail(row)
        if isinstance(p, PCon) and p.name == con_name:
            result.append(p.args + rest)
        elif isinstance(p, PWild):
            result.append([PWild()] * arity + rest)
        # Other constructors or literals: skip
    return result


def _specialize_lit(matrix: list[list], value: Any) -> list[list]:
    """Specialize the matrix by a literal value (arity 0)."""
    result: list[list] = []
    for row in matrix:
        p = _head(row)
        rest = _tail(row)
        if isinstance(p, PLit) and p.value == value:
            result.append(rest)
        elif isinstance(p, PWild):
            result.append(rest)
        # Other literals or constructors: skip
    return result


def _default(matrix: list[list]) -> list[list]:
    """Default matrix: keep only wildcard rows, drop the first column."""
    result: list[list] = []
    for row in matrix:
        p = _head(row)
        if isinstance(p, PWild):
            result.append(_tail(row))
    return result


# ------------------------------------------------------------------ #
# Type queries                                                        #
# ------------------------------------------------------------------ #

def _resolve_type_name(ty: Any, ctx: CheckCtx) -> str | None:
    """Extract the type constructor name from a MonoType, or None."""
    from bootstrap.typecheck import TCon, TApp, TMeta, TTup
    ty = ctx.deref(ty)
    if isinstance(ty, TCon):
        return ty.name
    if isinstance(ty, TApp):
        return _resolve_type_name(ty.fun, ctx)
    if isinstance(ty, TTup):
        return f'__Tuple__{len(ty.elems)}'
    return None


def _tuple_arity_from_name(ty_name: str) -> int | None:
    """Extract tuple arity from __Tuple__N name, or None."""
    if ty_name and ty_name.startswith('__Tuple__'):
        try:
            return int(ty_name[9:])
        except ValueError:
            pass
    return None


def _all_constructors(ty_name: str | None, ctx: CheckCtx) -> list[tuple[str, int]] | None:
    """Return all constructors for a type, or None if infinite/unknown.

    Returns list of (con_name, arity) for finite types.
    Returns None for Nat, Text, or unknown types.
    """
    if ty_name is None:
        return None
    if ty_name in ('Nat', 'Text', 'Bytes', 'Int'):
        return None  # infinite
    # Tuples: single constructor with N fields
    arity = _tuple_arity_from_name(ty_name)
    if arity is not None:
        return [('__Tuple__', arity)]
    if ty_name in ctx.type_constructors:
        return ctx.type_constructors[ty_name]
    return None


def _is_sigma_complete(sigma: set[str], ty_name: str | None, ctx: CheckCtx) -> bool:
    """Check if the constructor set sigma covers all constructors of the type."""
    cons = _all_constructors(ty_name, ctx)
    if cons is None:
        return False  # infinite or unknown — never complete without wildcard
    all_names = {name for name, _ in cons}
    sigma_con_names = {s for s in sigma if not s.startswith('__Lit__:')}
    return all_names <= sigma_con_names


# ------------------------------------------------------------------ #
# Usefulness algorithm                                                #
# ------------------------------------------------------------------ #

def _useful(matrix: list[list], q: list, ctx: CheckCtx) -> bool:
    """Is pattern vector q useful w.r.t. the matrix?

    Returns True if there exists a value matched by q that is not
    matched by any row in matrix.
    """
    n_cols = len(q)

    # Base case: zero columns
    if n_cols == 0:
        return len(matrix) == 0  # useful iff no rows left

    p = q[0]
    col_type = ctx.col_types[0] if ctx.col_types else None
    ty_name = _resolve_type_name(col_type, ctx) if col_type is not None else None

    # If type unknown, try to infer from constructors in the column
    if ty_name is None:
        sigma_set = _sigma(matrix)
        for s in sigma_set:
            if not s.startswith('__Lit__:'):
                inferred = ctx.type_for_con(s)
                if inferred:
                    ty_name = inferred
                    break

    if isinstance(p, PCon):
        spec_matrix = _specialize(matrix, p.name, p.arity)
        new_q = p.args + q[1:]
        # Column types: expand sub-pattern types as fresh (unknown)
        new_col_types = [None] * p.arity + ctx.col_types[1:]
        new_ctx = CheckCtx(ctx.type_constructors, ctx.deref, new_col_types)
        return _useful(spec_matrix, new_q, new_ctx)

    if isinstance(p, PLit):
        spec_matrix = _specialize_lit(matrix, p.value)
        new_q = q[1:]
        new_col_types = ctx.col_types[1:]
        new_ctx = CheckCtx(ctx.type_constructors, ctx.deref, new_col_types)
        return _useful(spec_matrix, new_q, new_ctx)

    # p is PWild
    sigma = _sigma(matrix)

    if _is_sigma_complete(sigma, ty_name, ctx):
        # Check usefulness under each constructor specialization
        cons = _all_constructors(ty_name, ctx)
        assert cons is not None
        for con_name, arity in cons:
            spec_matrix = _specialize(matrix, con_name, arity)
            new_q = [PWild()] * arity + q[1:]
            new_col_types = [None] * arity + ctx.col_types[1:]
            new_ctx = CheckCtx(ctx.type_constructors, ctx.deref, new_col_types)
            if _useful(spec_matrix, new_q, new_ctx):
                return True
        return False
    else:
        # Sigma incomplete — check default matrix
        def_matrix = _default(matrix)
        new_q = q[1:]
        new_col_types = ctx.col_types[1:]
        new_ctx = CheckCtx(ctx.type_constructors, ctx.deref, new_col_types)
        return _useful(def_matrix, new_q, new_ctx)


# ------------------------------------------------------------------ #
# Missing pattern computation                                        #
# ------------------------------------------------------------------ #

def _missing_patterns(matrix: list[list], ty_name: str | None, ctx: CheckCtx) -> list[str]:
    """Compute human-readable names of missing patterns."""
    if ty_name is None:
        return ['_']
    cons = _all_constructors(ty_name, ctx)
    if cons is None:
        return ['_ (infinite type)']
    sigma = _sigma(matrix)
    sigma_names = {s for s in sigma if not s.startswith('__Lit__:')}
    missing = []
    for name, arity in cons:
        if name not in sigma_names:
            short = name.split('.')[-1] if '.' in name else name
            if arity == 0:
                missing.append(short)
            else:
                args = ' '.join(['_'] * arity)
                missing.append(f'{short} {args}')
    return missing if missing else ['_']


# ------------------------------------------------------------------ #
# Public API                                                          #
# ------------------------------------------------------------------ #

class ExhaustivenessError(Exception):
    """Raised when a match is not exhaustive."""
    def __init__(self, message: str, loc: Any = None):
        super().__init__(message)
        self.loc = loc


def check_exhaustiveness(
    scrutinee_type: Any,
    arms: list[tuple],
    type_constructors: dict[str, list[tuple[str, int]]],
    deref: Callable,
    loc: Any = None,
) -> list[tuple[int, str]]:
    """Check exhaustiveness of a match expression.

    Args:
        scrutinee_type: The resolved scrutinee MonoType.
        arms: List of (pattern, guard, body) triples.
        type_constructors: FQ type → [(con_name, arity)].
        deref: TypeChecker.deref for chasing TMeta links.
        loc: Source location for error messages.

    Returns:
        List of (arm_index, description) redundancy warnings.

    Raises:
        ExhaustivenessError if the match is not exhaustive.
    """
    # Convert patterns to abstract form
    rows: list[list] = []
    for pat, _guard, _body in arms:
        rows.append([pat_to_abstract(pat)])

    ctx = CheckCtx(type_constructors, deref, [scrutinee_type])

    # --- Exhaustiveness check ---
    # The match is exhaustive iff the wildcard vector [_] is NOT useful
    wildcard_q = [PWild()]
    if _useful(rows, wildcard_q, ctx):
        # Non-exhaustive: compute missing patterns
        ty_name = _resolve_type_name(scrutinee_type, ctx)
        missing = _missing_patterns(rows, ty_name, ctx)
        missing_str = ', '.join(missing)
        raise ExhaustivenessError(
            f"non-exhaustive match: missing pattern(s): {missing_str}",
            loc,
        )

    # --- Redundancy check ---
    warnings: list[tuple[int, str]] = []
    for i in range(len(rows)):
        preceding = rows[:i]
        row_q = rows[i]
        # Reset col_types for each check
        ctx_r = CheckCtx(type_constructors, deref, [scrutinee_type])
        if not _useful(preceding, row_q, ctx_r):
            pat = arms[i][0]
            warnings.append((i, _pat_description(pat)))

    return warnings


def _pat_description(pat: Any) -> str:
    """Short human-readable description of a pattern for warnings."""
    if isinstance(pat, PatWild):
        return '_'
    if isinstance(pat, PatVar):
        return pat.name
    if isinstance(pat, PatCon):
        name = str(pat.name).split('.')[-1]
        if pat.args:
            args = ' '.join(_pat_description(a) for a in pat.args)
            return f'{name} {args}'
        return name
    if isinstance(pat, PatNat):
        return str(pat.value)
    if isinstance(pat, PatText):
        return f'"{pat.value}"'
    if isinstance(pat, PatTuple):
        inner = ', '.join(_pat_description(p) for p in pat.pats)
        return f'({inner})'
    if isinstance(pat, PatAs):
        return _pat_description(pat.pat)
    return '_'
