"""
Gallowglass bootstrap scope resolver.

Resolves all names in a parsed Program to their fully-qualified forms and
builds a flat Env (symbol table) for the type checker.

Algorithm:
  Phase 1 (pre-pass):  Collect all top-level declarations into the module frame.
                        This enables forward references between top-level lets.
  Phase 2 (in-order):  Process use declarations and resolve expressions, types,
                        and patterns as we walk the declaration list.

Output:
  (resolved_program, env)
  - resolved_program: same structure, every QualName in ExprVar/PatCon/TyCon
    replaced with its fully-qualified form.
  - env: Env for this module; all top-level bindings are exported.

Public API:
  resolve(program, module_name, module_env, filename) -> (Program, Env)

Reference: bootstrap/src/scope.sire
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Union

from bootstrap.lexer import Loc
from bootstrap.ast import (
    Program, QualName,
    # Types
    TyVar, TyCon, TyApp, TyArr, TyForall, TyEffect, TyTuple, TyRecord,
    TyUnit, TyBottom, TyEmpty, TyConstrained, TyRefined, EffRow,
    # Patterns
    PatWild, PatVar, PatCon, PatNat, PatText, PatTuple, PatList,
    PatCons, PatAs, PatOr,
    # Expressions
    ExprVar, ExprApp, ExprLam, ExprLet, ExprMatch, ExprHandle,
    HandlerReturn, HandlerOp, ExprIf, ExprTuple, ExprList, ExprNat,
    ExprText, ExprRawText, ExprBytes, ExprHexBytes, ExprUnit, ExprPin,
    ExprDo, ExprFix, ExprOp, ExprUnary, ExprAnn, ExprWith, ExprRecord,
    ExprRecordUpdate,
    # Declarations
    ContractClause, DeclLet, Constructor, DeclType, DeclTypeAlias,
    DeclTypeBuiltin, DeclRecord, EffOp, DeclEff, ClassMember, ClassLaw,
    DeclClass, InstanceMember, DeclInst, ExtItem, DeclExt, UseSpec,
    DeclUse, ModItem, DeclMod,
)


# ---------------------------------------------------------------------------
# Binding kinds
# ---------------------------------------------------------------------------

@dataclass
class BindingValue:
    """A let-bound value or function."""
    fq_name: str
    type_ann: Any | None
    decl: Any         # DeclLet or None
    loc: Loc

@dataclass
class BindingCon:
    """A data constructor."""
    fq_name: str
    fq_type: str      # fully-qualified type it belongs to
    arity: int
    loc: Loc

@dataclass
class BindingType:
    """A type constructor."""
    fq_name: str
    kind_arity: int   # number of type parameters
    loc: Loc

@dataclass
class BindingClass:
    """A typeclass declaration."""
    fq_name: str
    param_count: int
    loc: Loc

@dataclass
class BindingClassMethod:
    """A typeclass method (also visible as a value-level binding)."""
    fq_name: str
    class_fq: str
    type_ann: Any | None
    loc: Loc

@dataclass
class BindingExtValue:
    """An external (VM boundary) operation from external mod."""
    fq_name: str
    type_ann: Any | None
    loc: Loc

@dataclass
class BindingExtType:
    """An external type from external mod."""
    fq_name: str
    loc: Loc

Binding = Union[
    BindingValue, BindingCon, BindingType, BindingClass,
    BindingClassMethod, BindingExtValue, BindingExtType,
]


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

@dataclass
class Env:
    """
    Flat symbol table produced by scope resolution.

    bindings:       fq_name (str) → Binding
    module_exports: module_fq (str) → set[str] of fq_names exported
    class_methods:  class_fq (str) → set[str] of method fq_names
    """
    bindings: dict[str, Binding] = field(default_factory=dict)
    module_exports: dict[str, set[str]] = field(default_factory=dict)
    class_methods: dict[str, set[str]] = field(default_factory=dict)

    def lookup(self, fq_name: str) -> Binding | None:
        return self.bindings.get(fq_name)

    def exports_of(self, module_fq: str) -> set[str]:
        return self.module_exports.get(module_fq, set())


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class ScopeError(Exception):
    def __init__(self, msg: str, loc: Loc):
        super().__init__(f"{loc.file}:{loc.line}:{loc.col}: error: {msg}")
        self.loc = loc


# ---------------------------------------------------------------------------
# Scope frame and resolver state
# ---------------------------------------------------------------------------

@dataclass
class ScopeFrame:
    """
    One lexical scope frame.

    short_name → set[fq_name]  (set size > 1 means ambiguous import)
    is_local: True for lambda/let/match frames; False for module-level frames.
    """
    bindings: dict[str, set[str]] = field(default_factory=dict)
    is_local: bool = False

    def bind(self, short: str, fq: str) -> None:
        if short not in self.bindings:
            self.bindings[short] = set()
        self.bindings[short].add(fq)

    def lookup(self, short: str) -> set[str] | None:
        return self.bindings.get(short)


class Resolver:
    def __init__(
        self,
        module_name: str,
        module_env: dict[str, Env],
        filename: str,
    ):
        self.module_name = module_name
        self.module_env = module_env   # already-resolved external modules
        self.filename = filename
        self.env = Env()
        self.env.module_exports[module_name] = set()
        # Frame stack: index 0 = outermost module frame
        self.frames: list[ScopeFrame] = [ScopeFrame(is_local=False)]
        # Module alias map: short prefix → fq module name
        # e.g. 'List' → 'Core.List' after  use Core.List
        self.module_aliases: dict[str, str] = {}
        # Pre-declare keyword constructors (True, False, Unit, Never)
        # These are built-in and don't belong to any user module.
        for kw, ty in [('True', 'Bool'), ('False', 'Bool'),
                       ('Unit', 'Unit'), ('Never', 'Never')]:
            fq = kw   # bare name IS the fq for built-ins
            b = BindingCon(fq, ty, 0, Loc('<builtin>', 0, 0))
            self.env.bindings[fq] = b
            self.frames[0].bind(kw, fq)

    # ------------------------------------------------------------------
    # Frame management
    # ------------------------------------------------------------------

    def _push_frame(self, local: bool = True) -> None:
        self.frames.append(ScopeFrame(is_local=local))

    def _pop_frame(self) -> None:
        self.frames.pop()

    def _bind_in_top(self, short: str, fq: str) -> None:
        """Bind short → fq in the innermost frame."""
        self.frames[-1].bind(short, fq)

    def _lookup_short(self, short: str, loc: Loc) -> str:
        """Look up a bare name in the frame stack, inner-to-outer."""
        for frame in reversed(self.frames):
            candidates = frame.lookup(short)
            if candidates is not None:
                if len(candidates) > 1:
                    mods = ', '.join(sorted(candidates))
                    raise ScopeError(
                        f"ambiguous name '{short}' (could be: {mods})", loc)
                return next(iter(candidates))
        raise ScopeError(f"unbound name '{short}'", loc)

    def _lookup_qual(self, parts: list[str], loc: Loc) -> str:
        """Look up a qualified name M.M...M.name."""
        # Everything but last is module path; last is the name.
        mod_parts = parts[:-1]
        name = parts[-1]
        mod_short = '.'.join(mod_parts)

        # Resolve module alias (e.g. 'List' → 'Core.List')
        fq_mod = self.module_aliases.get(mod_short)
        if fq_mod is None:
            # Try exact match in module_env keys
            for k in self.module_env:
                if k == mod_short or k.endswith('.' + mod_short):
                    fq_mod = k
                    break
        if fq_mod is None:
            raise ScopeError(f"unknown module '{mod_short}'", loc)

        fq = fq_mod + '.' + name

        # Check in module_env
        other_env = self.module_env.get(fq_mod)
        if other_env is not None:
            if fq in other_env.bindings:
                return fq
            raise ScopeError(
                f"'{name}' is not defined in module '{fq_mod}'", loc)

        # Check in our own env (external mods register under their own path)
        if fq in self.env.bindings:
            return fq

        raise ScopeError(
            f"'{name}' is not defined in module '{fq_mod}'", loc)

    def _resolve_qname(self, qname: QualName) -> str:
        """Return the fully-qualified name for a QualName reference."""
        if len(qname.parts) == 1:
            return self._lookup_short(qname.parts[0], qname.loc)
        return self._lookup_qual(qname.parts, qname.loc)

    def _fq(self, name: str, prefix: str | None = None) -> str:
        """Build a fully-qualified name under the given prefix (or current module)."""
        base = prefix if prefix is not None else self.module_name
        return f"{base}.{name}"

    # ------------------------------------------------------------------
    # Registration helpers
    # ------------------------------------------------------------------

    def _register(self, fq: str, binding: Binding, short: str | None = None) -> None:
        """Register a binding in env and in the top frame under its short name."""
        if fq in self.env.bindings:
            prev = self.env.bindings[fq]
            raise ScopeError(
                f"duplicate definition of '{fq.split('.')[-1]}'", binding.loc)
        self.env.bindings[fq] = binding
        self.env.module_exports[self.module_name].add(fq)
        # Bind the short name in the module frame (frame[0])
        bare = short if short is not None else fq.split('.')[-1]
        self.frames[0].bind(bare, fq)

    def _register_ext(self, fq: str, binding: Binding) -> None:
        """Register an external binding — no current-module export, absolute path."""
        self.env.bindings[fq] = binding
        # Also bind under the short bare name for local reference
        bare = fq.split('.')[-1]
        self.frames[0].bind(bare, fq)

    # ------------------------------------------------------------------
    # Phase 1: collect top-level declarations (pre-pass)
    # ------------------------------------------------------------------

    def _collect_decls(self, decls: list[Any], prefix: str | None = None) -> None:
        """
        Register all names declared in decls into the env and module frame.
        Does NOT resolve expressions — that happens in Phase 2.

        prefix: the namespace prefix for this block.
                If None, we are at the module's top level.
                If set, we are inside a nested mod block — bare names are NOT
                bound in the module frame; only qualified paths are accessible.
        """
        mod = prefix if prefix is not None else self.module_name
        top_level = (prefix is None)
        for decl in decls:
            self._collect_decl(decl, mod, top_level=top_level)

    def _collect_decl(self, decl: Any, mod: str, top_level: bool = True) -> None:
        if isinstance(decl, DeclLet):
            fq = f"{mod}.{decl.name}"
            b = BindingValue(fq, decl.type_ann, decl, decl.loc)
            if fq in self.env.bindings:
                raise ScopeError(
                    f"duplicate definition of '{decl.name}'", decl.loc)
            self.env.bindings[fq] = b
            self.env.module_exports.setdefault(self.module_name, set()).add(fq)
            if top_level:
                self.frames[0].bind(decl.name, fq)
            # Nested mod items: bind only the qualified path, not the bare name

        elif isinstance(decl, DeclType):
            fq_ty = f"{mod}.{decl.name}"
            b = BindingType(fq_ty, len(decl.params), decl.loc)
            self._safe_register_type(fq_ty, b, decl.name, top_level=top_level)
            for con in decl.constructors:
                fq_con = f"{mod}.{con.name}"
                bc = BindingCon(fq_con, fq_ty, len(con.arg_types), con.loc)
                self._safe_register_con(fq_con, bc, con.name, top_level=top_level)

        elif isinstance(decl, DeclTypeAlias):
            fq = f"{mod}.{decl.name}"
            b = BindingType(fq, len(decl.params), decl.loc)
            self._safe_register_type(fq, b, decl.name, top_level=top_level)

        elif isinstance(decl, DeclTypeBuiltin):
            fq = f"{mod}.{decl.name}"
            b = BindingType(fq, len(decl.params), decl.loc)
            self._safe_register_type(fq, b, decl.name, top_level=top_level)

        elif isinstance(decl, DeclRecord):
            fq = f"{mod}.{decl.name}"
            b = BindingType(fq, len(decl.params), decl.loc)
            self._safe_register_type(fq, b, decl.name, top_level=top_level)
            # Synthetic constructor shares fq with type for records
            if fq not in self.env.bindings:
                bc = BindingCon(fq, fq, len(decl.fields), decl.loc)
                self.env.bindings[fq] = bc
                if top_level:
                    self.frames[0].bind(decl.name, fq)

        elif isinstance(decl, DeclEff):
            fq = f"{mod}.{decl.name}"
            b = BindingType(fq, len(decl.params), decl.loc)
            self._safe_register_type(fq, b, decl.name, top_level=top_level)
            # Register each effect op as a value binding so callers can reference it.
            # E.g. `eff Counter { inc : Unit → Nat }` makes `inc` resolve to
            # `Module.Counter.inc` (FQ name used by the codegen CPS compilation).
            for op in decl.ops:
                fq_op = f"{fq}.{op.name}"
                b_op = BindingValue(fq_op, op.ty, None, op.loc)
                if fq_op not in self.env.bindings:
                    self.env.bindings[fq_op] = b_op
                    self.env.module_exports.setdefault(self.module_name, set()).add(fq_op)
                    if top_level:
                        self.frames[0].bind(op.name, fq_op)

        elif isinstance(decl, DeclClass):
            fq = f"{mod}.{decl.name}"
            b = BindingClass(fq, len(decl.params), decl.loc)
            if fq not in self.env.bindings:
                self.env.bindings[fq] = b
                self.env.module_exports.setdefault(self.module_name, set()).add(fq)
                if top_level:
                    self.frames[0].bind(decl.name, fq)
            self.env.class_methods.setdefault(fq, set())
            for member in decl.members:
                if isinstance(member, ClassMember):
                    fq_m = f"{mod}.{member.name}"
                    bm = BindingClassMethod(fq_m, fq, member.ty, member.loc)
                    if fq_m not in self.env.bindings:
                        self.env.bindings[fq_m] = bm
                        self.env.module_exports.setdefault(self.module_name, set()).add(fq_m)
                        if top_level:
                            self.frames[0].bind(member.name, fq_m)
                    self.env.class_methods[fq].add(fq_m)

        elif isinstance(decl, DeclInst):
            # Instance declarations don't introduce new names at the value level.
            # Validation of member names against class is done in Phase 2.
            pass

        elif isinstance(decl, DeclExt):
            # External mod — bindings under the external path, always absolute.
            ext_mod = '.'.join(decl.module_path)
            # Register module alias so  ExtMod.name  resolves correctly
            last = decl.module_path[-1]
            self.module_aliases[ext_mod] = ext_mod
            self.module_aliases[last] = ext_mod
            for item in decl.items:
                if item.is_type:
                    fq = f"{ext_mod}.{item.name}"
                    b = BindingExtType(fq, item.loc)
                    self.env.bindings[fq] = b
                    self.frames[0].bind(item.name, fq)
                else:
                    fq = f"{ext_mod}.{item.name}"
                    b = BindingExtValue(fq, item.ty, item.loc)
                    self.env.bindings[fq] = b
                    self.frames[0].bind(item.name, fq)

        elif isinstance(decl, DeclMod):
            sub_prefix = f"{mod}.{'.'.join(decl.name)}"
            # Register sub-module alias
            self.module_aliases['.'.join(decl.name)] = sub_prefix
            self.module_aliases[sub_prefix] = sub_prefix
            # DeclMod.body contains Decl objects directly (not ModItem wrappers)
            # top_level=False: inner names not bound as bare names in module frame
            self._collect_decls(decl.body, prefix=sub_prefix)

        elif isinstance(decl, DeclUse):
            # use declarations are order-sensitive; handled in Phase 2.
            pass

    def _safe_register_type(self, fq: str, b: Binding, short: str, top_level: bool = True) -> None:
        if fq in self.env.bindings:
            raise ScopeError(
                f"duplicate definition of '{short}'", b.loc)
        self.env.bindings[fq] = b
        self.env.module_exports.setdefault(self.module_name, set()).add(fq)
        if top_level:
            self.frames[0].bind(short, fq)

    def _safe_register_con(self, fq: str, b: BindingCon, short: str, top_level: bool = True) -> None:
        existing = self.env.bindings.get(fq)
        if existing is not None and not isinstance(existing, BindingType):
            raise ScopeError(
                f"duplicate definition of '{short}'", b.loc)
        # Constructor may share fq with its type (e.g. newtype Foo = | Foo)
        self.env.bindings[fq] = b
        self.env.module_exports.setdefault(self.module_name, set()).add(fq)
        if top_level:
            self.frames[0].bind(short, fq)

    # ------------------------------------------------------------------
    # Phase 2: resolve declarations in order
    # ------------------------------------------------------------------

    def resolve_program(self, program: Program) -> Program:
        # Pre-pass: collect all top-level names
        self._collect_decls(program.decls)
        # Resolution pass
        resolved = [self._resolve_decl(d) for d in program.decls]
        return Program(resolved, program.loc)

    def _resolve_decl(self, decl: Any) -> Any:
        if isinstance(decl, DeclUse):
            self._process_use(decl)
            return decl

        if isinstance(decl, DeclLet):
            body = self._resolve_expr(decl.body)
            type_ann = self._resolve_type(decl.type_ann) if decl.type_ann else None
            return DeclLet(decl.name, type_ann, decl.contracts, body, decl.loc)

        if isinstance(decl, DeclType):
            return decl   # constructors are in env; types erased at runtime

        if isinstance(decl, (DeclTypeAlias, DeclTypeBuiltin, DeclRecord, DeclEff)):
            return decl

        if isinstance(decl, DeclClass):
            members = []
            for m in decl.members:
                if isinstance(m, ClassMember):
                    default = self._resolve_expr(m.default) if m.default else None
                    members.append(ClassMember(m.name, m.ty, m.contracts, default, m.loc))
                else:
                    members.append(m)
            return DeclClass(decl.constraints, decl.name, decl.params, members, decl.loc)

        if isinstance(decl, DeclInst):
            return self._resolve_inst(decl)

        if isinstance(decl, DeclExt):
            return decl

        if isinstance(decl, DeclMod):
            # DeclMod.body contains Decl objects directly (not ModItem wrappers)
            resolved_inner = [self._resolve_decl(d) for d in decl.body]
            return DeclMod(decl.name, resolved_inner, decl.loc)

        return decl

    def _process_use(self, decl: DeclUse) -> None:
        mod_path = '.'.join(decl.module_path)
        other_env = self.module_env.get(mod_path)
        if other_env is None:
            raise ScopeError(
                f"unknown module '{mod_path}' (not yet resolved)", decl.loc)

        # Register module alias: last component and full path
        last = decl.module_path[-1]
        self.module_aliases[mod_path] = mod_path
        self.module_aliases[last] = mod_path

        if decl.spec is None:
            # use Mod  — qualified access only; no new unqualified names
            return

        if decl.spec.unqualified:
            # use Mod unqualified { names... }
            # Parser emits items as ('name', str), ('type', str), ('op', str),
            # ('instance', name, types), or 'instances' (bare keyword).
            for item in decl.spec.names:
                short = _use_item_short(item)
                if short is None:
                    continue   # 'instances' wildcard — no value binding
                fq = f"{mod_path}.{short}"
                if fq not in other_env.bindings:
                    raise ScopeError(
                        f"'{short}' is not exported by module '{mod_path}'",
                        decl.spec.loc)
                self._bind_in_top(short, fq)
        else:
            # use Mod { names... }  — bring module prefix into scope
            for item in decl.spec.names:
                short = _use_item_short(item)
                if short is None:
                    continue
                fq = f"{mod_path}.{short}"
                self._bind_in_top(short, fq)

    def _resolve_inst(self, decl: DeclInst) -> DeclInst:
        # Validate member names against the class declaration
        class_short = decl.class_name
        class_fq = self._lookup_short(class_short, decl.loc)
        valid_methods = self.env.class_methods.get(class_fq, set())
        # valid_methods is a set of fq names; extract short names
        valid_shorts = {fq.split('.')[-1] for fq in valid_methods}

        members = []
        for m in decl.members:
            if m.name not in valid_shorts:
                raise ScopeError(
                    f"'{m.name}' is not a member of class '{class_short}'",
                    m.loc)
            body = self._resolve_expr(m.body)
            members.append(InstanceMember(m.name, body, m.loc))
        return DeclInst(decl.constraints, decl.class_name, decl.type_args, members, decl.loc)

    # ------------------------------------------------------------------
    # Type resolution
    # ------------------------------------------------------------------

    def _resolve_type(self, ty: Any) -> Any:
        if ty is None:
            return None
        if isinstance(ty, TyVar):
            return ty   # type variables are not resolved (typecheck handles them)
        if isinstance(ty, TyCon):
            try:
                fq = self._resolve_qname(ty.name)
                return TyCon(QualName(fq.split('.'), ty.name.loc), ty.loc)
            except ScopeError:
                # Effect rows are not checked in the bootstrap — be lenient
                # with unresolved type constructors (they may be effect names
                # from prelude modules not yet compiled).
                return ty
        if isinstance(ty, TyApp):
            return TyApp(self._resolve_type(ty.fun), self._resolve_type(ty.arg), ty.loc)
        if isinstance(ty, TyArr):
            return TyArr(self._resolve_type(ty.from_), self._resolve_type(ty.to_), ty.loc)
        if isinstance(ty, TyForall):
            return TyForall(ty.vars, self._resolve_type(ty.body), ty.loc)
        if isinstance(ty, TyEffect):
            # Effect rows: resolve the base type; effect names are lenient
            row = self._resolve_eff_row(ty.row)
            base = self._resolve_type(ty.ty)
            return TyEffect(row, base, ty.loc)
        if isinstance(ty, TyTuple):
            return TyTuple([self._resolve_type(e) for e in ty.elems], ty.loc)
        if isinstance(ty, TyRecord):
            fields = [(n, self._resolve_type(t)) for n, t in ty.fields]
            return TyRecord(fields, ty.loc)
        if isinstance(ty, TyConstrained):
            return TyConstrained(ty.constraints, self._resolve_type(ty.ty), ty.loc)
        if isinstance(ty, TyRefined):
            return TyRefined(ty.name, self._resolve_type(ty.ty), ty.pred, ty.loc)
        # TyUnit, TyBottom, TyEmpty: no names to resolve
        return ty

    def _resolve_eff_row(self, row: EffRow) -> EffRow:
        # Effect names in rows are lenient in the bootstrap; don't error
        return row

    # ------------------------------------------------------------------
    # Expression resolution
    # ------------------------------------------------------------------

    def _resolve_expr(self, expr: Any) -> Any:
        if isinstance(expr, ExprVar):
            try:
                fq = self._resolve_qname(expr.name)
                return ExprVar(QualName(fq.split('.'), expr.name.loc), expr.loc)
            except ScopeError:
                raise

        if isinstance(expr, ExprApp):
            return ExprApp(
                self._resolve_expr(expr.fun),
                self._resolve_expr(expr.arg),
                expr.loc)

        if isinstance(expr, ExprLam):
            self._push_frame()
            params = [self._bind_pat_for_lam(p) for p in expr.params]
            body = self._resolve_expr(expr.body)
            self._pop_frame()
            return ExprLam(params, body, expr.loc)

        if isinstance(expr, ExprLet):
            rhs = self._resolve_expr(expr.rhs)
            type_ann = self._resolve_type(expr.type_ann) if expr.type_ann else None
            self._push_frame()
            self._bind_pat_names(expr.pattern)
            body = self._resolve_expr(expr.body)
            self._pop_frame()
            return ExprLet(expr.pattern, type_ann, rhs, body, expr.loc)

        if isinstance(expr, ExprMatch):
            scrutinee = self._resolve_expr(expr.scrutinee)
            arms = []
            for (pat, guard, body) in expr.arms:
                self._push_frame()
                resolved_pat = self._resolve_pat(pat)
                guard_r = self._resolve_expr(guard) if guard else None
                body_r = self._resolve_expr(body)
                self._pop_frame()
                arms.append((resolved_pat, guard_r, body_r))
            return ExprMatch(scrutinee, arms, expr.loc)

        if isinstance(expr, ExprHandle):
            comp = self._resolve_expr(expr.comp)
            arms = [self._resolve_handler_arm(a) for a in expr.arms]
            return ExprHandle(comp, arms, expr.loc)

        if isinstance(expr, ExprIf):
            return ExprIf(
                self._resolve_expr(expr.cond),
                self._resolve_expr(expr.then_),
                self._resolve_expr(expr.else_),
                expr.loc)

        if isinstance(expr, ExprTuple):
            return ExprTuple([self._resolve_expr(e) for e in expr.elems], expr.loc)

        if isinstance(expr, ExprList):
            return ExprList([self._resolve_expr(e) for e in expr.elems], expr.loc)

        if isinstance(expr, ExprPin):
            rhs = self._resolve_expr(expr.rhs)
            type_ann = self._resolve_type(expr.type_ann) if expr.type_ann else None
            self._push_frame()
            self._bind_in_top(expr.name, expr.name)   # local pin binder
            body = self._resolve_expr(expr.body)
            self._pop_frame()
            return ExprPin(expr.name, type_ann, rhs, body, expr.loc)

        if isinstance(expr, ExprDo):
            rhs = self._resolve_expr(expr.rhs)
            self._push_frame()
            self._bind_in_top(expr.name, expr.name)
            body = self._resolve_expr(expr.body)
            self._pop_frame()
            return ExprDo(expr.name, rhs, body, expr.loc)

        if isinstance(expr, ExprFix):
            return ExprFix(self._resolve_expr(expr.lam), expr.loc)

        if isinstance(expr, ExprOp):
            return ExprOp(
                expr.op,
                self._resolve_expr(expr.lhs),
                self._resolve_expr(expr.rhs),
                expr.loc)

        if isinstance(expr, ExprUnary):
            return ExprUnary(expr.op, self._resolve_expr(expr.operand), expr.loc)

        if isinstance(expr, ExprAnn):
            return ExprAnn(
                self._resolve_expr(expr.expr),
                self._resolve_type(expr.type_),
                expr.loc)

        if isinstance(expr, ExprWith):
            return ExprWith(
                self._resolve_expr(expr.expr),
                self._resolve_expr(expr.dict_),
                [self._resolve_expr(a) for a in expr.extra_args],
                expr.loc)

        if isinstance(expr, ExprRecord):
            fields = [(n, self._resolve_expr(v)) for n, v in expr.fields]
            return ExprRecord(fields, expr.loc)

        if isinstance(expr, ExprRecordUpdate):
            base = self._resolve_expr(expr.base)
            fields = [(n, self._resolve_expr(v)) for n, v in expr.fields]
            return ExprRecordUpdate(base, fields, expr.loc)

        # Literals and ExprUnit: no names
        return expr

    def _resolve_handler_arm(self, arm: Any) -> Any:
        if isinstance(arm, HandlerReturn):
            self._push_frame()
            self._bind_pat_names(arm.pattern)
            body = self._resolve_expr(arm.body)
            self._pop_frame()
            return HandlerReturn(arm.pattern, body, arm.loc)
        if isinstance(arm, HandlerOp):
            self._push_frame()
            for p in arm.arg_pats:
                self._bind_pat_names(p)
            self._bind_in_top(arm.resume, arm.resume)
            body = self._resolve_expr(arm.body)
            self._pop_frame()
            return HandlerOp(arm.once, arm.op_name, arm.arg_pats, arm.resume, body, arm.loc)
        return arm

    # ------------------------------------------------------------------
    # Pattern resolution
    # ------------------------------------------------------------------

    def _resolve_pat(self, pat: Any) -> Any:
        """Resolve constructor references in a pattern; bind PatVar names locally."""
        if isinstance(pat, PatCon):
            try:
                fq = self._resolve_qname(pat.name)
                binding = self.env.bindings.get(fq)
                if binding is not None and not isinstance(binding, BindingCon):
                    raise ScopeError(
                        f"'{fq.split('.')[-1]}' is not a constructor", pat.name.loc)
                # Arity check
                if isinstance(binding, BindingCon):
                    if len(pat.args) != binding.arity:
                        raise ScopeError(
                            f"constructor '{fq.split('.')[-1]}' expects "
                            f"{binding.arity} argument(s), got {len(pat.args)}",
                            pat.loc)
                args = [self._resolve_pat(a) for a in pat.args]
                return PatCon(QualName(fq.split('.'), pat.name.loc), args, pat.loc)
            except ScopeError:
                raise

        if isinstance(pat, PatVar):
            self._bind_in_top(pat.name, pat.name)   # local binder
            return pat

        if isinstance(pat, PatAs):
            inner = self._resolve_pat(pat.pat)
            self._bind_in_top(pat.name, pat.name)
            return PatAs(inner, pat.name, pat.loc)

        if isinstance(pat, PatOr):
            return PatOr([self._resolve_pat(p) for p in pat.pats], pat.loc)

        if isinstance(pat, PatTuple):
            return PatTuple([self._resolve_pat(p) for p in pat.pats], pat.loc)

        if isinstance(pat, PatList):
            return PatList([self._resolve_pat(p) for p in pat.pats], pat.loc)

        if isinstance(pat, PatCons):
            return PatCons(
                self._resolve_pat(pat.head),
                self._resolve_pat(pat.tail),
                pat.loc)

        # PatWild, PatNat, PatText: no names
        return pat

    def _bind_pat_names(self, pat: Any) -> None:
        """Bind names introduced by a pattern into the current frame (no resolution)."""
        if isinstance(pat, PatVar):
            self._bind_in_top(pat.name, pat.name)
        elif isinstance(pat, PatAs):
            self._bind_pat_names(pat.pat)
            self._bind_in_top(pat.name, pat.name)
        elif isinstance(pat, PatTuple):
            for p in pat.pats:
                self._bind_pat_names(p)
        elif isinstance(pat, PatList):
            for p in pat.pats:
                self._bind_pat_names(p)
        elif isinstance(pat, PatCons):
            self._bind_pat_names(pat.head)
            self._bind_pat_names(pat.tail)
        elif isinstance(pat, PatCon):
            for a in pat.args:
                self._bind_pat_names(a)
        elif isinstance(pat, PatOr):
            for p in pat.pats:
                self._bind_pat_names(p)

    def _bind_pat_for_lam(self, pat: Any) -> Any:
        """Bind lambda parameter pattern names and return the pattern unchanged."""
        self._bind_pat_names(pat)
        return pat


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _use_item_short(item: Any) -> str | None:
    """
    Extract the short (unqualified) name from a use-list item.

    The parser emits use items as:
      ('name', str)        — a snake_case value
      ('type', str)        — a PascalCase type
      ('op', str)          — an operator name
      ('instance', ...)    — an instance import (no value binding)
      'instances'          — wildcard instances import (no value binding)
    """
    if isinstance(item, str):
        return None   # 'instances' keyword — no value binding
    if isinstance(item, tuple):
        kind = item[0]
        if kind in ('name', 'type', 'op'):
            return item[1]
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve(
    program: Program,
    module_name: str,
    module_env: dict[str, Env] | None = None,
    filename: str = '<stdin>',
) -> tuple[Program, Env]:
    """
    Resolve all names in program.

    Parameters:
        program:     Parsed Program AST.
        module_name: Fully-qualified module name, e.g. 'Core.List'.
        module_env:  Envs of already-resolved modules (earlier files).
        filename:    For error messages.

    Returns:
        (resolved_program, env)

    Raises:
        ScopeError on the first detected error.
    """
    r = Resolver(module_name, module_env or {}, filename)
    resolved = r.resolve_program(program)
    return resolved, r.env
