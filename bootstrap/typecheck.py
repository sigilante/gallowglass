"""
Gallowglass bootstrap type checker.

Implements Algorithm W (Hindley-Milner) with:
  - Unification with occurs check
  - Let-generalization at top-level declarations
  - Monomorphic local let bindings
  - Single-parameter typeclass instance existence checking
  - Effect rows parsed but completely ignored

Public API:
    typecheck(program, scope_env, module, filename) -> TypeEnv
    TypecheckError(msg, loc)
    TypeEnv = dict[str, Scheme]   # fq_name → Scheme

Reference: bootstrap/src/typecheck.sire, bootstrap/BOOTSTRAP.md §3.2
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from bootstrap.lexer import Loc
from bootstrap.ast import (
    Program, QualName,
    TyVar, TyCon as AstTyCon, TyApp as AstTyApp, TyArr as AstTyArr,
    TyForall, TyEffect, TyTuple as AstTyTuple, TyRecord, TyUnit, TyBottom,
    TyEmpty, TyConstrained, TyRefined,
    PatWild, PatVar, PatCon, PatNat, PatText, PatTuple, PatList,
    PatCons, PatAs, PatOr,
    ExprVar, ExprApp, ExprLam, ExprLet, ExprMatch, ExprHandle,
    HandlerReturn, HandlerOp,
    ExprIf, ExprTuple, ExprList, ExprNat, ExprText, ExprRawText,
    ExprBytes, ExprHexBytes, ExprUnit, ExprPin, ExprDo, ExprFix,
    ExprOp, ExprUnary, ExprAnn, ExprWith, ExprRecord, ExprRecordUpdate,
    DeclLet, Constructor, DeclType, DeclTypeAlias, DeclTypeBuiltin,
    DeclRecord, DeclEff, DeclClass, DeclInst, DeclExt, DeclUse, DeclMod,
    ClassMember, InstanceMember, ExtItem,
)
from bootstrap.scope import (
    Env, BindingValue, BindingCon, BindingType, BindingClass,
    BindingClassMethod, BindingExtValue, BindingExtType,
)


# ---------------------------------------------------------------------------
# Internal monotype representation
# ---------------------------------------------------------------------------

@dataclass
class TMeta:
    """Unification variable (mutable cell)."""
    id: int
    ref: Any = field(default=None, compare=False, repr=False)  # MonoType | None


@dataclass
class TCon:
    """Type constructor, e.g. 'Nat', 'Bool', 'Test.Option'."""
    name: str


@dataclass
class TArr:
    """Function type: dom → cod."""
    dom: Any  # MonoType
    cod: Any  # MonoType


@dataclass
class TApp:
    """Type application: F a."""
    fun: Any  # MonoType
    arg: Any  # MonoType


@dataclass
class TTup:
    """Tuple type: (a, b, c). len(elems) >= 2."""
    elems: list  # list[MonoType]


@dataclass
class TRow:
    """Effect row: flat dict of named effects plus an optional open tail.

    effects: dict[str, list[MonoType]]  — effect_name → list of type args
             e.g. {'IO': [], 'Exn': [io_error_ty], 'State': [int_ty]}
    tail: TMeta | None  — open row variable; None = closed row

    The tail variable is a unification meta that ranges over "whatever other
    effects the caller operates in." Row unification distributes excess effects
    into each side's tail (see spec/05-type-system.md §4.3).
    """
    effects: dict  # str → list[MonoType]
    tail: Any      # TMeta | None


@dataclass
class TComp:
    """Computation type: {row} T — a value of type T with an effect row.

    Appears as the return type of effectful functions and effect operations.
    Eliminated by `handle` expressions (which remove one effect from the row).
    """
    row: Any  # TRow
    ty: Any   # MonoType (value type)


@dataclass
class TBound:
    """Scheme-bound variable (only appears inside Scheme.body)."""
    name: str


# Scheme: ∀ vars. constraints ⇒ body
@dataclass
class Scheme:
    vars: list[str]
    body: Any  # MonoType
    constraints: list[tuple[str, list]] = field(default_factory=list)
    # Each constraint is (class_name, [MonoType args])


# TypeEnv: maps fq_name -> Scheme
TypeEnv = dict[str, Scheme]


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class TypecheckError(Exception):
    def __init__(self, msg: str, loc: Loc):
        super().__init__(f"{loc.file}:{loc.line}:{loc.col}: type error: {msg}")
        self.loc = loc


# ---------------------------------------------------------------------------
# Helper: pretty-print a monotype (for error messages)
# ---------------------------------------------------------------------------

def _pp(ty: Any, checker: 'TypeChecker') -> str:
    ty = checker.deref(ty)
    if isinstance(ty, TMeta):
        return f"?{ty.id}"
    elif isinstance(ty, TCon):
        return ty.name
    elif isinstance(ty, TBound):
        return ty.name
    elif isinstance(ty, TArr):
        d = _pp(ty.dom, checker)
        c = _pp(ty.cod, checker)
        # Parenthesise left-hand side if it's a function
        dom_pp = f"({d})" if isinstance(checker.deref(ty.dom), TArr) else d
        return f"{dom_pp} → {c}"
    elif isinstance(ty, TApp):
        f_ = _pp(ty.fun, checker)
        a_ = _pp(ty.arg, checker)
        needs_parens = isinstance(checker.deref(ty.arg), (TApp, TArr))
        a_pp = f"({a_})" if needs_parens else a_
        return f"{f_} {a_pp}"
    elif isinstance(ty, TTup):
        return "(" + ", ".join(_pp(e, checker) for e in ty.elems) + ")"
    elif isinstance(ty, TRow):
        parts = []
        for name in sorted(ty.effects.keys()):
            args = ty.effects[name]
            if args:
                parts.append(name + ' ' + ' '.join(_pp(a, checker) for a in args))
            else:
                parts.append(name)
        inner = ', '.join(parts) if parts else '∅'
        if ty.tail is not None:
            inner += ' | ' + _pp(ty.tail, checker)
        return '{' + inner + '}'
    elif isinstance(ty, TComp):
        return _pp(ty.row, checker) + ' ' + _pp(ty.ty, checker)
    return repr(ty)


# ---------------------------------------------------------------------------
# Standalone type pretty-printing (no TypeChecker needed)
# ---------------------------------------------------------------------------
# Post-generalization types contain no TMeta, so these work on resolved types.

def pp_type(ty: Any) -> str:
    """Pretty-print a MonoType without a TypeChecker instance."""
    if isinstance(ty, TCon):
        return ty.name
    elif isinstance(ty, TBound):
        return ty.name
    elif isinstance(ty, TArr):
        d = pp_type(ty.dom)
        c = pp_type(ty.cod)
        dom_pp = f"({d})" if isinstance(ty.dom, TArr) else d
        return f"{dom_pp} → {c}"
    elif isinstance(ty, TApp):
        f_ = pp_type(ty.fun)
        a_ = pp_type(ty.arg)
        needs_parens = isinstance(ty.arg, (TApp, TArr))
        a_pp = f"({a_})" if needs_parens else a_
        return f"{f_} {a_pp}"
    elif isinstance(ty, TTup):
        return "(" + ", ".join(pp_type(e) for e in ty.elems) + ")"
    elif isinstance(ty, TRow):
        parts = []
        for name in sorted(ty.effects.keys()):
            args = ty.effects[name]
            if args:
                parts.append(name + ' ' + ' '.join(pp_type(a) for a in args))
            else:
                parts.append(name)
        inner = ', '.join(parts) if parts else '∅'
        if ty.tail is not None:
            inner += ' | ' + pp_type(ty.tail)
        return '{' + inner + '}'
    elif isinstance(ty, TComp):
        return pp_type(ty.row) + ' ' + pp_type(ty.ty)
    elif isinstance(ty, TMeta):
        return f"?{ty.id}"
    return repr(ty)


def pp_scheme(scheme: Scheme) -> str:
    """Pretty-print a Scheme with ∀ quantifier and constraints."""
    body = pp_type(scheme.body)
    constraint_str = ''
    if scheme.constraints:
        parts = []
        for class_name, type_args in scheme.constraints:
            args = ' '.join(pp_type(a) for a in type_args)
            parts.append(f'{class_name} {args}' if args else class_name)
        if len(parts) == 1:
            constraint_str = f'{parts[0]} ⇒ '
        else:
            constraint_str = '(' + ', '.join(parts) + ') ⇒ '
    if scheme.vars:
        vs = ' '.join(scheme.vars)
        return f"∀ {vs}. {constraint_str}{body}"
    if constraint_str:
        return f"{constraint_str}{body}"
    return body


# ---------------------------------------------------------------------------
# TypeChecker
# ---------------------------------------------------------------------------

class TypeChecker:
    def __init__(self, module: str, filename: str):
        self.module = module
        self.filename = filename
        self._counter = 0
        # Global type environment: fq_name -> Scheme
        self.type_env: TypeEnv = {}
        # Class method signatures: class_fq -> {method_name: Scheme}
        self.class_methods: dict[str, dict[str, Scheme]] = {}
        # Instance database: class_fq -> set of concrete fq type names
        self.instance_db: dict[str, set[str]] = {}
        # Type arity: type_fq -> number of params
        self.type_arity: dict[str, int] = {}
        # Type constructors: type_fq -> [(con_fq, arity)]
        self.type_constructors: dict[str, list[tuple[str, int]]] = {}
        # Per-subexpression type recording for IDE queries. When this is a
        # dict, infer() stores id(expr) -> Type for every visited expression
        # node. Final types must be _zonk'd at query time. Off (None) by
        # default — the recording adds overhead and is only useful for
        # tooling that wants type-at-position lookups.
        self.expr_types: dict[int, Any] | None = None
        self._init_builtins()

    # ------------------------------------------------------------------ #
    # Meta variables                                                       #
    # ------------------------------------------------------------------ #

    def fresh(self) -> TMeta:
        m = TMeta(self._counter)
        self._counter += 1
        return m

    def fresh_row(self) -> TRow:
        """Create an open row with no explicit effects and a fresh tail variable."""
        return TRow({}, self.fresh())

    def deref(self, ty: Any) -> Any:
        """Chase TMeta links (no path compression for simplicity)."""
        while isinstance(ty, TMeta) and ty.ref is not None:
            ty = ty.ref
        return ty

    # ------------------------------------------------------------------ #
    # Occurs check and unification                                         #
    # ------------------------------------------------------------------ #

    def _occurs(self, meta: TMeta, ty: Any) -> bool:
        ty = self.deref(ty)
        if isinstance(ty, TMeta):
            return ty.id == meta.id
        elif isinstance(ty, (TCon, TBound)):
            return False
        elif isinstance(ty, TArr):
            return self._occurs(meta, ty.dom) or self._occurs(meta, ty.cod)
        elif isinstance(ty, TApp):
            return self._occurs(meta, ty.fun) or self._occurs(meta, ty.arg)
        elif isinstance(ty, TTup):
            return any(self._occurs(meta, e) for e in ty.elems)
        elif isinstance(ty, TRow):
            if any(self._occurs(meta, a) for args in ty.effects.values() for a in args):
                return True
            return ty.tail is not None and self._occurs(meta, ty.tail)
        elif isinstance(ty, TComp):
            return self._occurs(meta, ty.row) or self._occurs(meta, ty.ty)
        return False

    def _flatten_row(self, row: Any) -> tuple:
        """Chase tail pointers and return (effects_dict, tail_meta_or_None).

        Merges chained TRow values into a single flat dict.  The returned
        tail is either None (closed) or a TMeta that hasn't been resolved yet.
        """
        row = self.deref(row)
        if isinstance(row, TMeta):
            return {}, row      # unknown open row
        if not isinstance(row, TRow):
            return {}, None     # shouldn't happen; treat as closed
        effects: dict = dict(row.effects)
        tail = row.tail
        while tail is not None:
            d = self.deref(tail)
            if isinstance(d, TMeta):
                tail = d
                break
            if not isinstance(d, TRow):
                tail = None
                break
            # Merge effects (first-seen wins; duplicates unified at the call site)
            for k, v in d.effects.items():
                if k not in effects:
                    effects[k] = v
            tail = d.tail
        return effects, tail

    def unify(self, t1: Any, t2: Any, loc: Loc) -> None:
        t1 = self.deref(t1)
        t2 = self.deref(t2)

        if isinstance(t1, TMeta):
            if isinstance(t2, TMeta) and t1.id == t2.id:
                return
            if self._occurs(t1, t2):
                raise TypecheckError(
                    f"infinite type: ?{t1.id} occurs in {_pp(t2, self)}", loc)
            t1.ref = t2
            return

        if isinstance(t2, TMeta):
            self.unify(t2, t1, loc)
            return

        if isinstance(t1, TCon) and isinstance(t2, TCon):
            if t1.name != t2.name:
                raise TypecheckError(
                    f"cannot unify '{t1.name}' with '{t2.name}'", loc)
            return

        if isinstance(t1, TArr) and isinstance(t2, TArr):
            self.unify(t1.dom, t2.dom, loc)
            self.unify(t1.cod, t2.cod, loc)
            return

        if isinstance(t1, TApp) and isinstance(t2, TApp):
            self.unify(t1.fun, t2.fun, loc)
            self.unify(t1.arg, t2.arg, loc)
            return

        if isinstance(t1, TTup) and isinstance(t2, TTup):
            if len(t1.elems) != len(t2.elems):
                raise TypecheckError(
                    f"tuple arity mismatch: {len(t1.elems)} vs {len(t2.elems)}", loc)
            for e1, e2 in zip(t1.elems, t2.elems):
                self.unify(e1, e2, loc)
            return

        if isinstance(t1, TBound) and isinstance(t2, TBound):
            if t1.name != t2.name:
                raise TypecheckError(
                    f"cannot unify bound vars '{t1.name}' and '{t2.name}'", loc)
            return

        # ---- Effect row unification (spec/05-type-system.md §4.3) ----
        if isinstance(t1, TRow) or isinstance(t2, TRow):
            # Promote a TMeta to an open row if needed (handles row-var meta)
            if not isinstance(t1, TRow):
                raise TypecheckError(
                    f"cannot unify row '{_pp(t2, self)}' with type '{_pp(t1, self)}'", loc)
            if not isinstance(t2, TRow):
                raise TypecheckError(
                    f"cannot unify row '{_pp(t1, self)}' with type '{_pp(t2, self)}'", loc)
            e1, tail1 = self._flatten_row(t1)
            e2, tail2 = self._flatten_row(t2)
            common = set(e1.keys()) & set(e2.keys())
            only1 = {k: v for k, v in e1.items() if k not in e2}   # in t1 not t2
            only2 = {k: v for k, v in e2.items() if k not in e1}   # in t2 not t1
            # Unify type args for shared effects
            for name in common:
                args1, args2 = e1[name], e2[name]
                if len(args1) != len(args2):
                    raise TypecheckError(
                        f"effect '{name}': argument arity mismatch", loc)
                for a1, a2 in zip(args1, args2):
                    self.unify(a1, a2, loc)
            if only1 or only2:
                # Asymmetric: excess effects flow into the other side's tail
                fresh_shared = self.fresh()
                if only2:
                    if tail1 is None:
                        raise TypecheckError(
                            f"closed effect row missing: {', '.join(sorted(only2))}", loc)
                    self.unify(tail1, TRow(only2, fresh_shared), loc)
                else:
                    # only1 non-empty, only2 empty
                    if tail1 is None:
                        pass  # t1 closed and has everything t2 has — tails below
                    else:
                        self.unify(tail1, TRow(only2, fresh_shared), loc)
                if only1:
                    if tail2 is None:
                        raise TypecheckError(
                            f"closed effect row missing: {', '.join(sorted(only1))}", loc)
                    self.unify(tail2, TRow(only1, fresh_shared), loc)
                else:
                    if tail2 is None:
                        pass
                    else:
                        self.unify(tail2, TRow(only1, fresh_shared), loc)
            else:
                # Same explicit effects; unify tails directly
                if tail1 is None and tail2 is None:
                    pass
                elif tail1 is None:
                    self.unify(tail2, TRow({}, None), loc)
                elif tail2 is None:
                    self.unify(tail1, TRow({}, None), loc)
                else:
                    self.unify(tail1, tail2, loc)
            return

        # ---- Computation type unification ----
        if isinstance(t1, TComp) and isinstance(t2, TComp):
            self.unify(t1.row, t2.row, loc)
            self.unify(t1.ty, t2.ty, loc)
            return
        # Permissive: TComp vs plain type — extract the value type only.
        # Effects in non-handle positions are not enforced in the bootstrap.
        if isinstance(t1, TComp) and not isinstance(t2, TComp):
            self.unify(t1.ty, t2, loc)
            return
        if isinstance(t2, TComp) and not isinstance(t1, TComp):
            self.unify(t1, t2.ty, loc)
            return

        raise TypecheckError(
            f"cannot unify '{_pp(t1, self)}' with '{_pp(t2, self)}'", loc)

    # ------------------------------------------------------------------ #
    # Free metas and generalization                                        #
    # ------------------------------------------------------------------ #

    def _free_metas(self, ty: Any, seen: set[int] | None = None) -> set[int]:
        if seen is None:
            seen = set()
        ty = self.deref(ty)
        if isinstance(ty, TMeta):
            if ty.id in seen:
                return set()
            seen.add(ty.id)
            return {ty.id}
        elif isinstance(ty, (TCon, TBound)):
            return set()
        elif isinstance(ty, TArr):
            return self._free_metas(ty.dom, seen) | self._free_metas(ty.cod, seen)
        elif isinstance(ty, TApp):
            return self._free_metas(ty.fun, seen) | self._free_metas(ty.arg, seen)
        elif isinstance(ty, TTup):
            result: set[int] = set()
            for e in ty.elems:
                result |= self._free_metas(e, seen)
            return result
        elif isinstance(ty, TRow):
            result = set()
            for args in ty.effects.values():
                for a in args:
                    result |= self._free_metas(a, seen)
            if ty.tail is not None:
                result |= self._free_metas(ty.tail, seen)
            return result
        elif isinstance(ty, TComp):
            return self._free_metas(ty.row, seen) | self._free_metas(ty.ty, seen)
        return set()

    def _free_metas_in_lenv(self, lenv: dict[str, Scheme]) -> set[int]:
        result: set[int] = set()
        for s in lenv.values():
            result |= self._free_metas(s.body)
        return result

    def _zonk(self, ty: Any) -> Any:
        """Fully resolve all metas."""
        ty = self.deref(ty)
        if isinstance(ty, (TMeta, TCon, TBound)):
            return ty
        elif isinstance(ty, TArr):
            return TArr(self._zonk(ty.dom), self._zonk(ty.cod))
        elif isinstance(ty, TApp):
            return TApp(self._zonk(ty.fun), self._zonk(ty.arg))
        elif isinstance(ty, TTup):
            return TTup([self._zonk(e) for e in ty.elems])
        elif isinstance(ty, TRow):
            zonked = {k: [self._zonk(a) for a in v] for k, v in ty.effects.items()}
            tail = self._zonk(ty.tail) if ty.tail is not None else None
            # If tail resolved to another TRow, flatten it in
            if isinstance(tail, TRow):
                for k, v in tail.effects.items():
                    if k not in zonked:
                        zonked[k] = v
                tail = tail.tail
            return TRow(zonked, tail)
        elif isinstance(ty, TComp):
            return TComp(self._zonk(ty.row), self._zonk(ty.ty))
        return ty

    def _generalize(self, lenv: dict[str, Scheme], ty: Any) -> Scheme:
        """Close over free metas not in lenv."""
        ty = self._zonk(ty)
        env_metas = self._free_metas_in_lenv(lenv)
        ty_metas = self._free_metas(ty)
        to_gen = ty_metas - env_metas
        if not to_gen:
            return Scheme([], ty)
        # Assign alphabetical names to metas
        letters = 'abcdefghijklmnopq'
        meta_to_name: dict[int, str] = {}
        for i, mid in enumerate(sorted(to_gen)):
            meta_to_name[mid] = letters[i % len(letters)]
        body = self._replace_metas(ty, meta_to_name)
        return Scheme(list(meta_to_name.values()), body)

    def _replace_metas(self, ty: Any, meta_to_name: dict[int, str]) -> Any:
        ty = self.deref(ty)
        if isinstance(ty, TMeta):
            name = meta_to_name.get(ty.id)
            return TBound(name) if name else ty
        elif isinstance(ty, (TCon, TBound)):
            return ty
        elif isinstance(ty, TArr):
            return TArr(
                self._replace_metas(ty.dom, meta_to_name),
                self._replace_metas(ty.cod, meta_to_name))
        elif isinstance(ty, TApp):
            return TApp(
                self._replace_metas(ty.fun, meta_to_name),
                self._replace_metas(ty.arg, meta_to_name))
        elif isinstance(ty, TTup):
            return TTup([self._replace_metas(e, meta_to_name) for e in ty.elems])
        elif isinstance(ty, TRow):
            new_effects = {k: [self._replace_metas(a, meta_to_name) for a in v]
                           for k, v in ty.effects.items()}
            new_tail = self._replace_metas(ty.tail, meta_to_name) if ty.tail is not None else None
            return TRow(new_effects, new_tail)
        elif isinstance(ty, TComp):
            return TComp(self._replace_metas(ty.row, meta_to_name),
                         self._replace_metas(ty.ty, meta_to_name))
        return ty

    def _instantiate(self, scheme: Scheme) -> Any:
        """Replace bound vars with fresh metas."""
        if not scheme.vars:
            return scheme.body
        sub = {v: self.fresh() for v in scheme.vars}
        return self._replace_bounds(scheme.body, sub)

    def _replace_bounds(self, ty: Any, sub: dict[str, Any]) -> Any:
        if isinstance(ty, TBound):
            return sub.get(ty.name, ty)
        elif isinstance(ty, (TMeta, TCon)):
            return ty
        elif isinstance(ty, TArr):
            return TArr(
                self._replace_bounds(ty.dom, sub),
                self._replace_bounds(ty.cod, sub))
        elif isinstance(ty, TApp):
            return TApp(
                self._replace_bounds(ty.fun, sub),
                self._replace_bounds(ty.arg, sub))
        elif isinstance(ty, TTup):
            return TTup([self._replace_bounds(e, sub) for e in ty.elems])
        elif isinstance(ty, TRow):
            new_effects = {k: [self._replace_bounds(a, sub) for a in v]
                           for k, v in ty.effects.items()}
            new_tail = self._replace_bounds(ty.tail, sub) if ty.tail is not None else None
            return TRow(new_effects, new_tail)
        elif isinstance(ty, TComp):
            return TComp(self._replace_bounds(ty.row, sub),
                         self._replace_bounds(ty.ty, sub))
        return ty

    # ------------------------------------------------------------------ #
    # AST type → internal monotype                                         #
    # ------------------------------------------------------------------ #

    def _collect_free_tyvars(self, ty: Any) -> list[str]:
        """Collect TyVar names (for implicit ∀ quantification)."""
        seen: list[str] = []
        def walk(t: Any) -> None:
            if isinstance(t, TyVar):
                if t.name not in seen:
                    seen.append(t.name)
            elif isinstance(t, AstTyCon):
                pass
            elif isinstance(t, AstTyApp):
                walk(t.fun); walk(t.arg)
            elif isinstance(t, AstTyArr):
                walk(t.from_); walk(t.to_)
            elif isinstance(t, TyForall):
                # Already explicitly quantified — skip vars
                inner = t.body
                walk(inner)
            elif isinstance(t, TyEffect):
                walk(t.ty)
                # Also collect type args within the effect row entries
                for _name, args in t.row.entries:
                    for arg in args:
                        walk(arg)
                # Row variables (r–z) participate in generalization too
                if t.row.row_var and t.row.row_var not in seen:
                    seen.append(t.row.row_var)
            elif isinstance(t, AstTyTuple):
                for e in t.elems: walk(e)
            elif isinstance(t, TyRecord):
                for _, ft in t.fields: walk(ft)
            elif isinstance(t, TyConstrained):
                walk(t.ty)
            elif isinstance(t, TyRefined):
                walk(t.ty)
        walk(ty)
        return seen

    def ast_to_mono(self, ty: Any, bound: dict[str, Any]) -> Any:
        """Convert an AST type to an internal monotype.

        bound: maps type-variable name -> TBound or TMeta
        Effect rows are silently ignored.
        """
        if ty is None:
            return self.fresh()
        if isinstance(ty, TyVar):
            if ty.name in bound:
                return bound[ty.name]
            # Unquantified type variable — treat as fresh meta
            m = self.fresh()
            bound[ty.name] = m
            return m
        if isinstance(ty, AstTyCon):
            name = str(ty.name)
            # If bare name, check for FQ equivalent in type_arity
            if '.' not in name:
                fq_name = f"{self.module}.{name}"
                if fq_name in self.type_arity:
                    name = fq_name
            return TCon(name)
        if isinstance(ty, AstTyApp):
            return TApp(self.ast_to_mono(ty.fun, bound), self.ast_to_mono(ty.arg, bound))
        if isinstance(ty, AstTyArr):
            return TArr(self.ast_to_mono(ty.from_, bound), self.ast_to_mono(ty.to_, bound))
        if isinstance(ty, TyForall):
            new_bound = dict(bound)
            for v in ty.vars:
                new_bound[v] = TBound(v)
            return self.ast_to_mono(ty.body, new_bound)
        if isinstance(ty, TyEffect):
            # Convert effect row to TRow, wrap return type in TComp
            row_effects: dict = {}
            for eff_name, eff_args in ty.row.entries:
                row_effects[eff_name] = [self.ast_to_mono(a, bound) for a in eff_args]
            if ty.row.row_var is not None:
                rv = ty.row.row_var
                if rv in bound:
                    tail = bound[rv]
                else:
                    tail = self.fresh()
                    bound[rv] = tail
            else:
                tail = None
            row = TRow(row_effects, tail)
            return TComp(row, self.ast_to_mono(ty.ty, bound))
        if isinstance(ty, AstTyTuple):
            return TTup([self.ast_to_mono(e, bound) for e in ty.elems])
        if isinstance(ty, TyRecord):
            # Treat record types as opaque TCon for now
            return self.fresh()
        if isinstance(ty, TyUnit):
            return TCon('⊤')
        if isinstance(ty, TyBottom):
            return TCon('⊥')
        if isinstance(ty, TyEmpty):
            return TCon('⊥')
        if isinstance(ty, TyConstrained):
            return self.ast_to_mono(ty.ty, bound)
        if isinstance(ty, TyRefined):
            return self.ast_to_mono(ty.ty, bound)
        # Unknown — return fresh meta
        return self.fresh()

    def ast_to_scheme(self, ty: Any) -> Scheme:
        """Convert an AST type annotation to a Scheme.

        Free TyVars become universally quantified.
        Explicit TyForall vars also become universally quantified.
        Constraints from TyConstrained are preserved.
        """
        if ty is None:
            return Scheme([], self.fresh())

        # Unwrap outermost TyForall/TyConstrained if present
        explicit_vars: list[str] = []
        ast_constraints: list[tuple[str, list]] = []
        inner_ty = ty
        if isinstance(ty, TyForall):
            explicit_vars = list(ty.vars)
            inner_ty = ty.body
        elif isinstance(ty, TyConstrained):
            # Peel off constraint wrapper, preserving constraints
            ast_constraints = list(ty.constraints)
            inner_ty = ty.ty
            if isinstance(inner_ty, TyForall):
                explicit_vars = list(inner_ty.vars)
                inner_ty = inner_ty.body

        # Collect implicit free type variables
        implicit_vars = self._collect_free_tyvars(inner_ty)
        all_vars = explicit_vars + [v for v in implicit_vars if v not in explicit_vars]

        # Build bound map
        bound = {v: TBound(v) for v in all_vars}
        body = self.ast_to_mono(inner_ty, bound)

        # Convert AST constraints to Scheme constraints
        scheme_constraints = []
        for class_name, type_args in ast_constraints:
            mono_args = [self.ast_to_mono(a, bound) for a in type_args]
            scheme_constraints.append((class_name, mono_args))

        return Scheme(all_vars, body, scheme_constraints)

    # ------------------------------------------------------------------ #
    # Built-in types and operators                                         #
    # ------------------------------------------------------------------ #

    def _init_builtins(self) -> None:
        """Pre-register primitive types, constructors, and operators."""
        # Primitive type constructors
        for name in ('Nat', 'Bool', 'Text', 'Bytes', '⊤', '⊥'):
            self.type_arity[name] = 0

        # Bool constructors (pre-resolved keyword constructors from scope)
        for fq in ('True', 'False'):
            self.type_env[fq] = Scheme([], TCon('Bool'))
        self.type_constructors['Bool'] = [('True', 0), ('False', 0)]

        # Unit keyword constructor
        self.type_env['Unit'] = Scheme([], TCon('⊤'))

        # Common infix operator types (operator string → Scheme)
        # These are used when ExprOp is encountered.
        nat2 = Scheme([], TArr(TCon('Nat'), TArr(TCon('Nat'), TCon('Nat'))))
        bool2 = Scheme([], TArr(TCon('Bool'), TArr(TCon('Bool'), TCon('Bool'))))
        nat_cmp = Scheme([], TArr(TCon('Nat'), TArr(TCon('Nat'), TCon('Bool'))))
        a = TBound('a')
        poly_eq = Scheme(['a'], TArr(a, TArr(a, TCon('Bool'))))
        self._op_types: dict[str, Scheme] = {
            '+': nat2, '-': nat2, '*': nat2, '/': nat2,
            '≤': nat_cmp, '≥': nat_cmp, '<': nat_cmp, '>': nat_cmp,
            '&&': bool2, '||': bool2,
            '==': poly_eq, '≠': poly_eq,
            '++': Scheme(['a'], TArr(a, TArr(a, a))),  # overloaded, but ok
            '|>': Scheme(['a', 'b'], TArr(TBound('a'), TArr(
                TArr(TBound('a'), TBound('b')), TBound('b')))),
        }

    def _lookup_op(self, op: str) -> Scheme:
        """Return the type scheme for a built-in operator, or a fresh scheme."""
        return self._op_types.get(op, Scheme([], self.fresh()))

    def _resolve_list_tycon(self) -> str:
        """Resolve the List type constructor name, preferring FQ if registered."""
        # Check for module-qualified List (e.g. Core.List.List)
        for name in self.type_arity:
            if name.endswith('.List') and self.type_arity[name] == 1:
                return name
        return 'List'

    # ------------------------------------------------------------------ #
    # Exhaustiveness checking                                             #
    # ------------------------------------------------------------------ #

    def _check_exhaustiveness(self, scrutinee, scr_ty, arms, loc):
        """Check pattern match exhaustiveness; raise TypecheckError on
        either non-exhaustive matches or redundant arms.

        Redundancy is a hard error rather than a warning because the
        bootstrap codegen dispatches constructor matches by tag-sorted
        order (`codegen.py:_compile_con_match`), not by source order.
        A pattern subsumed by an earlier arm in source order may still
        execute at runtime — i.e. the source-level and codegen-level
        views of "which arm fires" diverge.  Rejecting the program at
        type-check time prevents the codegen from ever seeing the
        inconsistent state.  AUDIT.md B4.
        """
        from bootstrap.exhaustiveness import (
            check_exhaustiveness, ExhaustivenessError,
        )
        resolved_ty = self.deref(scr_ty)
        try:
            redundancies = check_exhaustiveness(
                resolved_ty, arms, self.type_constructors, self.deref, loc
            )
        except ExhaustivenessError as e:
            raise TypecheckError(str(e), e.loc) from None

        if redundancies:
            idx, desc = redundancies[0]
            arm_loc = getattr(arms[idx][0], 'loc', None) or loc
            raise TypecheckError(
                f"redundant match arm at index {idx}: {desc} is subsumed "
                f"by an earlier pattern. Reorder so the catch-all pattern "
                f"comes last (the bootstrap codegen dispatches by "
                f"constructor tag, not source order — see AUDIT.md B4).",
                arm_loc,
            )

    # ------------------------------------------------------------------ #
    # Constructor type reconstruction from DeclType                        #
    # ------------------------------------------------------------------ #

    def _build_result_type(self, fq_name: str, params: list[str]) -> Any:
        """Build the result monotype for a type constructor with params."""
        ty: Any = TCon(fq_name)
        for p in params:
            ty = TApp(ty, TBound(p))
        return ty

    def _register_decl_type(self, fq_prefix: str, decl: DeclType) -> None:
        """Register DeclType: the type constructor and all constructor schemes."""
        fq_type = f"{fq_prefix}.{decl.name}" if fq_prefix else decl.name
        self.type_arity[fq_type] = len(decl.params)
        result = self._build_result_type(fq_type, decl.params)
        bound = {v: TBound(v) for v in decl.params}
        con_list: list[tuple[str, int]] = []
        for con in decl.constructors:
            fq_con = f"{fq_prefix}.{con.name}" if fq_prefix else con.name
            # Build arrow type: arg1 → arg2 → ... → result
            con_ty: Any = result
            for arg_ty in reversed(con.arg_types):
                con_ty = TArr(self.ast_to_mono(arg_ty, dict(bound)), con_ty)
            self.type_env[fq_con] = Scheme(list(decl.params), con_ty)
            con_list.append((fq_con, len(con.arg_types)))
        self.type_constructors[fq_type] = con_list

    def _register_decl_class(self, fq_prefix: str, decl: DeclClass) -> None:
        """Register class methods with their parameterized schemes."""
        fq_cls = f"{fq_prefix}.{decl.name}" if fq_prefix else decl.name
        methods: dict[str, Scheme] = {}
        for member in decl.members:
            if isinstance(member, ClassMember):
                fq_m = f"{fq_prefix}.{member.name}" if fq_prefix else member.name
                scheme = self.ast_to_scheme(member.ty)
                self.type_env[fq_m] = scheme
                methods[member.name] = scheme
        self.class_methods[fq_cls] = methods

    def _register_decl_eff(self, fq_prefix: str, decl: DeclEff) -> None:
        """Register effect operations from a DeclEff.

        For `eff State s { get : ⊤ → s; put : s → ⊤ }`, each operation
        is registered as a function whose return type is a TComp carrying the
        effect in its row.  Row variable `r` is added so callers can compose
        the effect with other effects in their context.

          State.get : ∀ s r. ⊤ → {State s | r} s
          State.put : ∀ s r. s → {State s | r} ⊤
        """
        fq_eff = f"{fq_prefix}.{decl.name}" if fq_prefix else decl.name
        # Build a bound map for the effect's own type params
        param_bounds = {p: TBound(p) for p in decl.params}
        # Effect type args in the row: list of TBound for each param
        eff_args = [param_bounds[p] for p in decl.params]
        # Row variable for openness
        row_var = TBound('r') if 'r' not in param_bounds else TBound('r_')
        row_var_name = 'r' if 'r' not in param_bounds else 'r_'
        for op in decl.ops:
            fq_op = f"{fq_eff}.{op.name}"
            # Parse op type: A → B  (caller perspective)
            # Result: A → {Eff args | r} B
            op_mono = self.ast_to_mono(op.ty, dict(param_bounds))
            # op_mono should be TArr(arg_ty, ret_ty) or just ret_ty (nullary)
            if isinstance(op_mono, TArr):
                arg_ty = op_mono.dom
                ret_ty = op_mono.cod
            else:
                arg_ty = TCon('⊤')
                ret_ty = op_mono
            eff_row = TRow({decl.name: eff_args}, row_var)
            op_return = TComp(eff_row, ret_ty)
            full_ty = TArr(arg_ty, op_return)
            all_vars = list(decl.params) + [row_var_name]
            self.type_env[fq_op] = Scheme(all_vars, full_ty)

    def _register_decl_ext(self, decl: DeclExt) -> None:
        """Register external mod items."""
        mod_path = '.'.join(decl.module_path)
        for item in decl.items:
            fq = f"{mod_path}.{item.name}"
            if item.is_type:
                self.type_arity[fq] = 0
            else:
                scheme = self.ast_to_scheme(item.ty)
                self.type_env[fq] = scheme

    # ------------------------------------------------------------------ #
    # Pattern inference                                                    #
    # ------------------------------------------------------------------ #

    def infer_pat(
        self, pat: Any, lenv: dict[str, Scheme]
    ) -> tuple[Any, dict[str, Scheme]]:
        """Infer type of pattern; return (pat_type, new_bindings).

        new_bindings: dict of var_name -> Scheme for variables bound by this pattern.
        """
        if isinstance(pat, PatWild):
            return self.fresh(), {}

        if isinstance(pat, PatVar):
            m = self.fresh()
            return m, {pat.name: Scheme([], m)}

        if isinstance(pat, PatNat):
            return TCon('Nat'), {}

        if isinstance(pat, PatText):
            return TCon('Text'), {}

        if isinstance(pat, PatCon):
            fq = str(pat.name)
            if fq not in self.type_env:
                raise TypecheckError(f"unknown constructor '{fq}'", pat.loc)
            con_ty = self._instantiate(self.type_env[fq])
            bindings: dict[str, Scheme] = {}
            # Peel off arg types
            for arg_pat in pat.args:
                if not isinstance(self.deref(con_ty), TArr):
                    raise TypecheckError(
                        f"too many arguments to constructor '{fq}'", pat.loc)
                arr = self.deref(con_ty)
                arg_ty, arg_bindings = self.infer_pat(arg_pat, lenv)
                self.unify(arr.dom, arg_ty, pat.loc)
                bindings.update(arg_bindings)
                con_ty = arr.cod
            return con_ty, bindings

        if isinstance(pat, PatTuple):
            elem_types = []
            bindings = {}
            for p in pat.pats:
                t, b = self.infer_pat(p, lenv)
                elem_types.append(t)
                bindings.update(b)
            return TTup(elem_types), bindings

        if isinstance(pat, PatList):
            elem_ty = self.fresh()
            bindings = {}
            for p in pat.pats:
                t, b = self.infer_pat(p, lenv)
                self.unify(elem_ty, t, p.loc)
                bindings.update(b)
            list_ty = TApp(TCon(self._resolve_list_tycon()), elem_ty)
            return list_ty, bindings

        if isinstance(pat, PatCons):
            head_ty, head_b = self.infer_pat(pat.head, lenv)
            tail_ty, tail_b = self.infer_pat(pat.tail, lenv)
            list_ty = TApp(TCon(self._resolve_list_tycon()), head_ty)
            self.unify(list_ty, tail_ty, pat.loc)
            return list_ty, {**head_b, **tail_b}

        if isinstance(pat, PatAs):
            inner_ty, inner_b = self.infer_pat(pat.pat, lenv)
            inner_b[pat.name] = Scheme([], inner_ty)
            return inner_ty, inner_b

        if isinstance(pat, PatOr):
            ty, bindings = self.infer_pat(pat.pats[0], lenv)
            for p in pat.pats[1:]:
                t, _ = self.infer_pat(p, lenv)
                self.unify(ty, t, p.loc)
            return ty, bindings

        # Unknown pattern — be permissive
        return self.fresh(), {}

    # ------------------------------------------------------------------ #
    # Expression inference                                                 #
    # ------------------------------------------------------------------ #

    def infer(self, lenv: dict[str, Scheme], expr: Any) -> Any:
        """Infer type of expression in the given local environment.

        Thin wrapper around ``_infer_impl`` that records every visited
        expression in ``self.expr_types`` when recording is enabled. The
        recorded type is the just-inferred form; later unifications may
        refine its metas, so callers should ``_zonk`` before display.
        """
        ty = self._infer_impl(lenv, expr)
        if self.expr_types is not None:
            self.expr_types[id(expr)] = ty
        return ty

    def _infer_impl(self, lenv: dict[str, Scheme], expr: Any) -> Any:
        """Inference implementation. See ``infer`` for the public entry."""

        if isinstance(expr, ExprNat):
            return TCon('Nat')

        if isinstance(expr, (ExprText, ExprRawText)):
            return TCon('Text')

        if isinstance(expr, (ExprBytes, ExprHexBytes)):
            return TCon('Bytes')

        if isinstance(expr, ExprUnit):
            return TCon('⊤')

        if isinstance(expr, ExprVar):
            name = str(expr.name)
            if name in lenv:
                return self._instantiate(lenv[name])
            if name in self.type_env:
                return self._instantiate(self.type_env[name])
            raise TypecheckError(f"unbound variable '{name}'", expr.loc)

        if isinstance(expr, ExprApp):
            fun_ty = self.infer(lenv, expr.fun)
            arg_ty = self.infer(lenv, expr.arg)
            cod = self.fresh()
            self.unify(fun_ty, TArr(arg_ty, cod), expr.loc)
            return cod

        if isinstance(expr, ExprLam):
            lenv2 = dict(lenv)
            param_types = []
            for pat in expr.params:
                pt, bindings = self.infer_pat(pat, lenv2)
                param_types.append(pt)
                lenv2.update(bindings)
            body_ty = self.infer(lenv2, expr.body)
            # Build right-to-left arrow: p1 → p2 → ... → body
            result: Any = body_ty
            for pt in reversed(param_types):
                result = TArr(pt, result)
            return result

        if isinstance(expr, ExprLet):
            # Local let: monomorphic
            rhs_ty = self.infer(lenv, expr.rhs)
            if expr.type_ann is not None:
                ann_ty = self.ast_to_mono(expr.type_ann, {})
                self.unify(rhs_ty, ann_ty, expr.loc)
            pat_ty, bindings = self.infer_pat(expr.pattern, lenv)
            self.unify(rhs_ty, pat_ty, expr.loc)
            lenv2 = {**lenv, **bindings}
            return self.infer(lenv2, expr.body)

        if isinstance(expr, ExprPin):
            rhs_ty = self.infer(lenv, expr.rhs)
            if expr.type_ann is not None:
                ann_ty = self.ast_to_mono(expr.type_ann, {})
                self.unify(rhs_ty, ann_ty, expr.loc)
            lenv2 = {**lenv, expr.name: Scheme([], rhs_ty)}
            return self.infer(lenv2, expr.body)

        if isinstance(expr, ExprMatch):
            scr_ty = self.infer(lenv, expr.scrutinee)
            result_ty = self.fresh()
            for pat, guard, body in expr.arms:
                pat_ty, bindings = self.infer_pat(pat, lenv)
                self.unify(scr_ty, pat_ty, pat.loc)
                lenv2 = {**lenv, **bindings}
                if guard is not None:
                    g_ty = self.infer(lenv2, guard)
                    self.unify(g_ty, TCon('Bool'), guard.loc)
                body_ty = self.infer(lenv2, body)
                self.unify(result_ty, body_ty, body.loc)
            # Exhaustiveness check
            self._check_exhaustiveness(expr.scrutinee, scr_ty, expr.arms, expr.loc)
            return result_ty

        if isinstance(expr, ExprIf):
            cond_ty = self.infer(lenv, expr.cond)
            self.unify(cond_ty, TCon('Bool'), expr.loc)
            then_ty = self.infer(lenv, expr.then_)
            else_ty = self.infer(lenv, expr.else_)
            self.unify(then_ty, else_ty, expr.loc)
            return then_ty

        if isinstance(expr, ExprTuple):
            return TTup([self.infer(lenv, e) for e in expr.elems])

        if isinstance(expr, ExprList):
            elem_ty = self.fresh()
            for e in expr.elems:
                t = self.infer(lenv, e)
                self.unify(elem_ty, t, e.loc)
            return TApp(TCon(self._resolve_list_tycon()), elem_ty)

        if isinstance(expr, ExprOp):
            op_scheme = self._lookup_op(expr.op)
            op_ty = self._instantiate(op_scheme)
            lhs_ty = self.infer(lenv, expr.lhs)
            rhs_ty = self.infer(lenv, expr.rhs)
            result_ty = self.fresh()
            self.unify(op_ty, TArr(lhs_ty, TArr(rhs_ty, result_ty)), expr.loc)
            return result_ty

        if isinstance(expr, ExprUnary):
            operand_ty = self.infer(lenv, expr.operand)
            if expr.op == '-':
                self.unify(operand_ty, TCon('Nat'), expr.loc)
                return TCon('Nat')
            if expr.op == '¬':
                self.unify(operand_ty, TCon('Bool'), expr.loc)
                return TCon('Bool')
            return operand_ty

        if isinstance(expr, ExprAnn):
            ann_ty = self.ast_to_mono(expr.type_, {})
            inner_ty = self.infer(lenv, expr.expr)
            self.unify(inner_ty, ann_ty, expr.loc)
            return ann_ty

        if isinstance(expr, ExprWith):
            # dict application — just infer the base expression
            return self.infer(lenv, expr.expr)

        if isinstance(expr, ExprRecord):
            # Record literal — return fresh for now
            for _, v in expr.fields:
                self.infer(lenv, v)
            return self.fresh()

        if isinstance(expr, ExprRecordUpdate):
            base_ty = self.infer(lenv, expr.base)
            for _, v in expr.fields:
                self.infer(lenv, v)
            return base_ty

        if isinstance(expr, ExprDo):
            rhs_ty = self.infer(lenv, expr.rhs)
            lenv2 = {**lenv, expr.name: Scheme([], rhs_ty)}
            return self.infer(lenv2, expr.body)

        if isinstance(expr, ExprFix):
            # fix (λ self args → body) : T
            # The lambda has type T → T (self-ref in, T out).
            t = self.fresh()
            lam_ty = self.infer(lenv, expr.lam)
            self.unify(lam_ty, TArr(t, t), expr.loc)
            return t

        if isinstance(expr, ExprHandle):
            # handle computation { | return x → e_r | op args k → e_op ... }
            # Typing (spec/05-type-system.md §5.1):
            #   computation : {E, R} α
            #   return arm:  x : α  ⊢  e_r : β
            #   op arms:  args : A_i,  k : B_i → {R} β  ⊢  e_op : {R} β
            #   result type: {R} β
            alpha = self.fresh()                     # computation value type
            beta = self.fresh()                      # handler result type
            residual_row = self.fresh_row()          # R: residual effect row

            # Infer computation; unify its value type with alpha
            comp_ty = self.infer(lenv, expr.comp)
            comp_row = self.fresh_row()
            self.unify(comp_ty, TComp(comp_row, alpha), expr.loc)

            for arm in expr.arms:
                if isinstance(arm, HandlerReturn):
                    # | return x → body: x : alpha, body : beta
                    pat_ty, pat_b = self.infer_pat(arm.pattern, lenv)
                    self.unify(pat_ty, alpha, arm.loc)
                    body_ty = self.infer({**lenv, **pat_b}, arm.body)
                    self.unify(body_ty, beta, arm.loc)

                elif isinstance(arm, HandlerOp):
                    # Look up the operation to get its argument and return types
                    op_fq = str(arm.op_name)
                    if op_fq in self.type_env:
                        op_ty = self._instantiate(self.type_env[op_fq])
                        # op_ty: A → {E | r} B  →  arg_ty=A, op_ret_val=B
                        op_arg_ty = self.fresh()
                        op_ret = self.fresh()
                        self.unify(op_ty, TArr(op_arg_ty, op_ret), arm.loc)
                        op_ret_val = self.fresh()
                        self.unify(op_ret, TComp(self.fresh_row(), op_ret_val), arm.loc)
                    else:
                        op_arg_ty = self.fresh()
                        op_ret_val = self.fresh()

                    # Bind arg patterns — for simplicity, bind each to op_arg_ty
                    lenv2 = dict(lenv)
                    for arg_pat in arm.arg_pats:
                        pt, pb = self.infer_pat(arg_pat, lenv2)
                        self.unify(pt, op_arg_ty, arm.loc)
                        lenv2.update(pb)

                    # Continuation k : op_ret_val → {R} β
                    k_ty = TArr(op_ret_val, TComp(residual_row, beta))
                    lenv2[arm.resume] = Scheme([], k_ty)

                    # Body : {R} β
                    body_ty = self.infer(lenv2, arm.body)
                    self.unify(body_ty, TComp(residual_row, beta), arm.loc)

            return TComp(residual_row, beta)

        # Fall-through: return fresh meta for unhandled nodes
        return self.fresh()

    # ------------------------------------------------------------------ #
    # Top-level declaration processing                                     #
    # ------------------------------------------------------------------ #

    def _fq(self, name: str) -> str:
        return f"{self.module}.{name}"

    def _register_types_in_decls(self, decls: list[Any], prefix: str) -> None:
        """Pre-pass: register type constructors and ext mods (needed for body inference)."""
        for decl in decls:
            if isinstance(decl, DeclType):
                self._register_decl_type(prefix, decl)
            elif isinstance(decl, DeclTypeAlias):
                fq = f"{prefix}.{decl.name}" if prefix else decl.name
                self.type_arity[fq] = len(decl.params)
            elif isinstance(decl, DeclTypeBuiltin):
                fq = f"{prefix}.{decl.name}" if prefix else decl.name
                self.type_arity[fq] = len(decl.params)
            elif isinstance(decl, DeclRecord):
                fq = f"{prefix}.{decl.name}" if prefix else decl.name
                self.type_arity[fq] = len(decl.params)
            elif isinstance(decl, DeclExt):
                self._register_decl_ext(decl)
            elif isinstance(decl, DeclEff):
                self._register_decl_eff(prefix, decl)
            elif isinstance(decl, DeclClass):
                self._register_decl_class(prefix, decl)
            elif isinstance(decl, DeclMod):
                sub_prefix = f"{prefix}.{'.'.join(decl.name)}" if prefix else '.'.join(decl.name)
                self._register_types_in_decls(decl.body, sub_prefix)

    def _prepass_lets(self, decls: list[Any], prefix: str) -> None:
        """Pre-pass: assign provisional types to all DeclLets (enables forward refs)."""
        for decl in decls:
            if isinstance(decl, DeclLet):
                fq = f"{prefix}.{decl.name}" if prefix else decl.name
                if decl.type_ann is not None:
                    self.type_env[fq] = self.ast_to_scheme(decl.type_ann)
                else:
                    self.type_env[fq] = Scheme([], self.fresh())
            elif isinstance(decl, DeclMod):
                sub_prefix = f"{prefix}.{'.'.join(decl.name)}" if prefix else '.'.join(decl.name)
                self._prepass_lets(decl.body, sub_prefix)

    # ------------------------------------------------------------------ #
    # Dependency graph and SCC detection (for ordered checking)            #
    # ------------------------------------------------------------------ #

    def _collect_expr_refs(self, expr: Any, into: set) -> None:
        """Walk an expression and collect all ExprVar name strings into `into`."""
        if isinstance(expr, ExprVar):
            into.add(str(expr.name))
        elif isinstance(expr, ExprApp):
            self._collect_expr_refs(expr.fun, into)
            self._collect_expr_refs(expr.arg, into)
        elif isinstance(expr, ExprLam):
            self._collect_expr_refs(expr.body, into)
        elif isinstance(expr, ExprLet):
            self._collect_expr_refs(expr.rhs, into)
            self._collect_expr_refs(expr.body, into)
        elif isinstance(expr, ExprPin):
            self._collect_expr_refs(expr.rhs, into)
            self._collect_expr_refs(expr.body, into)
        elif isinstance(expr, ExprMatch):
            self._collect_expr_refs(expr.scrutinee, into)
            for _pat, guard, body in expr.arms:
                if guard is not None:
                    self._collect_expr_refs(guard, into)
                self._collect_expr_refs(body, into)
        elif isinstance(expr, ExprIf):
            self._collect_expr_refs(expr.cond, into)
            self._collect_expr_refs(expr.then_, into)
            self._collect_expr_refs(expr.else_, into)
        elif isinstance(expr, ExprTuple):
            for e in expr.elems:
                self._collect_expr_refs(e, into)
        elif isinstance(expr, ExprList):
            for e in expr.elems:
                self._collect_expr_refs(e, into)
        elif isinstance(expr, ExprFix):
            self._collect_expr_refs(expr.lam, into)
        elif isinstance(expr, ExprOp):
            self._collect_expr_refs(expr.lhs, into)
            self._collect_expr_refs(expr.rhs, into)
        elif isinstance(expr, ExprUnary):
            self._collect_expr_refs(expr.operand, into)
        elif isinstance(expr, ExprAnn):
            self._collect_expr_refs(expr.expr, into)
        elif isinstance(expr, ExprDo):
            self._collect_expr_refs(expr.rhs, into)
            self._collect_expr_refs(expr.body, into)
        elif isinstance(expr, ExprHandle):
            self._collect_expr_refs(expr.comp, into)
            for arm in expr.arms:
                if isinstance(arm, HandlerReturn):
                    self._collect_expr_refs(arm.body, into)
                elif isinstance(arm, HandlerOp):
                    self._collect_expr_refs(arm.body, into)
        elif isinstance(expr, ExprWith):
            self._collect_expr_refs(expr.expr, into)
        elif isinstance(expr, ExprRecord):
            for _, v in expr.fields:
                self._collect_expr_refs(v, into)
        elif isinstance(expr, ExprRecordUpdate):
            self._collect_expr_refs(expr.base, into)
            for _, v in expr.fields:
                self._collect_expr_refs(v, into)

    def _build_dep_graph(self, let_decls: list, prefix: str) -> dict:
        """Build a forward-reference dependency graph among DeclLets in this scope."""
        fq_set = {f'{prefix}.{d.name}' for d in let_decls}
        graph: dict = {}
        for d in let_decls:
            fq = f'{prefix}.{d.name}'
            refs: set = set()
            self._collect_expr_refs(d.body, refs)
            fq_refs: set = set()
            for r in refs:
                if r in fq_set:
                    fq_refs.add(r)
                candidate = f'{prefix}.{r}'
                if candidate in fq_set:
                    fq_refs.add(candidate)
            graph[fq] = fq_refs & fq_set
        return graph

    def _tarjan_scc(self, graph: dict) -> list:
        """Tarjan's SCC algorithm. Returns SCCs in topological order (deps first).
        Within each SCC names are sorted lexicographically."""
        index_counter = [0]
        index: dict = {}
        lowlink: dict = {}
        on_stack: set = set()
        stack: list = []
        sccs: list = []

        def strongconnect(v: str) -> None:
            index[v] = index_counter[0]
            lowlink[v] = index_counter[0]
            index_counter[0] += 1
            stack.append(v)
            on_stack.add(v)
            for w in graph.get(v, set()):
                if w not in index:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], index[w])
            if lowlink[v] == index[v]:
                scc: list = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    scc.append(w)
                    if w == v:
                        break
                sccs.append(sorted(scc))

        for v in sorted(graph.keys()):
            if v not in index:
                strongconnect(v)

        return sccs  # topological order: dependencies before dependents

    # ------------------------------------------------------------------ #
    # Per-decl and per-SCC checking                                        #
    # ------------------------------------------------------------------ #

    def _check_let_decl(self, decl: Any, prefix: str) -> None:
        """Type-check a single DeclLet, then generalize if unannotated."""
        fq = f"{prefix}.{decl.name}" if prefix else decl.name
        expected = self.type_env.get(fq, Scheme([], self.fresh()))
        expected_mono = self._instantiate(expected)
        body_ty = self.infer({}, decl.body)
        self.unify(expected_mono, body_ty, decl.loc)
        if decl.type_ann is None:
            self.type_env[fq] = self._generalize({}, expected_mono)

    def _check_mutual_scc(self, decls: list, prefix: str) -> None:
        """Type-check a mutually recursive SCC.

        All bodies are checked before any generalization so that provisional
        unification variables are not prematurely closed over.
        """
        # Phase 1: instantiate all provisional types and check bodies.
        fq_to_mono: dict = {}
        for decl in decls:
            fq = f"{prefix}.{decl.name}" if prefix else decl.name
            expected = self.type_env.get(fq, Scheme([], self.fresh()))
            fq_to_mono[fq] = self._instantiate(expected)

        for decl in decls:
            fq = f"{prefix}.{decl.name}" if prefix else decl.name
            body_ty = self.infer({}, decl.body)
            self.unify(fq_to_mono[fq], body_ty, decl.loc)

        # Phase 2: generalize unannotated members (all at once).
        for decl in decls:
            fq = f"{prefix}.{decl.name}" if prefix else decl.name
            if decl.type_ann is None:
                self.type_env[fq] = self._generalize({}, fq_to_mono[fq])

    def _check_decls(self, decls: list[Any], prefix: str) -> None:
        """Main pass: infer bodies and unify/generalize, respecting SCC order."""
        # Collect let decls and process them in SCC (topological) order.
        let_decls = [d for d in decls if isinstance(d, DeclLet)]
        if let_decls:
            decl_map = {f'{prefix}.{d.name}': d for d in let_decls}
            graph = self._build_dep_graph(let_decls, prefix)
            sccs = self._tarjan_scc(graph)
            for scc_fq_names in sccs:
                scc_lets = [decl_map[n] for n in scc_fq_names if n in decl_map]
                if not scc_lets:
                    continue
                if len(scc_lets) == 1:
                    self._check_let_decl(scc_lets[0], prefix)
                else:
                    self._check_mutual_scc(scc_lets, prefix)

        # Handle instances and submodules in their original order.
        for decl in decls:
            if isinstance(decl, DeclInst):
                self._check_instance(decl, prefix)
            elif isinstance(decl, DeclMod):
                sub_prefix = f"{prefix}.{'.'.join(decl.name)}" if prefix else '.'.join(decl.name)
                self._check_decls(decl.body, sub_prefix)

    def _check_instance(self, decl: DeclInst, prefix: str) -> None:
        """Check that instance methods type-check against class method signatures."""
        class_fq = f"{prefix}.{decl.class_name}" if prefix else decl.class_name
        # Resolve class_fq against type_env (might be in a different module)
        # Find class method signatures
        methods = self.class_methods.get(class_fq, {})

        # Register the instance in the instance db
        if decl.type_args:
            first_arg = decl.type_args[0]
            con_name = str(first_arg) if isinstance(first_arg, str) else _pp(
                self.ast_to_mono(first_arg, {}), self)
            self.instance_db.setdefault(class_fq, set()).add(con_name)

        for member in decl.members:
            if not isinstance(member, InstanceMember):
                continue
            # Check method body type-checks
            method_scheme = methods.get(member.name)
            if method_scheme is not None:
                expected_mono = self._instantiate(method_scheme)
                body_ty = self.infer({}, member.body)
                self.unify(expected_mono, body_ty, decl.loc)
            else:
                # Method not in class — scope checker would have caught this;
                # just infer the body to catch internal errors
                self.infer({}, member.body)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def check(self, program: Program, scope_env: Env) -> None:
        """Run the full type-checking pipeline on a resolved program."""
        prefix = self.module

        # Phase 1: register all type declarations and external mods
        self._register_types_in_decls(program.decls, prefix)

        # Phase 2: pre-assign types to all top-level lets
        self._prepass_lets(program.decls, prefix)

        # Phase 3: infer bodies and unify
        self._check_decls(program.decls, prefix)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def typecheck(
    program: Program,
    scope_env: Env,
    module: str,
    filename: str = '<stdin>',
    prior_type_env: TypeEnv | None = None,
    prior_type_constructors: dict[str, list[tuple[str, int]]] | None = None,
) -> TypeEnv:
    """Type-check a resolved program; return the final TypeEnv.

    Args:
        module: Module name. Must match the name passed to ``resolve()`` and to
            any later Glass IR renderer — the returned TypeEnv keys are FQ
            names of the form ``f'{module}.{decl_name}'``, and a renderer
            given a different module will silently fail to find them.
        prior_type_env: Pre-existing type environment from other modules.
            Entries are copied into the checker before checking begins.
        prior_type_constructors: Pre-existing type constructor registry from
            other modules (type_fq → [(con_fq, arity)]).

    Raises TypecheckError on the first type error.
    """
    tc = TypeChecker(module, filename)
    if prior_type_env:
        tc.type_env.update(prior_type_env)
    if prior_type_constructors:
        tc.type_constructors.update(prior_type_constructors)
    tc.check(program, scope_env)
    return tc.type_env


def typecheck_with_types(
    program: Program,
    scope_env: Env,
    module: str,
    filename: str = '<stdin>',
    prior_type_env: TypeEnv | None = None,
    prior_type_constructors: dict[str, list[tuple[str, int]]] | None = None,
) -> tuple[TypeEnv, dict[int, Any]]:
    """Type-check and return both the top-level TypeEnv and a per-expression
    type map keyed by ``id(expr)``.

    Intended for tooling — IDE hover, MCP ``infer_type``, etc. The map is
    populated during inference and then fully ``_zonk``'d so metas resolved
    by later unifications are reflected in the recorded types.

    Raises TypecheckError on the first type error.
    """
    tc = TypeChecker(module, filename)
    if prior_type_env:
        tc.type_env.update(prior_type_env)
    if prior_type_constructors:
        tc.type_constructors.update(prior_type_constructors)
    tc.expr_types = {}
    tc.check(program, scope_env)
    zonked = {k: tc._zonk(v) for k, v in tc.expr_types.items()}
    return tc.type_env, zonked
