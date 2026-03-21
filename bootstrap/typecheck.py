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
class TBound:
    """Scheme-bound variable (only appears inside Scheme.body)."""
    name: str


# Scheme: ∀ vars. body
@dataclass
class Scheme:
    vars: list[str]
    body: Any  # MonoType


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
    return repr(ty)


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
        self._init_builtins()

    # ------------------------------------------------------------------ #
    # Meta variables                                                       #
    # ------------------------------------------------------------------ #

    def fresh(self) -> TMeta:
        m = TMeta(self._counter)
        self._counter += 1
        return m

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
        return False

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
                walk(t.ty)  # ignore effect row
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
            return TCon(str(ty.name))
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
            return self.ast_to_mono(ty.ty, bound)  # ignore effect
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
        """
        if ty is None:
            return Scheme([], self.fresh())

        # Unwrap outermost TyForall if present
        explicit_vars: list[str] = []
        inner_ty = ty
        if isinstance(ty, TyForall):
            explicit_vars = list(ty.vars)
            inner_ty = ty.body
        elif isinstance(ty, TyConstrained):
            # Peel off constraint wrapper
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
        return Scheme(all_vars, body)

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
        for con in decl.constructors:
            fq_con = f"{fq_prefix}.{con.name}" if fq_prefix else con.name
            # Build arrow type: arg1 → arg2 → ... → result
            con_ty: Any = result
            for arg_ty in reversed(con.arg_types):
                con_ty = TArr(self.ast_to_mono(arg_ty, dict(bound)), con_ty)
            self.type_env[fq_con] = Scheme(list(decl.params), con_ty)

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
            list_ty = TApp(TCon('List'), elem_ty)
            return list_ty, bindings

        if isinstance(pat, PatCons):
            head_ty, head_b = self.infer_pat(pat.head, lenv)
            tail_ty, tail_b = self.infer_pat(pat.tail, lenv)
            list_ty = TApp(TCon('List'), head_ty)
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
        """Infer type of expression in the given local environment."""

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
            return TApp(TCon('List'), elem_ty)

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
            return self.infer(lenv, expr.lam)

        if isinstance(expr, ExprHandle):
            return self.infer(lenv, expr.comp)

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

    def _check_decls(self, decls: list[Any], prefix: str) -> None:
        """Main pass: infer bodies and unify/generalize."""
        for decl in decls:
            if isinstance(decl, DeclLet):
                fq = f"{prefix}.{decl.name}" if prefix else decl.name
                expected = self.type_env.get(fq, Scheme([], self.fresh()))
                expected_mono = self._instantiate(expected)

                # Infer the body in an empty local env (top-level)
                body_ty = self.infer({}, decl.body)
                self.unify(expected_mono, body_ty, decl.loc)

                # Generalize if unannotated
                if decl.type_ann is None:
                    self.type_env[fq] = self._generalize({}, expected_mono)

            elif isinstance(decl, DeclInst):
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
    module: str = 'Main',
    filename: str = '<stdin>',
) -> TypeEnv:
    """Type-check a resolved program; return the final TypeEnv.

    Raises TypecheckError on the first type error.
    """
    tc = TypeChecker(module, filename)
    tc.check(program, scope_env)
    return tc.type_env
