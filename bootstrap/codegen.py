"""
Gallowglass bootstrap code generator.

Compiles the restricted Gallowglass dialect to PLAN values.

Scope: literals, variables, lambda/application, if/then/else,
       simple pattern matching on Nat and Bool, top-level lets
       (non-recursive and self-recursive).  Mutual recursion is
       deferred to a later milestone.

Reference: spec/04-plan-encoding.md
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from bootstrap.ast import (
    Program,
    DeclLet, DeclType, DeclExt, DeclClass, DeclInst, DeclEff,
    Constructor, ClassMember, InstanceMember,
    ExprVar, ExprApp, ExprLam, ExprLet, ExprMatch, ExprIf,
    ExprNat, ExprText, ExprBytes, ExprHexBytes, ExprUnit, ExprTuple,
    ExprPin, ExprOp, ExprUnary, ExprAnn, ExprFix,
    ExprHandle, ExprDo, HandlerReturn, HandlerOp,
    PatVar, PatWild, PatCon, PatNat, PatTuple,
    TyConstrained,
)
from dev.harness.plan import P, L, A, N, is_nat, is_pin, is_law, is_app


class CodegenError(Exception):
    pass


@dataclass
class _MutualRef:
    """
    Sentinel stored in env.globals for cross-references inside a mutual SCC.
    During compilation of a mutual recursion group, every SCC member's FQ name
    (and short name) maps to a _MutualRef so that _compile_var can emit the
    correct shared-pin extraction + partial application.
    """
    index: int  # 0-based index in the shared selector row


# ---------------------------------------------------------------------------
# Name encoding
# ---------------------------------------------------------------------------

def encode_name(s: str) -> int:
    """Encode a string as a little-endian nat (UTF-8 bytes)."""
    b = s.encode('utf-8')
    if not b:
        return 0
    result = 0
    for i, byte in enumerate(b):
        result |= byte << (8 * i)
    return result


# ---------------------------------------------------------------------------
# Law body helpers
# ---------------------------------------------------------------------------

def bapp(f, *args):
    """
    Build a left-associative (0 f x1 x2 ...) call inside a law body.
    In a law body, (0 f x) means "apply f to x".
    bapp(f, x) = A(A(N(0), f), x)
    bapp(f, x, y) = A(A(N(0), A(A(N(0), f), x)), y)
    """
    result = f
    for arg in args:
        result = A(A(N(0), result), arg)
    return result


def body_nat(k: int, arity: int):
    """
    Return the PLAN representation of the literal nat k inside a law body
    whose arity is `arity`.  Nats 0..arity are de Bruijn indices in the
    law evaluator, so a literal k <= arity must be pinned to escape the
    de Bruijn interpretation.
    """
    if k <= arity:
        return P(N(k))
    return N(k)


# ---------------------------------------------------------------------------
# Compile-time environment
# ---------------------------------------------------------------------------

@dataclass
class Env:
    """Compilation environment: maps names to their PLAN-level representations."""

    # Top-level names → Pin containing the compiled law (or raw PLAN value
    # for constructors / externals).
    globals: dict[str, Any] = field(default_factory=dict)

    # Local names (lambda params, let-bindings) → de Bruijn index (int).
    # The index is relative to the current law's arity.
    # locals[name] = de Bruijn index (1-based, left-to-right)
    locals: dict[str, int] = field(default_factory=dict)

    # Current law's arity (number of parameters so far).
    arity: int = 0

    # The FQ name of the function currently being compiled (for self-recursion).
    self_ref_name: str = ''

    # Optional: maps local param name → type key string (e.g. "Nat", "Text").
    # Used by _infer_type_key to determine instance dicts for constrained calls.
    param_types: dict = field(default_factory=dict)

    def child(self) -> 'Env':
        """Return a shallow copy for a new scope."""
        return Env(globals=self.globals, locals=dict(self.locals), arity=self.arity,
                   self_ref_name=self.self_ref_name,
                   param_types=dict(self.param_types))

    def bind_param(self, name: str) -> 'Env':
        """Add a new parameter, incrementing arity.  Returns the new env."""
        new_env = self.child()
        new_env.arity = self.arity + 1
        # Shift all existing locals up by 1 (new param is inserted at position 1,
        # i.e. it gets index = new_arity = self.arity + 1 in the new frame).
        for k in new_env.locals:
            new_env.locals[k] += 1
        new_env.locals[name] = 1  # new param is always at index 1 in the *shifted* env
        # Wait, that's wrong. In PLAN law bodies:
        #   law {n arity body}: index 0 = self, index 1 = arg1, ..., index arity = argN
        # So if we have a law of arity 2 taking (x, y):
        #   x → index 1, y → index 2
        # When we add a new param to an existing arity-n env:
        #   the new param gets index (n+1), existing params keep their indices.
        # So we should NOT shift existing locals.
        new_env.locals = dict(self.locals)
        new_env.locals[name] = self.arity + 1
        new_env.arity = self.arity + 1
        return new_env

    def bind_param_typed(self, name: str, type_key: str) -> 'Env':
        """Like bind_param but also records the parameter's type key."""
        new_env = self.bind_param(name)
        new_env.param_types[name] = type_key
        return new_env


# ---------------------------------------------------------------------------
# Constructor table
# ---------------------------------------------------------------------------

@dataclass
class ConInfo:
    """Constructor metadata."""
    tag: int              # position in the type declaration
    arity: int            # number of fields
    fq_name: str          # fully-qualified constructor name


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------

class Compiler:
    """
    Compiles a resolved, type-checked Gallowglass program to a dict of
    top-level PLAN values.

    Usage:
        compiler = Compiler(module='Main')
        result = compiler.compile(program)
        # result: dict[str, Any]  (fq_name -> PLAN value)
    """

    def __init__(self, module: str = 'Main'):
        self.module = module
        # map fq_name → PLAN value (not pinned yet)
        self.compiled: dict[str, Any] = {}
        # constructor info table: fq_name → ConInfo
        self.con_info: dict[str, ConInfo] = {}
        # global env (fills in as we compile)
        self.env = Env()
        # effect op tag lookup: fq_op_name → tag (and short_name → tag)
        self.effect_op_tags: dict[str, int] = {}
        # Typeclass info: class_fq → ordered list of method short names
        self._class_methods: dict[str, list[str]] = {}
        # Constrained lets: fq → [(class_fq, [method_fq, ...])] per constraint
        self._constrained_lets: dict[str, list] = {}
        # Register builtin constructors from scope resolver.
        # Bool: False = tag 0, True = tag 1 (conventional ordering)
        # These match the scope resolver's pre-declared 'True'/'False' bindings.
        self._register_builtins()

    # -----------------------------------------------------------------------
    # Builtins
    # -----------------------------------------------------------------------

    def _register_builtins(self) -> None:
        """Register builtin constructors and CPS helpers in the global env."""
        # Bool: False = 0 (tag 0), True = 1 (tag 1)
        # Scope resolver assigns FQ = bare name for builtins.
        builtins = {
            'False': N(0),   # Bool.False = tag 0
            'True':  N(1),   # Bool.True  = tag 1
            'Unit':  N(0),   # Unit = 0 (nullary)
        }
        for name, val in builtins.items():
            self.env.globals[name] = val
            self.con_info[name] = ConInfo(
                tag=int(val), arity=0, fq_name=name
            )
        # `pure v` — wrap a pure value as a no-op CPS computation.
        # Law of arity 3: (value, dispatch, k) → k value
        # N(1)=value, N(2)=dispatch (ignored), N(3)=k
        # body = bapp(N(3), N(1)) = apply k to value
        pure_law = L(3, encode_name('pure'), bapp(N(3), N(1)))
        self.env.globals['pure'] = pure_law

    # -----------------------------------------------------------------------
    # Entry point
    # -----------------------------------------------------------------------

    def compile(self, program: Program) -> dict[str, Any]:
        """
        Compile a program.  Returns a dict mapping FQ names to PLAN values.
        The values are NOT pinned (caller may pin them).
        """
        # Pass 1: register type declarations (constructors)
        for decl in program.decls:
            if isinstance(decl, DeclType):
                self._register_type(decl)

        # Pass 2: register external declarations as opaque pins
        for decl in program.decls:
            if isinstance(decl, DeclExt):
                self._register_ext(decl)

        # Pass 2.5: register effect declarations as CPS laws
        for decl in program.decls:
            if isinstance(decl, DeclEff):
                self._register_eff(decl)

        # Pass 2.6: register typeclass declarations (method name ordering)
        for decl in program.decls:
            if isinstance(decl, DeclClass):
                self._compile_class(decl)

        # Pass 3: compile let and instance declarations together in source order.
        #
        # DeclLet groups are processed in Tarjan SCC topological order so that
        # mutually-recursive groups are compiled together and dependencies are
        # compiled before dependents.  DeclInst declarations are compiled in-line
        # (immediately when encountered in source order) so that instance method
        # bodies can reference previously compiled lets, and subsequent lets can
        # use the freshly emitted instance dicts at their call sites.
        let_decls = [d for d in program.decls if isinstance(d, DeclLet)]
        if let_decls:
            dep_graph = self._build_dep_graph(let_decls)
            sccs = self._tarjan_scc(dep_graph)
            decl_map = {f'{self.module}.{d.name}': d for d in let_decls}

            # Map each let fq-name → its SCC index in the topological order.
            fq_to_scc_idx: dict[str, int] = {}
            for idx, scc_names in enumerate(sccs):
                for fq in scc_names:
                    if fq in decl_map:
                        fq_to_scc_idx[fq] = idx

            compiled_scc_idxs: set[int] = set()

            for decl in program.decls:
                if isinstance(decl, DeclLet):
                    fq = f'{self.module}.{decl.name}'
                    scc_idx = fq_to_scc_idx.get(fq)
                    if scc_idx is not None and scc_idx not in compiled_scc_idxs:
                        compiled_scc_idxs.add(scc_idx)
                        scc_names = sccs[scc_idx]
                        scc_let_names = [n for n in scc_names if n in decl_map]
                        if len(scc_let_names) == 1:
                            self._compile_let(decl_map[scc_let_names[0]])
                        else:
                            scc_ds = [decl_map[n] for n in scc_let_names]
                            self._compile_mutual_scc(scc_let_names, scc_ds)
                elif isinstance(decl, DeclInst):
                    self._compile_inst(decl)

            # Compile any SCCs not yet triggered (e.g., out-of-order declarations)
            for scc_idx, scc_names in enumerate(sccs):
                if scc_idx not in compiled_scc_idxs:
                    scc_let_names = [n for n in scc_names if n in decl_map]
                    if scc_let_names:
                        compiled_scc_idxs.add(scc_idx)
                        if len(scc_let_names) == 1:
                            self._compile_let(decl_map[scc_let_names[0]])
                        else:
                            scc_ds = [decl_map[n] for n in scc_let_names]
                            self._compile_mutual_scc(scc_let_names, scc_ds)
        else:
            # No lets — just compile instances
            for decl in program.decls:
                if isinstance(decl, DeclInst):
                    self._compile_inst(decl)

        return self.compiled

    # -----------------------------------------------------------------------
    # Type declarations → constructor functions
    # -----------------------------------------------------------------------

    def _register_type(self, decl: DeclType) -> None:
        """Compile each constructor of a type declaration into a law."""
        for tag, con in enumerate(decl.constructors):
            fq_con = f'{self.module}.{con.name}'
            n_fields = len(con.arg_types)
            info = ConInfo(tag=tag, arity=n_fields, fq_name=fq_con)
            self.con_info[fq_con] = info

            if n_fields == 0:
                # Nullary constructor = bare nat tag
                val = N(tag)
            else:
                # Constructor function: law of arity n_fields
                # Body: (tag field1 field2 ... fieldN)
                # In law body context: tag must be body_nat(tag, n_fields)
                # fields are de Bruijn indices n_fields, n_fields-1, ..., 1
                # (left-to-right: param1 → index 1, paramN → index n_fields)
                body = self._build_constructor_body(tag, n_fields)
                name_nat = encode_name(con.name)
                val = L(n_fields, name_nat, body)

            self.compiled[fq_con] = val
            self.env.globals[fq_con] = val

    def _build_constructor_body(self, tag: int, n_fields: int) -> Any:
        """
        Build the law body for a constructor with given tag and field count.

        The constructor produces a tagged-row value (tag f1 f2 ... fN).
        In PLAN law body context:
          - N(k) where k <= arity is a de Bruijn ref (not a literal)
          - A(N(0), N(k)) = quote(k) = the literal nat k
          - A(A(N(0), f), x) = apply f to x

        So to build A(tag, f1, ..., fN) as the result value, we use:
          bapp(bapp(...bapp(quote(tag), N(1))..., N(N-1)), N(N))
        """
        # quote(tag) = A(N(0), N(tag)) — embeds the literal tag nat in a law body
        quoted_tag = A(N(0), N(tag))
        # Apply to each field using bapp: bapp(f, x) = A(A(N(0), f), x)
        result = quoted_tag
        for i in range(1, n_fields + 1):
            result = bapp(result, N(i))
        return result

    # -----------------------------------------------------------------------
    # External declarations → opaque nat/pin stubs
    # -----------------------------------------------------------------------

    # Mapping from Core.PLAN FQ names to their actual PLAN opcode numbers.
    # Opcodes as implemented by the harness and xocore-tech/PLAN VM:
    #   P(0)=Pin(1 arg), P(1)=MkLaw(3 args), P(2)=Inc(1 arg),
    #   P(3)=Case_(6 args), P(4)=Force(1 arg)
    _CORE_PLAN_OPCODES: dict[str, int] = {
        'Core.PLAN.pin':     0,
        'Core.PLAN.mk_law':  1,
        'Core.PLAN.inc':     2,
        'Core.PLAN.reflect': 3,
        'Core.PLAN.force':   4,
    }

    def _register_ext(self, decl: DeclExt) -> None:
        """Register external module items as opaque values."""
        mod_path = '.'.join(decl.module_path)
        for item in decl.items:
            if item.is_type:
                continue
            fq = f'{mod_path}.{item.name}'
            # Core.PLAN operations map directly to PLAN opcode pins.
            if fq in self._CORE_PLAN_OPCODES:
                stub = P(N(self._CORE_PLAN_OPCODES[fq]))
            else:
                # All other externals: opaque sentinel pin (placeholder).
                stub = P(N(encode_name(fq)))
            self.compiled[fq] = stub
            self.env.globals[fq] = stub

    # -----------------------------------------------------------------------
    # Effect declarations → CPS dispatch laws
    # -----------------------------------------------------------------------

    def _register_eff(self, decl: DeclEff) -> None:
        """
        Compile each effect operation as a 3-arg CPS law.

        Each op compiles to: L(3, name, dispatch(tag, op_arg, k))
        where the law body is:
          N(1)=op_arg, N(2)=dispatch, N(3)=k
          body = bapp(bapp(bapp(N(2), tag_val), N(1)), N(3))
               = dispatch(tag, op_arg, k)

        Calling `E.op arg` produces A(op_law, arg) — a 2-arg partial
        application that is the CPS computation value for that operation.
        """
        fq_eff = f'{self.module}.{decl.name}'
        dummy_env = Env(globals=self.env.globals, arity=3)
        for tag, op in enumerate(decl.ops):
            fq_op = f'{fq_eff}.{op.name}'
            # Tag literal inside a 3-arg law body: quote if tag <= 3
            tag_val = self._compile_nat_literal(tag, dummy_env)
            # Body: dispatch(tag, op_arg, k)
            # N(2)=dispatch, tag_val, N(1)=op_arg, N(3)=k
            body = bapp(bapp(bapp(N(2), tag_val), N(1)), N(3))
            name_nat = encode_name(op.name)
            val = L(3, name_nat, body)
            self.compiled[fq_op] = val
            self.env.globals[fq_op] = val
            # Also register under short name for same-module lookup
            self.env.globals[op.name] = val
            # Record tag for dispatch_fn construction
            self.effect_op_tags[fq_op] = tag
            self.effect_op_tags[op.name] = tag

    # -----------------------------------------------------------------------
    # Typeclass declarations
    # -----------------------------------------------------------------------

    def _compile_class(self, decl: DeclClass) -> None:
        """Register typeclass method names (no PLAN value emitted for the class itself)."""
        fq_cls = f'{self.module}.{decl.name}'
        methods = [m.name for m in decl.members if isinstance(m, ClassMember)]
        self._class_methods[fq_cls] = methods

    def _compile_inst(self, decl: DeclInst) -> None:
        """Compile instance methods and emit named dict values.

        Naming convention:
          Module.inst_ClassName_TypeKey_method  — individual method law
          Module.inst_ClassName_TypeKey          — dict (= method law for single-method class)
        """
        class_fq = f'{self.module}.{decl.class_name}'
        type_key = self._typearg_key(decl.type_args[0]) if decl.type_args else 'Unknown'

        methods = self._class_methods.get(class_fq, [])
        compiled_methods: dict[str, Any] = {}

        for member in decl.members:
            if not isinstance(member, InstanceMember):
                continue
            env = Env(globals=self.env.globals, arity=0)
            # Instance methods may reference earlier lets (e.g., recursive via
            # the module-level self-ref mechanism is not needed here; the body
            # is a standalone expression).
            val = self._compile_expr(member.body, env, name_hint=member.name)
            method_fq = f'{self.module}.inst_{decl.class_name}_{type_key}_{member.name}'
            self.compiled[method_fq] = val
            self.env.globals[method_fq] = val
            compiled_methods[member.name] = val

        # For single-method classes: the dict IS the one method.
        # For multi-method classes: each method has its own named law; the
        # dict FQ is not emitted as a bundle (methods are passed flat).
        ordered_vals = [compiled_methods[m] for m in methods if m in compiled_methods]
        if len(ordered_vals) == 1:
            dict_fq = f'{self.module}.inst_{decl.class_name}_{type_key}'
            self.compiled[dict_fq] = ordered_vals[0]
            self.env.globals[dict_fq] = ordered_vals[0]

    def _typearg_key(self, type_arg: Any) -> str:
        """Convert a type argument AST node to a stable string key for instance names."""
        if isinstance(type_arg, str):
            return type_arg
        if hasattr(type_arg, 'name'):   # TyCon, TyVar, TyApp with .name
            return str(type_arg.name)
        if hasattr(type_arg, 'fun'):    # TyApp
            return self._typearg_key(type_arg.fun) + '_' + self._typearg_key(type_arg.arg)
        return str(type_arg)

    def _extract_param_types(self, ty: Any) -> list[str | None] | None:
        """Extract ordered parameter type keys from a function type annotation.

        Returns a list like ["Nat", "Nat", None] for `Nat → Nat → a → ...`.
        Returns None if the annotation is not a function type or has no params.
        Only resolves concrete type constructors; type variables become None.
        """
        from bootstrap.ast import TyForall, TyArr as AstTyArr, TyCon as AstTyCon
        # Peel forall/constrained wrappers
        while ty is not None:
            if isinstance(ty, TyConstrained):
                ty = ty.ty
            elif isinstance(ty, TyForall):
                ty = ty.body
            else:
                break
        if ty is None:
            return None
        result = []
        while isinstance(ty, AstTyArr):
            dom = ty.from_
            if isinstance(dom, AstTyCon):
                result.append(dom.name)
            else:
                result.append(None)
            ty = ty.to_
        return result if result else None

    def _extract_constraints(self, ty: Any) -> list:
        """Extract constraint list from a (possibly nested) type annotation.

        Unwraps TyForall, TyConstrained wrappers.
        Returns list of (class_name_short, type_args_list) tuples.
        """
        from bootstrap.ast import TyForall
        constraints = []
        # Peel TyForall and TyConstrained layers
        while ty is not None:
            if isinstance(ty, TyConstrained):
                constraints.extend(ty.constraints)
                ty = ty.ty
            elif isinstance(ty, TyForall):
                ty = ty.body
            else:
                break
        return constraints

    # -----------------------------------------------------------------------
    # Let declarations
    # -----------------------------------------------------------------------

    def _compile_let(self, decl: DeclLet) -> None:
        """Compile a top-level let declaration."""
        fq = f'{self.module}.{decl.name}'
        env = Env(globals=self.env.globals, arity=0)
        env.self_ref_name = fq

        # Check for typeclass constraints in the type annotation
        constraints = self._extract_constraints(decl.type_ann) if decl.type_ann is not None else []

        if constraints:
            val = self._compile_constrained_let(decl, fq, env, constraints)
            # Record for call-site dict insertion
            constraint_info = []
            for class_short, _type_args in constraints:
                class_fq = f'{self.module}.{class_short}'
                methods = self._class_methods.get(class_fq, [class_short])
                method_fqs = [f'{self.module}.{m}' for m in methods]
                constraint_info.append((class_fq, method_fqs))
            self._constrained_lets[fq] = constraint_info
        else:
            # Extract param type hints from the type annotation for constrained call-site inference
            param_types = self._extract_param_types(decl.type_ann) if decl.type_ann is not None else None
            if param_types and isinstance(decl.body, ExprLam):
                user_params = self._flatten_params(decl.body)
                body_expr = self._lambda_body(decl.body)
                name_nat = encode_name(decl.name)
                val = self._compile_lam_as_law(user_params, body_expr, env, decl.name,
                                               param_types=param_types)
            else:
                val = self._compile_expr(decl.body, env, name_hint=decl.name)

        self.compiled[fq] = val
        self.env.globals[fq] = val

    def _compile_constrained_let(self, decl: DeclLet, fq: str, env: Env,
                                  constraints: list) -> Any:
        """Compile a let declaration that has typeclass constraints.

        Adds one leading parameter per method per constraint (flat dict passing).
        Inside the body, class method FQ names are bound to their dict params.
        """
        # Collect dict param FQ names (one per method per constraint, in order)
        dict_param_fqs: list[str] = []
        for class_short, _type_args in constraints:
            class_fq = f'{self.module}.{class_short}'
            methods = self._class_methods.get(class_fq, [class_short])
            for m in methods:
                dict_param_fqs.append(f'{self.module}.{m}')

        # Extract user params and body from the body expression
        if isinstance(decl.body, ExprLam):
            user_params = self._flatten_params(decl.body)
            body_expr = self._lambda_body(decl.body)
        else:
            user_params = []
            body_expr = decl.body

        # Build body_env: dict params first (bound by method FQ name), then user params
        body_env = Env(globals=env.globals, arity=0, self_ref_name=fq)
        for method_fq in dict_param_fqs:
            body_env = body_env.bind_param(method_fq)
        for pat in user_params:
            param_name = self._pat_var_name(pat)
            body_env = body_env.bind_param(param_name)

        body_val = self._compile_expr(body_expr, body_env, fq)
        total_arity = len(dict_param_fqs) + len(user_params)
        name_nat = encode_name(decl.name)
        return L(total_arity, name_nat, body_val)

    # -----------------------------------------------------------------------
    # Expression compilation
    # -----------------------------------------------------------------------

    def _compile_expr(self, expr: Any, env: Env, name_hint: str = '') -> Any:
        """Compile an expression to a PLAN value."""
        if isinstance(expr, ExprNat):
            return self._compile_nat_literal(expr.value, env)

        if isinstance(expr, (ExprText, ExprBytes, ExprHexBytes)):
            return self._compile_bytes_literal(expr, env)

        if isinstance(expr, ExprUnit):
            # Unit = 0
            return self._compile_nat_literal(0, env)

        if isinstance(expr, ExprVar):
            return self._compile_var(expr, env)

        if isinstance(expr, ExprAnn):
            return self._compile_expr(expr.expr, env, name_hint)

        if isinstance(expr, ExprApp):
            return self._compile_app(expr, env, name_hint)

        if isinstance(expr, ExprLam):
            return self._compile_lam(expr, env, name_hint)

        if isinstance(expr, ExprIf):
            return self._compile_if(expr, env, name_hint)

        if isinstance(expr, ExprMatch):
            return self._compile_match(expr, env, name_hint)

        if isinstance(expr, ExprLet):
            return self._compile_local_let(expr, env, name_hint)

        if isinstance(expr, ExprPin):
            return self._compile_expr_pin(expr, env, name_hint)

        if isinstance(expr, ExprOp):
            return self._compile_op(expr, env, name_hint)

        if isinstance(expr, ExprUnary):
            return self._compile_unary(expr, env, name_hint)

        if isinstance(expr, ExprTuple):
            return self._compile_tuple(expr, env, name_hint)

        if isinstance(expr, ExprFix):
            return self._compile_fix(expr, env, name_hint)

        if isinstance(expr, ExprHandle):
            return self._compile_handle(expr, env, name_hint)

        if isinstance(expr, ExprDo):
            return self._compile_do(expr, env, name_hint)

        raise CodegenError(
            f'codegen: unsupported expression {type(expr).__name__}'
        )

    def _compile_nat_literal(self, value: int, env: Env) -> Any:
        """
        Compile a nat literal.

        In a law body (env.arity > 0), nats 0..arity are de Bruijn indices,
        so embedding N(k) directly would cause kal to misread it as a parameter
        reference.  We use the PLAN quote form A(N(0), N(k)) instead:
          kal evaluates A(N(0), N(k)) via the (0 x) = quote branch → returns N(k)

        Outside a law body, N(value) is safe.
        """
        if env.arity > 0 and value <= env.arity:
            return A(N(0), N(value))   # quote form: returns literal nat value
        return N(value)

    def _compile_bytes_literal(self, expr: Any, env: Env) -> Any:
        """Compile a bytes/text literal to a nat (little-endian encoding)."""
        if isinstance(expr, ExprText):
            if isinstance(expr.value, str):
                b = expr.value.encode('utf-8')
            else:
                # interpolated — not supported in bootstrap codegen
                raise CodegenError('codegen: interpolated strings not supported')
        elif isinstance(expr, (ExprBytes, ExprHexBytes)):
            b = expr.value
        else:
            b = b''
        value = int.from_bytes(b, 'little') if b else 0
        return self._compile_nat_literal(value, env)

    def _compile_var(self, expr: ExprVar, env: Env) -> Any:
        """Compile a variable reference."""
        fq = str(expr.name)

        # Self-reference: inside a law body, the function's own FQ name → N(0)
        if env.self_ref_name and env.arity > 0:
            if fq == env.self_ref_name or fq.split('.')[-1] == env.self_ref_name.split('.')[-1]:
                return N(0)

        # Local de Bruijn reference
        if fq in env.locals:
            idx = env.locals[fq]
            # Inside a law body, de Bruijn index is just the nat itself
            return N(idx)

        # Global reference (top-level let or constructor)
        if fq in env.globals:
            val = env.globals[fq]
            if isinstance(val, _MutualRef):
                if env.arity > 0:
                    # Extract law_j from the shared row and partially apply it
                    # to the shared row so callers only see the original arity.
                    # The shared row is at env.locals['__shared__'] (index s_idx).
                    # j_val is the literal nat j (using quote form to avoid de Bruijn collision)
                    # bapp(N(s_idx), j_val) = (shared_row j) = law_j
                    # bapp(law_j, N(s_idx)) = law_j shared_row  (1 arg consumed)
                    s_idx = env.locals.get('__shared__', 1)
                    j_val = self._compile_nat_literal(val.index, env)
                    return bapp(bapp(N(s_idx), j_val), N(s_idx))
                else:
                    raise CodegenError(
                        f'codegen: mutual SCC reference {fq!r} used outside a law body'
                    )
            if env.arity == 0:
                # At top level: return the value directly
                return val
            else:
                # Inside a law body: nat globals (True=1, False=0, nullary constructors)
                # must use the quote form A(N(0), N(k)) so kal returns the bare nat k,
                # not a pinned version that breaks Case_ dispatch.
                if is_nat(val):
                    return self._compile_nat_literal(val, env)
                return P(val) if not is_pin(val) else val

        # Last resort: try with module prefix stripped (for builtins)
        short = fq.split('.')[-1]
        if short in env.globals:
            val = env.globals[short]
            if isinstance(val, _MutualRef):
                if env.arity > 0:
                    s_idx = env.locals.get('__shared__', 1)
                    j_val = self._compile_nat_literal(val.index, env)
                    return bapp(bapp(N(s_idx), j_val), N(s_idx))
                else:
                    raise CodegenError(
                        f'codegen: mutual SCC reference {short!r} used outside a law body'
                    )
            if env.arity == 0:
                return val
            if is_nat(val):
                return self._compile_nat_literal(val, env)
            return P(val) if not is_pin(val) else val

        raise CodegenError(f'codegen: unbound variable {fq!r}')

    def _compile_app(self, expr: ExprApp, env: Env, name_hint: str) -> Any:
        """Compile function application.

        If the outermost function is a constrained let applied to its first
        user argument (bare ExprVar call), insert the resolved dict args first.
        """
        # Detect: ExprApp(ExprVar(constrained_fn), first_user_arg)
        # We check if the direct function (not a chain) is a bare constrained let.
        # Walk to the root of the application chain.
        root = expr.fun
        while isinstance(root, ExprApp):
            root = root.fun
        if isinstance(root, ExprVar):
            root_fq = str(root.name)
            if root_fq in self._constrained_lets:
                return self._compile_constrained_app(expr, env, name_hint)

        fn_val = self._compile_expr(expr.fun, env)
        arg_val = self._compile_expr(expr.arg, env)

        if env.arity == 0:
            # At top level: build a direct App node
            return A(fn_val, arg_val)
        else:
            # Inside a law body: use bapp notation (0 f x)
            return bapp(fn_val, arg_val)

    def _compile_constrained_app(self, expr: ExprApp, env: Env, name_hint: str) -> Any:
        """Compile a call to a constrained function, auto-inserting dict args.

        Unwraps the full application chain f a1 a2 ... aN, inserts dict args
        between f and the user args, then recompiles the full chain.
        """
        # Unwrap application chain: collect args, find root function
        args: list = []
        node: Any = expr
        while isinstance(node, ExprApp):
            args.append(node.arg)
            node = node.fun
        args.reverse()
        fn_expr = node  # ExprVar(constrained_fn_fq)
        fq = str(fn_expr.name)

        constraint_info = self._constrained_lets[fq]  # [(class_fq, [method_fq, ...])]

        # Infer type key from the first user arg (for instance lookup)
        type_key = self._infer_type_key(args[0], env) if args else None

        # Compile function reference
        fn_val = self._compile_global_ref(fq, env)
        result = fn_val

        # Apply dict args (one per method per constraint)
        for class_fq, method_fqs in constraint_info:
            class_short = class_fq.split('.')[-1]
            if type_key is None:
                raise CodegenError(
                    f'codegen: cannot determine instance type for constraint {class_short!r} '
                    f'at call to {fq!r} — use explicit dict passing'
                )
            for method_fq in method_fqs:
                method_short = method_fq.split('.')[-1]
                inst_method_key = f'{self.module}.inst_{class_short}_{type_key}_{method_short}'
                inst_dict_key = f'{self.module}.inst_{class_short}_{type_key}'
                if inst_method_key in self.env.globals:
                    dict_val = self._compile_global_ref(inst_method_key, env)
                elif inst_dict_key in self.env.globals:
                    dict_val = self._compile_global_ref(inst_dict_key, env)
                else:
                    raise CodegenError(
                        f'codegen: no instance {class_short} {type_key} '
                        f'(looked for {inst_method_key!r})'
                    )
                if env.arity == 0:
                    result = A(result, dict_val)
                else:
                    result = bapp(result, dict_val)

        # Apply user args
        for arg_expr in args:
            arg_val = self._compile_expr(arg_expr, env)
            if env.arity == 0:
                result = A(result, arg_val)
            else:
                result = bapp(result, arg_val)

        return result

    def _compile_global_ref(self, fq: str, env: Env) -> Any:
        """Compile a reference to a global value, respecting body vs. top-level context."""
        val = self.env.globals.get(fq)
        if val is None:
            raise CodegenError(f'codegen: unknown global {fq!r}')
        if env.arity == 0:
            return val
        if is_nat(val):
            return self._compile_nat_literal(val, env)
        return P(val) if not is_pin(val) else val

    def _infer_type_key(self, expr: Any, env: Env) -> str | None:
        """Heuristically determine the type key of an expression for instance lookup.

        Covers: literal Nats/Text, locals with tracked param types, explicit type
        annotations, and some global value checks.
        Returns None if the type cannot be determined.
        """
        if isinstance(expr, ExprNat):
            return 'Nat'
        if isinstance(expr, ExprText):
            return 'Text'
        if isinstance(expr, ExprVar):
            fq = str(expr.name)
            # Check env.param_types (set from type annotations on outer let declarations)
            short = fq.split('.')[-1]
            if short in env.param_types:
                return env.param_types[short]
            if fq in env.param_types:
                return env.param_types[fq]
            # Check if it's a global with known Nat value
            val = self.env.globals.get(fq)
            if val is not None and is_nat(val):
                return 'Nat'
        if isinstance(expr, ExprAnn):
            # Explicit type annotation: (expr : Type)
            from bootstrap.ast import TyCon as AstTyCon
            if isinstance(expr.ty, AstTyCon):
                return expr.ty.name
            return self._infer_type_key(expr.expr, env)
        return None

    def _compile_lam(self, expr: ExprLam, env: Env, name_hint: str) -> Any:
        """
        Compile a lambda expression.

        If the lambda is in a top-level context (env.arity == 0) and we have
        a name_hint, compile to a named law with lambda lifting.

        If the lambda is nested, lambda-lift it: collect free variables, add
        them as extra parameters, and return a partial application at the call site.
        """
        # Collect all parameters (may be multi-param lambda after desugaring)
        params = self._flatten_params(expr)
        # Descend into nested lambdas to find the true body
        body_expr = self._lambda_body(expr)

        if env.arity == 0:
            # Top-level lambda → named law
            return self._compile_lam_as_law(params, body_expr, env, name_hint)
        else:
            # Nested lambda → lambda lifting
            return self._compile_lam_lifted(params, body_expr, env, name_hint)

    def _flatten_params(self, expr: ExprLam) -> list:
        """Collect all param patterns of a multi-param lambda."""
        params = list(expr.params)
        inner = expr.body
        while isinstance(inner, ExprLam):
            params.extend(inner.params)
            inner = inner.body
        return params

    def _lambda_body(self, expr: ExprLam) -> Any:
        """Descend into nested lambdas to find the innermost body."""
        inner = expr.body
        while isinstance(inner, ExprLam):
            inner = inner.body
        return inner

    def _compile_lam_as_law(self, params: list, body_expr: Any, env: Env, name_hint: str,
                             param_types: list | None = None) -> Any:
        """Compile params + body as a top-level (named) law.

        param_types: optional list of type-key strings (one per param, in order).
        When provided, param type info is recorded in the body env for constrained
        call-site dict insertion.
        """
        # Build body env with params bound
        body_env = Env(globals=env.globals, arity=0, self_ref_name=env.self_ref_name,
                       param_types=dict(env.param_types))
        for i, pat in enumerate(params):
            param_name = self._pat_var_name(pat)
            if param_types and i < len(param_types) and param_types[i] is not None:
                body_env = body_env.bind_param_typed(param_name, param_types[i])
            else:
                body_env = body_env.bind_param(param_name)

        body_val = self._compile_expr(body_expr, body_env, name_hint)
        name_nat = encode_name(name_hint) if name_hint else 0
        return L(len(params), name_nat, body_val)

    def _compile_lam_lifted(self, params: list, body_expr: Any, env: Env, name_hint: str) -> Any:
        """
        Lambda-lift a nested lambda.

        Strategy:
        1. Find free variables in the lambda body (variables in env.locals
           not bound by the new params).
        2. Create a new law with (free_vars + params) as parameters.
        3. Return a partial application of that law to the free vars.
        """
        param_names = [self._pat_var_name(p) for p in params]
        free_vars = self._free_vars(body_expr, set(param_names), env)

        # Build the lifted law's env: free vars first (leading params), then own params
        lifted_env = Env(globals=env.globals, arity=0)
        for fv in free_vars:
            lifted_env = lifted_env.bind_param(fv)
        for pn in param_names:
            lifted_env = lifted_env.bind_param(pn)

        body_val = self._compile_expr(body_expr, lifted_env)
        name_nat = encode_name(name_hint) if name_hint else 0
        lifted_law = L(len(free_vars) + len(params), name_nat, body_val)

        # At the call site, partially apply the lifted law to the free vars.
        # In a law body, we reference free vars by their de Bruijn indices from env.locals.
        if not free_vars:
            # No captures: just return the law (as a pin to avoid de Bruijn collision)
            return P(lifted_law)

        # Apply captured values using bapp
        # The lifted law itself must be wrapped (it's a literal value in a law body)
        result = P(lifted_law)
        for fv in free_vars:
            fv_ref = N(env.locals[fv])  # de Bruijn index in the *outer* env
            result = bapp(result, fv_ref)
        return result

    def _free_vars(self, expr: Any, bound: set, env: Env) -> list:
        """
        Compute free variables in expr that appear in env.locals but not in bound.
        Returns a list in env.locals key order (for deterministic lambda lifting).
        """
        found = set()
        self._collect_free(expr, bound, env, found)
        # Return in the order they appear in env.locals
        return [k for k in env.locals if k in found]

    def _collect_free(self, expr: Any, bound: set, env: Env, found: set) -> None:
        """Recursively collect free variable names."""
        if isinstance(expr, ExprVar):
            name = str(expr.name)
            if name in env.locals and name not in bound:
                found.add(name)
            # If the name resolves to a _MutualRef in globals, the expression
            # implicitly uses __shared__ (the shared row).  Mark it as free so
            # lambda-lifting captures it correctly.
            fq = name
            resolved_val = env.globals.get(fq) or env.globals.get(name.split('.')[-1])
            if isinstance(resolved_val, _MutualRef) and '__shared__' in env.locals and '__shared__' not in bound:
                found.add('__shared__')
        elif isinstance(expr, ExprApp):
            self._collect_free(expr.fun, bound, env, found)
            self._collect_free(expr.arg, bound, env, found)
        elif isinstance(expr, ExprLam):
            new_bound = set(bound)
            for p in expr.params:
                pn = self._pat_var_name(p)
                if pn:
                    new_bound.add(pn)
            self._collect_free(expr.body, new_bound, env, found)
        elif isinstance(expr, ExprIf):
            self._collect_free(expr.cond, bound, env, found)
            self._collect_free(expr.then_, bound, env, found)
            self._collect_free(expr.else_, bound, env, found)
        elif isinstance(expr, ExprMatch):
            self._collect_free(expr.scrutinee, bound, env, found)
            for pat, guard, body in expr.arms:
                arm_bound = set(bound) | self._pat_binds(pat)
                self._collect_free(body, arm_bound, env, found)
        elif isinstance(expr, ExprLet):
            self._collect_free(expr.rhs, bound, env, found)
            new_bound = set(bound)
            pn = self._pat_var_name(expr.pattern)
            if pn:
                new_bound.add(pn)
            self._collect_free(expr.body, new_bound, env, found)
        elif isinstance(expr, ExprOp):
            self._collect_free(expr.lhs, bound, env, found)
            self._collect_free(expr.rhs, bound, env, found)
        elif isinstance(expr, ExprUnary):
            self._collect_free(expr.operand, bound, env, found)
        elif isinstance(expr, ExprAnn):
            self._collect_free(expr.expr, bound, env, found)
        elif isinstance(expr, ExprTuple):
            for e in expr.elems:
                self._collect_free(e, bound, env, found)
        elif isinstance(expr, ExprFix):
            lam = expr.lam
            all_params = self._flatten_params(lam)
            body = self._lambda_body(lam)
            new_bound = set(bound)
            for p in all_params:
                pn = self._pat_var_name(p)
                if pn:
                    new_bound.add(pn)
            self._collect_free(body, new_bound, env, found)
        elif isinstance(expr, ExprHandle):
            self._collect_free(expr.comp, bound, env, found)
            for arm in expr.arms:
                if isinstance(arm, HandlerReturn):
                    arm_bound = set(bound) | self._pat_binds(arm.pattern)
                    self._collect_free(arm.body, arm_bound, env, found)
                elif isinstance(arm, HandlerOp):
                    arm_bound = set(bound) | {arm.resume}
                    for p in arm.arg_pats:
                        arm_bound |= self._pat_binds(p)
                    self._collect_free(arm.body, arm_bound, env, found)
        elif isinstance(expr, ExprDo):
            self._collect_free(expr.rhs, bound, env, found)
            new_bound = set(bound) | {expr.name}
            self._collect_free(expr.body, new_bound, env, found)

    def _pat_binds(self, pat: Any) -> set:
        """Collect names bound by a pattern."""
        if isinstance(pat, PatVar):
            return {pat.name}
        if isinstance(pat, PatCon):
            result = set()
            for p in pat.args:
                result |= self._pat_binds(p)
            return result
        if isinstance(pat, PatTuple):
            result = set()
            for p in pat.pats:
                result |= self._pat_binds(p)
            return result
        return set()

    def _pat_var_name(self, pat: Any) -> str:
        """Extract the variable name from a simple pattern."""
        if isinstance(pat, PatVar):
            return pat.name
        if isinstance(pat, PatWild):
            return '_wild'
        return '_pat'

    # -----------------------------------------------------------------------
    # if/then/else
    # -----------------------------------------------------------------------

    # Shared helper values embedded in compiled laws.
    # identity: L(1, 0, N(1)) — returns its argument
    # const2:   L(2, 0, N(1)) — takes 2 args, returns first
    _ID_LAW = L(1, 0, N(1))
    _CONST2_LAW = L(2, 0, N(1))

    def _compile_if(self, expr: ExprIf, env: Env, name_hint: str) -> Any:
        """
        Compile if/then/else using Bool's nat encoding (False=0, True=1).

        Uses opcode 3 (P(N(3))), which is the Case_ dispatch opcode.
        Op 3 takes 6 separate arguments: (p, l, a, z, m, o)
        where p,l,a handle pin/law/app cases (unused for Bool/Nat),
        z is the zero (False) branch, m is the succ function (receives pred),
        and o is the scrutinee.

        In law body context, de Bruijn refs inside plain App nodes are NOT
        resolved by kal.  We use bapp chains to force resolution:
          bapp(f, x) = A(A(N(0), f), x)
          kal evaluates this as apply(kal(f), kal(x))
        """
        cond_body = self._compile_expr(expr.cond, env)
        then_body = self._compile_expr(expr.then_, env, name_hint + '_then')
        else_body = self._compile_expr(expr.else_, env, name_hint + '_else')

        id_pin = P(self._ID_LAW)
        const2_pin = P(self._CONST2_LAW)

        if env.arity == 0:
            # Top-level: apply P(N(3)) to 6 separate args directly.
            const_then = A(const2_pin, then_body)
            return A(A(A(A(A(A(P(N(3)), id_pin), id_pin), id_pin),
                        else_body), const_then), cond_body)
        else:
            # Law body: use bapp chains so de Bruijn refs are resolved by kal.
            const_then_body = bapp(const2_pin, then_body)
            step = P(N(3))
            step = bapp(step, id_pin)           # p
            step = bapp(step, id_pin)           # l
            step = bapp(step, id_pin)           # a
            step = bapp(step, else_body)        # z (False branch)
            step = bapp(step, const_then_body)  # m (True branch fn)
            step = bapp(step, cond_body)        # o (scrutinee)
            return step

    # -----------------------------------------------------------------------
    # Pattern matching
    # -----------------------------------------------------------------------

    def _compile_match(self, expr: ExprMatch, env: Env, name_hint: str) -> Any:
        """
        Compile a match expression.

        Handles:
        - Matching on Nat literals (0, 1, ...) using opcode 2 (nat iteration)
        - Matching on Bool (True/False = nat 1/0) — same as Nat
        - Matching on algebraic type constructors (tagged rows)
        - Wildcard / variable patterns
        """
        scrutinee = self._compile_expr(expr.scrutinee, env)
        arms = expr.arms

        # Classify the match based on the first meaningful pattern
        first_pat = arms[0][0] if arms else None

        if self._is_nat_match(arms):
            return self._compile_nat_match(scrutinee, arms, env, name_hint)
        elif self._is_con_match(arms):
            return self._compile_con_match(scrutinee, arms, env, name_hint)
        elif self._is_tuple_match(arms):
            return self._compile_tuple_match(scrutinee, arms, env, name_hint)
        else:
            # Wildcard or variable match: just bind the scrutinee
            return self._compile_fallback_match(scrutinee, arms, env, name_hint)

    def _is_nat_match(self, arms) -> bool:
        """True if any arm has a PatNat pattern."""
        for pat, _, _ in arms:
            if isinstance(pat, PatNat):
                return True
        return False

    def _is_con_match(self, arms) -> bool:
        """True if any arm has a PatCon pattern."""
        for pat, _, _ in arms:
            if isinstance(pat, PatCon):
                return True
        return False

    def _compile_nat_match(self, scrutinee: Any, arms: list, env: Env, name_hint: str) -> Any:
        """
        Compile match on Nat patterns using opcode 2 (nat iteration).

        Opcode 2: (2 zero_case succ_fn n)
        - if n == 0 → zero_case
        - if n == k+1 → apply succ_fn to k

        For matching { 0 -> e0 | _ -> e1 }:
          (2 e0 (λ prev -> e1) scrutinee)

        For matching { 0 -> e0 | 1 -> e1 | _ -> e2 }:
          (2 e0 (λ prev -> (2 e1 (λ prev2 -> e2) prev)) scrutinee)
        """
        # Sort arms: nat arms first (by tag), then wildcard
        nat_arms = [(pat.value, body) for pat, _, body in arms if isinstance(pat, PatNat)]
        wild_arm = next(((pat, body) for pat, _, body in arms
                        if isinstance(pat, (PatWild,)) or
                        (isinstance(pat, PatVar))), None)
        wild_body = wild_arm[1] if wild_arm else None
        wild_var = wild_arm[0].name if (wild_arm and isinstance(wild_arm[0], PatVar)) else None

        # Build a ladder of opcode 2 calls
        op2 = P(N(2))

        # Find max tag used
        if not nat_arms:
            # Only wildcard
            return self._compile_expr(wild_body, env, name_hint)

        max_tag = max(v for v, _ in nat_arms)

        def build_ladder(current_n: int, pred_var: str | None, outer_env: Env) -> Any:
            """Build the nested op2 ladder starting from current_n."""
            # Find arm for current_n
            arm_body = next((body for v, body in nat_arms if v == current_n), None)
            if arm_body is None:
                arm_body = wild_body

            # Build the zero case (when remaining tag == 0)
            if arm_body is not None:
                # Bind pred_var if wildcard arm refers to it
                if pred_var is not None and wild_arm and wild_var:
                    arm_env = outer_env.child()
                    # pred_var is bound to the predecessor in the succ context
                    # For now we don't bind it (bootstrap limitation)
                arm_env = outer_env
                zero_val = self._compile_expr(arm_body, arm_env, name_hint + f'_{current_n}')
            else:
                zero_val = N(0)  # unreachable

            if current_n >= max_tag:
                # Base case: just return the zero val
                return zero_val

            # Succ case: build a const law that ignores the predecessor and recurses
            next_val = build_ladder(current_n + 1, pred_var, outer_env)

            if outer_env.arity == 0:
                succ_fn = self._make_const_law(next_val, name_hint + f'_succ{current_n}')
                return A(A(A(op2, zero_val), succ_fn), scrutinee if current_n == 0 else N(0))
            else:
                succ_fn = self._make_const_law_body(next_val, outer_env, name_hint + f'_succ{current_n}')
                inner_scrutinee = scrutinee if current_n == 0 else N(1)  # prev predecessor
                return bapp(bapp(bapp(op2, zero_val), succ_fn), inner_scrutinee)

        if env.arity == 0:
            result = build_ladder(0, None, env)
            # The ladder at the outermost level uses `scrutinee` already
            # but build_ladder doesn't automatically thread it; fix:
            return self._nat_match_top(nat_arms, wild_body, wild_var, scrutinee, op2, env, name_hint)
        else:
            return self._nat_match_body(nat_arms, wild_body, wild_var, scrutinee, op2, env, name_hint)

    def _make_op2_dispatch(self, zero_val, succ_body, scrutinee_body, env: Env) -> Any:
        """
        Build a Case_ dispatch: if scrutinee==0 return zero_val, else apply succ_body to pred.

        Uses opcode 3 (P(N(3)) = Case_) with 6 separate args: (p, l, a, z, m, o).
        p/l/a handlers are identity (unused for Nat scrutinee).
        """
        id_pin = P(self._ID_LAW)
        if env.arity == 0:
            return A(A(A(A(A(A(P(N(3)), id_pin), id_pin), id_pin),
                        zero_val), succ_body), scrutinee_body)
        else:
            step = P(N(3))
            step = bapp(step, id_pin)
            step = bapp(step, id_pin)
            step = bapp(step, id_pin)
            step = bapp(step, zero_val)
            step = bapp(step, succ_body)
            step = bapp(step, scrutinee_body)
            return step

    def _build_nat_dispatch(self, arms_sorted, wild_body, wild_var, scrutinee, env, name_hint):
        """
        Build a nat/con dispatch using opcode 2.

        For a single arm: op2(zero_val, const2(wild_or_0), scrutinee)
        For multiple arms: op2(arm0_val, succ_law_1, scrutinee)
          where succ_law_1 = L(1, 0, op2(arm1_val, succ_law_2, N(1)))
          and N(1) inside each succ law is the predecessor (de Bruijn index 1).

        If wild_var is not None (PatVar wildcard), the wildcard arm gets the
        predecessor bound to wild_var via _make_pred_succ_law.

        This ensures each level dispatches on the PREDECESSOR passed by op2,
        not on the original scrutinee.  The arm bodies compiled in outer env
        (may reference outer lambda params); the succ laws are compiled in a
        fresh arity-1 env (bootstrap limitation: arm bodies must not reference
        outer lambda params — they are recompiled inside the succ law).
        """
        const2_pin = P(self._CONST2_LAW)

        def make_succ_law(idx: int) -> Any:
            """L(1, 0, dispatch(idx, pred=N(1))) — takes predecessor, dispatches."""
            pred_env = Env(globals=env.globals, arity=1)
            body = dispatch(idx, N(1), pred_env)
            return P(L(1, 0, body))

        def dispatch(idx: int, scr: Any, cur_env: Env) -> Any:
            """Build op2 dispatch for arm[idx], scrutinee=scr, in cur_env."""
            tag, body_expr = arms_sorted[idx]
            zero_val = self._compile_expr(body_expr, cur_env, f'{name_hint}_{tag}')

            if idx + 1 < len(arms_sorted):
                # More named arms: succ law dispatches on predecessor
                succ = make_succ_law(idx + 1)
            elif wild_body is not None:
                if wild_var is not None and cur_env is env:
                    # PatVar wildcard at outer level: use proper predecessor binding
                    succ = self._make_pred_succ_law(wild_body, wild_var, cur_env, f'{name_hint}_wild')
                else:
                    wild_val = self._compile_expr(wild_body, cur_env, f'{name_hint}_wild')
                    # const2(wild_val): ignores predecessor, returns wild_val
                    if cur_env.arity > 0:
                        succ = bapp(const2_pin, wild_val)
                    else:
                        succ = A(const2_pin, wild_val)
            else:
                # No wildcard: unreachable; return 0
                fallback = A(N(0), N(0)) if cur_env.arity > 0 else N(0)
                if cur_env.arity > 0:
                    succ = bapp(const2_pin, fallback)
                else:
                    succ = A(const2_pin, N(0))

            return self._make_op2_dispatch(zero_val, succ, scr, cur_env)

        if not arms_sorted:
            if wild_body is not None:
                return self._compile_expr(wild_body, env, name_hint)
            return N(0)

        # Outer level: arm[0] with the original scrutinee
        tag0, body0 = arms_sorted[0]
        zero_val0 = self._compile_expr(body0, env, f'{name_hint}_{tag0}')

        if len(arms_sorted) == 1:
            # Single named arm + optional wildcard
            if wild_body is not None:
                if wild_var is not None:
                    succ = self._make_pred_succ_law(wild_body, wild_var, env, f'{name_hint}_wild')
                else:
                    wild_val = self._compile_expr(wild_body, env, f'{name_hint}_wild')
                    succ = bapp(const2_pin, wild_val) if env.arity > 0 else A(const2_pin, wild_val)
            else:
                fallback = A(N(0), N(0)) if env.arity > 0 else N(0)
                succ = bapp(const2_pin, fallback) if env.arity > 0 else A(const2_pin, N(0))
            return self._make_op2_dispatch(zero_val0, succ, scrutinee, env)
        else:
            # Multiple arms: arm[1:] become succ laws that use the predecessor
            succ = make_succ_law(1)
            return self._make_op2_dispatch(zero_val0, succ, scrutinee, env)

    def _nat_match_top(self, nat_arms, wild_body, wild_var, scrutinee, op2, env, name_hint):
        """Build nat match in top-level (non-law-body) context."""
        nat_arms = sorted(nat_arms, key=lambda t: t[0])
        return self._build_nat_dispatch(nat_arms, wild_body, wild_var, scrutinee, env, name_hint)

    def _nat_match_body(self, nat_arms, wild_body, wild_var, scrutinee, op2, env, name_hint):
        """Build nat match inside a law body."""
        nat_arms = sorted(nat_arms, key=lambda t: t[0])
        return self._build_nat_dispatch(nat_arms, wild_body, wild_var, scrutinee, env, name_hint)

    def _compile_con_match(self, scrutinee: Any, arms: list, env: Env, name_hint: str) -> Any:
        """
        Compile match on algebraic type constructors.

        The encoding:
        - Tagged value: (tag field1 field2 ...) where tag is a nat
        - Use opcode 2 on the tag to dispatch
        - Use opcode 1 (reflect) to extract fields from App nodes

        For the bootstrap, we handle:
        - Nullary constructors: scrutinee == tag (bare nat)
        - Unary constructors: scrutinee == A(tag, field)
        - Binary constructors: scrutinee == A(A(tag, field1), field2)

        We build a dispatch using opcode 2 (nat-iteration) on the extracted tag.
        The tag is extracted by "unpeeling" Apps with opcode 1 (reflect).
        """
        # Gather constructor info from arms
        con_arms = []
        wild_arm = None
        for pat, guard, body in arms:
            if isinstance(pat, PatCon):
                fq = str(pat.name)
                info = self.con_info.get(fq)
                if info is None:
                    raise CodegenError(f'codegen: unknown constructor {fq!r}')
                con_arms.append((info, pat.args, body))
            elif isinstance(pat, (PatWild, PatVar)):
                wild_arm = (pat, body)

        if not con_arms:
            return self._compile_fallback_match(scrutinee, arms, env, name_hint)

        # Sort by tag
        con_arms.sort(key=lambda t: t[0].tag)
        max_tag = con_arms[-1][0].tag

        op1 = P(N(1))  # reflect opcode
        op2 = P(N(2))  # nat iteration opcode

        def extract_tag(val: Any) -> Any:
            """
            Extract the outermost nat tag from a tagged value.
            A tagged value is (tag f1 ... fN) = A(A(...A(tag, f1)..., fN)).
            The tag is at the spine root.
            Strategy: use opcode 1 to peel Apps until we hit a Nat.
            For simplicity in bootstrap, assume all constructors in the match
            have the same arity — if they differ, we do the full opcode 1 dance.
            """
            # Opcode 1 takes 6 arguments: (1 pin_f law_f app_f zero nat_pred val)
            # For tag extraction, we need to walk the App spine.
            # Simplified approach: for Bool/simple enums (nullary only), tag = value.
            # For constructors with fields, use opcode 1 to unpack.
            all_nullary = all(info.arity == 0 for info, _, _ in con_arms)
            if all_nullary:
                # Tag IS the value
                return val
            else:
                # Not yet supported: field extraction requires full opcode 1 chain
                # For now, return the value and hope it's nullary (deferred to next milestone)
                return val

        # Build the dispatch
        # For all-nullary: tag == value, use op2 to select branch
        # For mixed/unary: extract tag first, then dispatch

        all_nullary = all(info.arity == 0 for info, _, _ in con_arms)

        if all_nullary:
            # Simple case: scrutinee is a bare nat tag
            tag_val = scrutinee
            wild_body = wild_arm[1] if wild_arm else None
            wild_var_con = wild_arm[0].name if (wild_arm and isinstance(wild_arm[0], PatVar)) else None
            arms_sorted = [(info.tag, body) for info, _, body in con_arms]
            return self._build_nat_dispatch(arms_sorted, wild_body, wild_var_con, tag_val, env, name_hint)
        else:
            # Constructor with fields: basic support
            # For a single-constructor type (like a record/singleton), just bind fields
            if len(con_arms) == 1:
                info, field_pats, body = con_arms[0]
                return self._compile_con_body_extraction(
                    scrutinee, info, field_pats, body, env, name_hint, wild_arm
                )
            else:
                return self._compile_con_match_case3(scrutinee, con_arms, wild_arm, env, name_hint)

    def _compile_con_body_extraction(self, scrutinee, info, field_pats, body, env, name_hint,
                                      wild_arm=None):
        """
        Bind field patterns of a constructor and compile the body.
        Uses _compile_con_match_case3 for single-constructor field extraction.
        wild_arm: the wildcard arm from the enclosing match, if any; passed through so
        non-matching constructors fall to the default instead of returning P(0).
        """
        if info.arity == 0:
            return self._compile_expr(body, env, name_hint)

        # Use a single-arm version of _compile_con_match_case3, preserving wild_arm
        # so that constructors other than `info` dispatch to the wildcard body.
        single_arm = [(info, field_pats, body)]
        return self._compile_con_match_case3(scrutinee, single_arm, wild_arm, env, name_hint)

    def _compile_con_match_case3(self, scrutinee: Any, con_arms, wild_arm, env: Env, name_hint: str) -> Any:
        """
        Compile a multi-constructor match where some constructors have fields.

        Uses Case_ (opcode 3) to dispatch on constructor type:
        - Nat 0: nullary constructor with tag 0
        - Nat k+1: nullary constructor with tag k+1 (via predecessor)
        - App(fun, arg): constructor with fields

        For unary constructors: fun=tag, arg=field.
        For binary constructors: fun=A(tag,field1), arg=field2; extract field1 by
        a nested Case_ on fun.
        """
        id_pin = P(self._ID_LAW)
        const2_pin = P(self._CONST2_LAW)

        # Separate nullary from field-bearing constructors
        nullary_arms = [(info, body) for info, pats, body in con_arms if info.arity == 0]
        field_arms   = [(info, pats, body) for info, pats, body in con_arms if info.arity > 0]
        wild_body    = wild_arm[1] if wild_arm else None
        wild_var_con = wild_arm[0].name if (wild_arm and isinstance(wild_arm[0], PatVar)) else None

        max_field_arity = max((info.arity for info, _, _ in field_arms), default=0)

        # --- Build z and m for nullary constructors ---
        nullary_nat_arms = sorted([(info.tag, body) for info, body in nullary_arms], key=lambda t: t[0])

        # z: fired when scrutinee is Nat 0 (nullary constructor with tag 0)
        z_arm = next((body for tag, body in nullary_nat_arms if tag == 0), None)
        if z_arm is not None:
            z_body = self._compile_expr(z_arm, env, f'{name_hint}_tag0')
        elif wild_body is not None:
            z_body = self._compile_expr(wild_body, env, f'{name_hint}_wild')
        else:
            z_body = body_nat(0, env.arity)

        # m: fired when scrutinee is Nat k+1 (nullary constructors with tag > 0)
        remaining_nullary = [(tag - 1, body) for tag, body in nullary_nat_arms if tag > 0]
        if remaining_nullary:
            # Build a succ law that dispatches on predecessor (= tag - 1)
            pred_env = Env(globals=env.globals, arity=1)
            m_inner_body = self._build_nat_dispatch(
                remaining_nullary, wild_body, wild_var_con, N(1), pred_env, f'{name_hint}_tag_succ'
            )
            m_body = P(L(1, encode_name(f'{name_hint}_m'), m_inner_body))
        elif wild_body is not None:
            wv = self._compile_expr(wild_body, env, f'{name_hint}_wild')
            m_body = A(const2_pin, wv) if env.arity == 0 else bapp(const2_pin, wv)
        else:
            m_body = A(const2_pin, body_nat(0, 0)) if env.arity == 0 else bapp(const2_pin, P(N(0)))

        # --- Build the app handler ---
        if not field_arms:
            app_handler = id_pin
        else:
            app_handler = self._build_app_handler(field_arms, wild_body, wild_var_con, env, name_hint)

        # --- Assemble Case_ dispatch ---
        return self._make_reflect_dispatch(app_handler, z_body, m_body, scrutinee, env)

    def _build_app_handler(self, field_arms, wild_body, wild_var_con, env: Env, name_hint: str) -> Any:
        """
        Build the App-handler law for Case_.

        The law receives (outer_fun, outer_arg) from Case_ when scrutinee is an App.
        - Unary constructors: outer_fun = tag (Nat), outer_arg = field
        - Binary constructors: outer_fun = A(tag, field1), outer_arg = field2

        We lambda-lift free variables from env so the law can reference them.
        """
        const2_pin = P(self._CONST2_LAW)

        # Find free variables in field arm bodies
        all_field_bodies = [body for _, _, body in field_arms]
        all_field_pat_names: set = set()
        for _, pats, _ in field_arms:
            for p in pats:
                if isinstance(p, PatVar):
                    all_field_pat_names.add(p.name)

        # Determine which env.locals are used in arm bodies (excluding field pat names).
        # Include wild_body so its free variables are captured into the handler law.
        combined_names: set = set()
        for b in all_field_bodies:
            self._collect_all_names(b, combined_names)
        if wild_body is not None:
            self._collect_all_names(wild_body, combined_names)
        free_locals = [fv for fv in env.locals
                       if fv in combined_names and fv not in all_field_pat_names]

        # Check if any body uses self-ref
        uses_self = False
        if env.self_ref_name:
            for b in all_field_bodies:
                if self._body_uses_self_ref(b, env):
                    uses_self = True
                    break

        max_arity = max(info.arity for info, _, _ in field_arms)

        # Build handler_env:
        # [self? (at 1)] + [free_locals...] + [outer_fun (n_cap+1)] + [outer_arg (n_cap+2)]
        handler_env = Env(globals=env.globals, arity=0)
        handler_env.self_ref_name = ''

        n_cap = 0
        if uses_self:
            n_cap += 1
            handler_env.arity = n_cap
            handler_env.locals[env.self_ref_name] = n_cap
            short = env.self_ref_name.split('.')[-1]
            handler_env.locals[short] = n_cap

        for fv in free_locals:
            n_cap += 1
            handler_env.arity = n_cap
            handler_env.locals[fv] = n_cap

        fun_idx = n_cap + 1
        arg_idx = n_cap + 2
        handler_env.arity = arg_idx

        if max_arity == 1:
            # Unary: outer_fun = tag (Nat), outer_arg = field
            field_sorted = sorted(field_arms, key=lambda t: t[0].tag)

            if len(field_sorted) == 1:
                # Single field arm: bind field to outer_arg and compile body.
                # When a wildcard is present, add a tag-check on outer_fun so
                # non-matching constructors (including binary ones whose outer_fun
                # is an App) correctly return the wild value.
                info, pats, body = field_sorted[0]
                arm_env = handler_env.child()
                if pats and isinstance(pats[0], PatVar):
                    arm_env.locals[pats[0].name] = arg_idx
                arm_body = self._compile_expr(body, arm_env, f'{name_hint}_{info.fq_name.split(".")[-1]}')
                if wild_body is not None:
                    wild_compiled = self._compile_expr(wild_body, handler_env, f'{name_hint}_wild')
                    if handler_env.arity > 0:
                        const_wild = bapp(const2_pin, wild_compiled)
                        const2_wild = bapp(const2_pin, const_wild)
                    else:
                        const_wild = A(const2_pin, wild_compiled)
                        const2_wild = A(const2_pin, const_wild)
                    if info.tag == 0:
                        # z=arm_body for Nat 0, m=wild for Nat k+1, a=const2_wild for App
                        handler_body = self._make_reflect_dispatch(
                            const2_wild, arm_body, const_wild, N(fun_idx), handler_env
                        )
                    else:
                        # Build succ chain for m arm so arm fires at Nat info.tag
                        n_cap = handler_env.arity
                        m_ext_env = Env(globals=handler_env.globals, arity=n_cap + 1,
                                        self_ref_name=handler_env.self_ref_name,
                                        locals=dict(handler_env.locals))
                        pred_ref = N(n_cap + 1)
                        m_inner = self._build_precompiled_nat_dispatch(
                            [(info.tag - 1, arm_body)], wild_body, None,
                            pred_ref, m_ext_env, f'{name_hint}_tag_m'
                        )
                        m_succ = P(L(n_cap + 1, 0, m_inner))
                        m_body_val = m_succ
                        for i in range(1, n_cap + 1):
                            m_body_val = bapp(m_body_val, N(i))
                        handler_body = self._make_reflect_dispatch(
                            const2_wild, wild_compiled, m_body_val, N(fun_idx), handler_env
                        )
                else:
                    handler_body = arm_body
            else:
                # Multiple field arms: dispatch on outer_fun (tag)
                # Build per-arm precompiled bodies keyed by tag value
                tag_val_pairs = []
                for info, pats, body in field_sorted:
                    arm_env = handler_env.child()
                    if pats and isinstance(pats[0], PatVar):
                        arm_env.locals[pats[0].name] = arg_idx
                    bv = self._compile_expr(body, arm_env, f'{name_hint}_{info.fq_name.split(".")[-1]}')
                    tag_val_pairs.append((info.tag, bv))
                # Dispatch on outer_fun using a precompiled nat dispatch
                handler_body = self._build_precompiled_nat_dispatch(
                    tag_val_pairs, wild_body, wild_var_con, N(fun_idx), handler_env, f'{name_hint}_tag'
                )

        elif max_arity == 2:
            # Binary: outer_fun = A(tag, field1), outer_arg = field2
            # Need inner Case_ on outer_fun to extract field1
            field_sorted = sorted(field_arms, key=lambda t: t[0].tag)

            # Inner handler env: captures (uses_self, free_locals, outer_arg=field2) + tag + field1
            inner_env = Env(globals=env.globals, arity=0)
            inner_env.self_ref_name = ''
            inner_n_cap = 0

            if uses_self:
                inner_n_cap += 1
                inner_env.arity = inner_n_cap
                inner_env.locals[env.self_ref_name] = inner_n_cap
                short = env.self_ref_name.split('.')[-1]
                inner_env.locals[short] = inner_n_cap

            for fv in free_locals:
                inner_n_cap += 1
                inner_env.arity = inner_n_cap
                inner_env.locals[fv] = inner_n_cap

            # outer_arg (field2) captured at inner_n_cap+1
            inner_n_cap += 1
            field2_inner_idx = inner_n_cap
            inner_env.arity = inner_n_cap

            # tag at inner_n_cap+1 (inner_fun is tag for unary part), field1 at inner_n_cap+2
            inner_tag_idx = inner_n_cap + 1
            field1_idx    = inner_n_cap + 2
            inner_env.arity = field1_idx

            # Compile each binary arm body in inner_env
            if len(field_sorted) == 1:
                info, pats, body = field_sorted[0]
                arm_env = inner_env.child()
                if len(pats) >= 1 and isinstance(pats[0], PatVar):
                    arm_env.locals[pats[0].name] = field1_idx
                if len(pats) >= 2 and isinstance(pats[1], PatVar):
                    arm_env.locals[pats[1].name] = field2_inner_idx
                inner_law_body = self._compile_expr(body, arm_env, f'{name_hint}_{info.fq_name.split(".")[-1]}')
            else:
                tag_val_pairs = []
                for info, pats, body in field_sorted:
                    arm_env = inner_env.child()
                    if len(pats) >= 1 and isinstance(pats[0], PatVar):
                        arm_env.locals[pats[0].name] = field1_idx
                    if len(pats) >= 2 and isinstance(pats[1], PatVar):
                        arm_env.locals[pats[1].name] = field2_inner_idx
                    bv = self._compile_expr(body, arm_env, f'{name_hint}_{info.fq_name.split(".")[-1]}')
                    tag_val_pairs.append((info.tag, bv))
                inner_law_body = self._build_precompiled_nat_dispatch(
                    tag_val_pairs, wild_body, wild_var_con, N(inner_tag_idx), inner_env, f'{name_hint}_inner_tag'
                )

            inner_law = P(L(field1_idx, encode_name(f'{name_hint}_inner'), inner_law_body))

            # Build outer handler body:
            # 1. Partially apply inner_law to: [self?] + [free_locals] + outer_arg
            inner_applied = inner_law
            if uses_self:
                inner_applied = bapp(inner_applied, N(1))
            for i, fv in enumerate(free_locals):
                fv_handler_idx = (2 if uses_self else 1) + i
                inner_applied = bapp(inner_applied, N(fv_handler_idx))
            inner_applied = bapp(inner_applied, N(arg_idx))

            # 2. Build Case_ on outer_fun (N(fun_idx)) with inner_applied as app handler.
            # For unary constructors (arity=1), outer_fun is a bare Nat (the tag), not an App.
            # The inner Case_ Nat zero fires when outer_fun=0; we use the unary arm body
            # with tag=0 (if any) instead of the default fallback.
            unary_tag0 = next(
                ((i, p, b) for i, p, b in field_arms if i.arity == 1 and i.tag == 0),
                None
            )
            if unary_tag0 is not None:
                u_info, u_pats, u_body = unary_tag0
                u_arm_env = handler_env.child()
                if u_pats and isinstance(u_pats[0], PatVar):
                    u_arm_env.locals[u_pats[0].name] = arg_idx
                z_body = self._compile_expr(
                    u_body, u_arm_env, f'{name_hint}_{u_info.fq_name.split(".")[-1]}'
                )
            else:
                z_body = body_nat(0, handler_env.arity)

            # Build m_body for unary arms with tag>0 (e.g. PPin tag=3).
            # outer_fun=N(tag) goes to the Nat m arm with pred=tag-1.
            # We lambda-lift handler_env so the arm bodies can reference outer_arg etc.
            unary_arms_gt0 = [(i, p, b) for i, p, b in field_arms
                              if i.arity == 1 and i.tag > 0]
            if unary_arms_gt0:
                n_cap = handler_env.arity
                m_ext_env = Env(globals=handler_env.globals, arity=n_cap + 1,
                                self_ref_name=handler_env.self_ref_name,
                                locals=dict(handler_env.locals))
                pred_ref_m = N(n_cap + 1)
                m_tag_val_pairs = []
                for m_info, m_pats, m_body_ast in unary_arms_gt0:
                    m_arm_env = m_ext_env.child()
                    if m_pats and isinstance(m_pats[0], PatVar):
                        m_arm_env.locals[m_pats[0].name] = arg_idx
                    m_arm_compiled = self._compile_expr(
                        m_body_ast, m_arm_env,
                        f'{name_hint}_{m_info.fq_name.split(".")[-1]}'
                    )
                    m_tag_val_pairs.append((m_info.tag - 1, m_arm_compiled))
                m_dispatch = self._build_precompiled_nat_dispatch(
                    m_tag_val_pairs, wild_body, wild_var_con,
                    pred_ref_m, m_ext_env, f'{name_hint}_m_tag'
                )
                m_succ_law = P(L(n_cap + 1, 0, m_dispatch))
                m_body = m_succ_law
                for i in range(1, n_cap + 1):
                    m_body = bapp(m_body, N(i))
            else:
                m_body = (bapp(const2_pin, P(N(0))) if handler_env.arity > 0
                          else A(const2_pin, N(0)))

            handler_body = self._make_reflect_dispatch(
                inner_applied, z_body, m_body, N(fun_idx), handler_env
            )
        else:
            raise CodegenError(
                f'codegen: constructors with arity > 2 not yet supported in bootstrap match'
            )

        handler_law = P(L(handler_env.arity, encode_name(f'{name_hint}_app'), handler_body))

        # Partially apply handler_law to captured values in outer env
        if env.arity == 0:
            result = handler_law
            if uses_self:
                result = A(result, N(0))
            for fv in free_locals:
                fv_val = env.globals.get(fv, N(0))
                result = A(result, fv_val)
        else:
            result = handler_law
            if uses_self:
                result = bapp(result, N(0))
            for fv in free_locals:
                result = bapp(result, N(env.locals[fv]))

        return result

    def _make_reflect_dispatch(self, app_handler: Any, z_body: Any, m_body: Any,
                                scrutinee: Any, env: Env) -> Any:
        """
        Build a Case_ (opcode 3) dispatch on scrutinee's constructor type.

        (3 id id app_handler z_body m_body scrutinee)
        """
        id_pin = P(self._ID_LAW)
        if env.arity == 0:
            return A(A(A(A(A(A(P(N(3)), id_pin), id_pin), app_handler),
                        z_body), m_body), scrutinee)
        else:
            step = P(N(3))
            step = bapp(step, id_pin)
            step = bapp(step, id_pin)
            step = bapp(step, app_handler)
            step = bapp(step, z_body)
            step = bapp(step, m_body)
            step = bapp(step, scrutinee)
            return step

    def _build_precompiled_nat_dispatch(self, tag_val_pairs: list, wild_body, wild_var,
                                         scrutinee: Any, env: Env, name_hint: str) -> Any:
        """
        Build a Case_ dispatch on pre-compiled (tag, value) pairs.
        Like _build_nat_dispatch but tag_val_pairs are already-compiled PLAN values.
        """
        const2_pin = P(self._CONST2_LAW)
        if not tag_val_pairs:
            if wild_body is not None:
                return self._compile_expr(wild_body, env, name_hint + '_wild')
            return body_nat(0, env.arity)

        tag_val_pairs = sorted(tag_val_pairs, key=lambda t: t[0])

        def make_ext_env(base_env: Env) -> tuple:
            """Return (ext_env, pred_ref) with one extra slot for the predecessor nat."""
            n_cap = base_env.arity
            ext = Env(globals=base_env.globals, arity=n_cap + 1,
                      self_ref_name=base_env.self_ref_name,
                      locals=dict(base_env.locals))
            return ext, N(n_cap + 1)

        def partially_apply(law_val: Any, base_env: Env) -> Any:
            """bapp law_val to N(1)..N(base_env.arity) so the result needs one more arg."""
            result = law_val
            for i in range(1, base_env.arity + 1):
                result = bapp(result, N(i))
            return result

        def make_succ_compiled(idx: int) -> Any:
            # Lambda-lift env vars so pre-compiled arm bodies (which reference
            # env's de Bruijn indices) remain reachable inside the succ law.
            ext_env, pred_ref = make_ext_env(env)
            remaining = [(t - tag_val_pairs[idx][0] - 1, v) for t, v in tag_val_pairs[idx+1:]]
            if remaining:
                body = self._build_precompiled_nat_dispatch(
                    remaining, wild_body, wild_var, pred_ref, ext_env, name_hint
                )
            elif wild_body is not None:
                body = self._compile_expr(wild_body, ext_env, name_hint + '_wild')
            else:
                body = body_nat(0, ext_env.arity)
            succ_law = P(L(ext_env.arity, 0, body))
            return partially_apply(succ_law, env)

        zero_val = tag_val_pairs[0][1]
        first_tag = tag_val_pairs[0][0]

        if len(tag_val_pairs) == 1:
            if wild_body is not None:
                wild_val = self._compile_expr(wild_body, env, name_hint + '_wild')
                const_wild = bapp(const2_pin, wild_val) if env.arity > 0 else A(const2_pin, wild_val)
            else:
                wild_val = None
                const_wild = bapp(const2_pin, P(N(0))) if env.arity > 0 else A(const2_pin, N(0))

            if first_tag <= 0:
                # first_tag==0: normal dispatch at tag 0.
                # first_tag<0: unreachable (duplicate-tag arms shifted to negative);
                # treat same as tag=0 — this arm never fires, so value is irrelevant.
                return self._make_op2_dispatch(zero_val, const_wild, scrutinee, env)
            else:
                # Non-zero first tag: fire zero_val at scrutinee=first_tag by building
                # a succ chain of depth first_tag using recursive lambda-lifted laws.
                z_val = wild_val if wild_val is not None else body_nat(0, env.arity)
                ext_env, pred_ref = make_ext_env(env)
                inner = self._build_precompiled_nat_dispatch(
                    [(first_tag - 1, zero_val)], wild_body, wild_var,
                    pred_ref, ext_env, name_hint
                )
                succ_law = P(L(ext_env.arity, 0, inner))
                succ = partially_apply(succ_law, env)
                return self._make_op2_dispatch(z_val, succ, scrutinee, env)
        else:
            succ = make_succ_compiled(0)

        return self._make_op2_dispatch(zero_val, succ, scrutinee, env)

    def _make_op2_dispatch_reflect(self, zero_val: Any, app_handler: Any,
                                    scrutinee: Any, env: Env) -> Any:
        """
        Build Case_ dispatch where the App branch uses app_handler.
        z = zero_val (for Nat 0), a = app_handler, m = const(0) (shouldn't fire), p/l = id.
        """
        id_pin = P(self._ID_LAW)
        const2_pin = P(self._CONST2_LAW)
        m_body = bapp(const2_pin, P(N(0))) if env.arity > 0 else A(const2_pin, N(0))
        if env.arity == 0:
            return A(A(A(A(A(A(P(N(3)), id_pin), id_pin), app_handler),
                        zero_val), m_body), scrutinee)
        else:
            step = P(N(3))
            step = bapp(step, id_pin)
            step = bapp(step, id_pin)
            step = bapp(step, app_handler)
            step = bapp(step, zero_val)
            step = bapp(step, m_body)
            step = bapp(step, scrutinee)
            return step

    def _compile_fallback_match(self, scrutinee: Any, arms: list, env: Env, name_hint: str) -> Any:
        """Match with only wildcard/variable patterns — just use the first arm's body."""
        if not arms:
            raise CodegenError('codegen: empty match')
        pat, _, body = arms[0]
        arm_env = env.child()
        if isinstance(pat, PatVar):
            if env.arity > 0:
                # Bind pat.name to scrutinee via local let-binding
                new_idx = env.arity + 1
                arm_env.locals[pat.name] = new_idx
                arm_env.arity = new_idx
                body_val = self._compile_expr(body, arm_env, name_hint)
                return A(A(N(1), scrutinee), body_val)
            else:
                arm_env.globals[pat.name] = scrutinee
        return self._compile_expr(body, arm_env, name_hint)

    # -----------------------------------------------------------------------
    # Self-reference and free-variable helpers
    # -----------------------------------------------------------------------

    def _body_uses_self_ref(self, expr: Any, env: Env) -> bool:
        """Return True if expr references env.self_ref_name."""
        if not env.self_ref_name:
            return False
        names: set = set()
        self._collect_all_names(expr, names)
        fq = env.self_ref_name
        short = fq.split('.')[-1]
        return fq in names or short in names

    def _collect_all_names(self, expr: Any, names: set) -> None:
        """Collect all ExprVar names referenced in expr."""
        if isinstance(expr, ExprVar):
            names.add(str(expr.name))
        elif isinstance(expr, ExprApp):
            self._collect_all_names(expr.fun, names)
            self._collect_all_names(expr.arg, names)
        elif isinstance(expr, ExprLam):
            self._collect_all_names(expr.body, names)
        elif isinstance(expr, ExprIf):
            self._collect_all_names(expr.cond, names)
            self._collect_all_names(expr.then_, names)
            self._collect_all_names(expr.else_, names)
        elif isinstance(expr, ExprMatch):
            self._collect_all_names(expr.scrutinee, names)
            for _, _, body in expr.arms:
                self._collect_all_names(body, names)
        elif isinstance(expr, ExprLet):
            self._collect_all_names(expr.rhs, names)
            self._collect_all_names(expr.body, names)
        elif isinstance(expr, ExprAnn):
            self._collect_all_names(expr.expr, names)
        elif isinstance(expr, ExprOp):
            self._collect_all_names(expr.lhs, names)
            self._collect_all_names(expr.rhs, names)
        elif isinstance(expr, ExprFix):
            self._collect_all_names(expr.lam.body, names)
        elif isinstance(expr, ExprHandle):
            self._collect_all_names(expr.comp, names)
            for arm in expr.arms:
                if isinstance(arm, HandlerReturn):
                    self._collect_all_names(arm.body, names)
                elif isinstance(arm, HandlerOp):
                    self._collect_all_names(arm.body, names)
        elif isinstance(expr, ExprDo):
            self._collect_all_names(expr.rhs, names)
            self._collect_all_names(expr.body, names)

    def _make_pred_succ_law(self, wild_body_expr: Any, wild_var: str, env: Env, name_hint: str) -> Any:
        """
        Build a 1-arg succ function that binds wild_var to the predecessor.

        Lambda-lifts free variables from env.locals and self_ref_name into the
        law so all captured values are in scope when Case_ calls the succ function.

        Returns an expression (in env's context) that, when applied to the
        predecessor, evaluates wild_body_expr with wild_var=predecessor.
        """
        # Collect free locals (names in env.locals referenced by wild_body_expr,
        # excluding wild_var which will be bound to the predecessor).
        free_locals = self._free_vars(wild_body_expr, {wild_var}, env)
        uses_self = self._body_uses_self_ref(wild_body_expr, env)

        # Build the lifted law's environment.
        # Param order: [self (if used)] + [free_locals...] + [wild_var (predecessor)]
        lifted_env = Env(globals=env.globals, arity=0)
        lifted_env.self_ref_name = ''  # no self-ref in lifted law; it's a different law

        if uses_self:
            # Bind self_ref_name as a local param so the body can call it
            lifted_env.arity += 1
            self_idx = lifted_env.arity
            lifted_env.locals[env.self_ref_name] = self_idx
            short = env.self_ref_name.split('.')[-1]
            lifted_env.locals[short] = self_idx

        for fv in free_locals:
            lifted_env.arity += 1
            lifted_env.locals[fv] = lifted_env.arity

        lifted_env.arity += 1
        lifted_env.locals[wild_var] = lifted_env.arity  # predecessor is last param

        # Compile the wildcard body in the lifted environment.
        body_val = self._compile_expr(wild_body_expr, lifted_env, name_hint + '_pred')
        lifted_law = P(L(lifted_env.arity, encode_name(name_hint + '_succ'), body_val))

        # Partially apply lifted_law to captured values in the outer env.
        if env.arity == 0:
            result = lifted_law
            if uses_self:
                result = A(result, N(0))
            for fv in free_locals:
                fv_val = env.globals.get(fv, env.globals.get(
                    f'{env.self_ref_name.rsplit(".", 1)[0]}.{fv}' if '.' in env.self_ref_name else fv,
                    N(0)
                ))
                result = A(result, fv_val)
        else:
            result = lifted_law
            if uses_self:
                result = bapp(result, N(0))
            for fv in free_locals:
                result = bapp(result, N(env.locals[fv]))

        return result

    # -----------------------------------------------------------------------
    # Local let expressions
    # -----------------------------------------------------------------------

    def _compile_local_let(self, expr: ExprLet, env: Env, name_hint: str) -> Any:
        """
        Compile a local let binding.

        In PLAN law bodies, local lets use the let-binding form:
          (1 rhs continuation)
        where continuation is the rest of the body with the bound var
        at the next de Bruijn index.

        In top-level context (env.arity == 0), compile inline.
        """
        pat = expr.pattern
        pat_name = self._pat_var_name(pat)

        rhs_val = self._compile_expr(expr.rhs, env, pat_name)

        if env.arity == 0:
            # Top-level: inline the binding as a global so body can reference it
            body_env = env.child()
            body_env.globals[pat_name] = rhs_val
            return self._compile_expr(expr.body, body_env, name_hint)
        else:
            # Law body: use PLAN let-binding form (1 rhs continuation)
            # judge processes A(A(N(1), rhs), body) by:
            #   evaluating rhs, binding it to the NEXT index (n+1), then evaluating body
            # Existing locals keep their current indices; the new binding gets index n+1.
            body_env = env.child()
            new_idx = env.arity + 1          # next available de Bruijn index
            body_env.locals[pat_name] = new_idx
            body_env.arity = new_idx
            body_val = self._compile_expr(expr.body, body_env, name_hint)
            return A(A(N(1), rhs_val), body_val)

    # -----------------------------------------------------------------------
    # Programmer pins
    # -----------------------------------------------------------------------

    def _compile_expr_pin(self, expr: ExprPin, env: Env, name_hint: str) -> Any:
        """Compile @name = rhs  body."""
        rhs_val = self._compile_expr(expr.rhs, env, expr.name)
        pinned = P(rhs_val) if not is_pin(rhs_val) else rhs_val

        body_env = env.child()
        body_env.globals[expr.name] = pinned
        return self._compile_expr(expr.body, body_env, name_hint)

    # -----------------------------------------------------------------------
    # Operators
    # -----------------------------------------------------------------------

    def _compile_op(self, expr: ExprOp, env: Env, name_hint: str) -> Any:
        """Compile a binary operator expression."""
        op = expr.op
        lhs = self._compile_expr(expr.lhs, env)
        rhs = self._compile_expr(expr.rhs, env)

        # Look up operator implementation from globals
        op_map = {
            '+': 'Core.Nat.add',
            '-': 'Core.Nat.sub',
            '*': 'Core.Nat.mul',
            '/': 'Core.Nat.div',
            '%': 'Core.Nat.mod',
            '≤': 'Core.Nat.lte',
            '≥': 'Core.Nat.gte',
            '<': 'Core.Nat.lt',
            '>': 'Core.Nat.gt',
            '≠': 'Core.Nat.neq',
            '·': 'Core.List.append',
            '⊕': 'Core.Nat.xor',
        }
        fn_name = op_map.get(op)
        if fn_name and fn_name in env.globals:
            fn_val = env.globals[fn_name]
            if env.arity == 0:
                return A(A(fn_val, lhs), rhs)
            else:
                fn_pin = P(fn_val) if not is_pin(fn_val) else fn_val
                return bapp(bapp(fn_pin, lhs), rhs)

        # Fallback: leave as App (useful for testing)
        if env.arity == 0:
            return A(A(N(0), lhs), rhs)
        else:
            return bapp(N(0), lhs, rhs)

    def _compile_unary(self, expr: ExprUnary, env: Env, name_hint: str) -> Any:
        """Compile a unary operator."""
        operand = self._compile_expr(expr.operand, env)
        if expr.op == '-':
            # Negation: not defined for Nat; return 0 as placeholder
            return N(0) if env.arity == 0 else P(N(0))
        if expr.op == '¬':
            # Boolean not: if x then False else True
            # Use the same Case_ dispatch helper (opcode 3, 6 args).
            false_val = N(0) if env.arity == 0 else A(N(0), N(0))
            true_val = N(1) if (env.arity == 0 or 1 > env.arity) else A(N(0), N(1))
            succ_fn = self._make_const_law(N(0), name_hint + '_not_succ') \
                if env.arity == 0 \
                else self._make_const_law_body(P(N(0)), env, name_hint + '_not_succ')
            return self._make_op2_dispatch(true_val, succ_fn, operand, env)
        return operand

    def _is_tuple_match(self, arms) -> bool:
        for pat, _, _ in arms:
            if isinstance(pat, PatTuple):
                return True
        return False

    def _compile_tuple_match(self, scrutinee, arms, env, name_hint):
        """Compile match on tuple patterns.

        Tuples are encoded as A(A(0, a), b) — a binary tagged value with tag 0.
        PatTuple([p0, p1]) is treated as a binary constructor with tag=0.
        """
        pair_info = ConInfo(tag=0, arity=2, fq_name='__Pair__')
        con_arms = []
        wild_arm = None
        for pat, guard, body in arms:
            if isinstance(pat, PatTuple):
                if len(pat.pats) != 2:
                    raise CodegenError(
                        f'codegen: only 2-tuples supported in bootstrap, got {len(pat.pats)}-tuple'
                    )
                con_arms.append((pair_info, list(pat.pats), body))
            elif isinstance(pat, (PatWild, PatVar)):
                wild_arm = (pat, body)
        if not con_arms:
            return self._compile_fallback_match(scrutinee, arms, env, name_hint)
        return self._compile_con_match_case3(scrutinee, con_arms, wild_arm, env, name_hint)

    # -----------------------------------------------------------------------
    # Tuples
    # -----------------------------------------------------------------------

    def _compile_tuple(self, expr: ExprTuple, env: Env, name_hint: str) -> Any:
        """Compile a 2-tuple as A(A(0, a), b) — tag-0 binary tagged value."""
        elems = [self._compile_expr(e, env) for e in expr.elems]
        if not elems:
            return self._compile_nat_literal(0, env)
        # Build left-to-right App chain starting with the tag (0)
        if env.arity == 0:
            result = N(0)
            for e in elems:
                result = A(result, e)
        else:
            # In a law body: tag 0 must be quoted (A(N(0), N(0))) to avoid
            # de Bruijn collision, then fields are bapp'd on.
            result = A(N(0), N(0))  # quote(0) = literal 0
            for e in elems:
                result = bapp(result, e)
        return result

    # -----------------------------------------------------------------------
    # Fix (anonymous recursion)
    # -----------------------------------------------------------------------

    def _compile_fix(self, expr: ExprFix, env: Env, name_hint: str) -> Any:
        """Compile fix λ self args → body (anonymous recursion)."""
        lam = expr.lam
        params = self._flatten_params(lam)
        body_expr = self._lambda_body(lam)

        if not params:
            raise CodegenError('codegen: fix requires at least one parameter (self-reference)')

        # First param is the self-reference name; remaining are user-visible arguments.
        self_name = self._pat_var_name(params[0])
        user_params = params[1:]

        # Build body env: self → N(0) via self_ref_name; user params → N(1), N(2), ...
        body_env = Env(globals=env.globals, arity=0, self_ref_name=self_name)
        for pat in user_params:
            pn = self._pat_var_name(pat)
            body_env = body_env.bind_param(pn)

        body_val = self._compile_expr(body_expr, body_env)
        name_nat = encode_name(name_hint) if name_hint else 0
        law = L(len(user_params), name_nat, body_val)

        if env.arity == 0:
            return law
        else:
            return P(law)


    # -----------------------------------------------------------------------
    # Effect handlers (M10.2): ExprHandle and ExprDo
    # -----------------------------------------------------------------------

    def _compile_handle(self, expr: ExprHandle, env: Env, name_hint: str) -> Any:
        """
        Compile: handle comp { | return x → body_r | op args k → body_op }

        Encoding (CPS):
          dispatch_fn = L(3+n_cap, name, nat_dispatch_body)   -- (op_tag, op_arg, k)
          return_fn   = L(1+n_cap, name, return_body)         -- (x)
          result = A(A(comp_val, dispatch_fn), return_fn)     -- [top level]
                 = bapp(bapp(comp_val, dispatch_fn), return_fn)  -- [law body]

        When `comp` is an effect op call `E.op arg`, the assembly evaluates as:
          op_law arg dispatch_fn return_fn
          → dispatch_fn(tag, arg, return_fn)
          → arm_body[arg_pats=arg, resume=return_fn]
        """
        comp_val = self._compile_expr(expr.comp, env, name_hint + '_comp')

        return_arm = next((a for a in expr.arms if isinstance(a, HandlerReturn)), None)
        op_arms    = [a for a in expr.arms if isinstance(a, HandlerOp)]

        dispatch_fn = self._compile_dispatch_fn(op_arms, env, name_hint)
        return_fn   = self._compile_return_fn(return_arm, env, name_hint)

        if env.arity == 0:
            return A(A(comp_val, dispatch_fn), return_fn)
        else:
            return bapp(bapp(comp_val, dispatch_fn), return_fn)

    def _collect_free_for_handler(self, bodies: list, arm_bounds: list, env: Env) -> list:
        """
        Collect free variables (in env.locals) used by handler arm bodies,
        excluding the arm-specific bound names.
        Returns a list in env.locals key order.
        """
        combined: set = set()
        for body, bound in zip(bodies, arm_bounds):
            self._collect_free(body, bound, env, combined)
        return [k for k in env.locals if k in combined]

    def _compile_dispatch_fn(self, op_arms: list, env: Env, name_hint: str) -> Any:
        """
        Build the handler dispatch law: L(n_cap+3, name, body)
        Law args: [cap_1..cap_n_cap] + [op_tag] + [op_arg] + [resume/k]

        Dispatches on op_tag via nat ladder; each arm binds op_arg and resume.
        Free outer locals are lambda-lifted as leading parameters.
        """
        if not op_arms:
            # No op arms: unreachable dispatch; return a 3-arg stub
            return P(L(3, encode_name(f'{name_hint}_dispatch'), body_nat(0, 3)))

        # Collect free vars from all arm bodies (excluding arm-specific names)
        all_bodies = []
        all_bounds = []
        for arm in op_arms:
            arm_bound: set = {arm.resume}
            for p in arm.arg_pats:
                arm_bound |= self._pat_binds(p)
            all_bodies.append(arm.body)
            all_bounds.append(arm_bound)

        free_locals = self._collect_free_for_handler(all_bodies, all_bounds, env)
        n_cap = len(free_locals)

        # Build dispatch_env: [caps...] + [op_tag] + [op_arg] + [resume]
        dispatch_env = Env(globals=env.globals, arity=n_cap + 3,
                           self_ref_name=env.self_ref_name)
        for i, fv in enumerate(free_locals, 1):
            dispatch_env.locals[fv] = i
        op_tag_idx = n_cap + 1
        op_arg_idx = n_cap + 2
        k_idx      = n_cap + 3

        # Compile each arm's body in arm_env (with op_arg and resume bound)
        tag_val_pairs = []
        for arm in op_arms:
            tag = self._lookup_op_tag(arm.op_name)
            arm_env = dispatch_env.child()
            for p in arm.arg_pats:
                pn = self._pat_var_name(p)
                if pn not in ('_wild', '_pat', '__'):
                    arm_env.locals[pn] = op_arg_idx
            arm_env.locals[arm.resume] = k_idx
            body_val = self._compile_expr(arm.body, arm_env, f'{name_hint}_op{tag}')
            tag_val_pairs.append((tag, body_val))

        tag_val_pairs.sort(key=lambda t: t[0])

        dispatch_body = self._build_precompiled_nat_dispatch(
            tag_val_pairs, None, None,
            N(op_tag_idx), dispatch_env, f'{name_hint}_dispatch'
        )

        dispatch_law = P(L(n_cap + 3, encode_name(f'{name_hint}_dispatch'), dispatch_body))

        # Partially apply to free_locals in outer env
        if env.arity == 0:
            result = dispatch_law
            for fv in free_locals:
                result = A(result, env.globals.get(fv, N(0)))
        else:
            result = dispatch_law
            for fv in free_locals:
                result = bapp(result, N(env.locals[fv]))
        return result

    def _compile_return_fn(self, return_arm: Any, env: Env, name_hint: str) -> Any:
        """
        Build the return handler: L(n_cap+1, name, body) where the last param
        is the pure return value bound by the return pattern.
        """
        if return_arm is None:
            return P(self._ID_LAW)  # identity by default

        pat_names = self._pat_binds(return_arm.pattern)
        free_locals = self._collect_free_for_handler(
            [return_arm.body], [pat_names], env
        )
        n_cap = len(free_locals)

        return_env = Env(globals=env.globals, arity=n_cap + 1,
                         self_ref_name=env.self_ref_name)
        for i, fv in enumerate(free_locals, 1):
            return_env.locals[fv] = i
        val_idx = n_cap + 1
        # Bind return pattern variable (simple PatVar or PatWild only)
        pat_name = self._pat_var_name(return_arm.pattern)
        if pat_name not in ('_wild', '_pat'):
            return_env.locals[pat_name] = val_idx

        body_val = self._compile_expr(return_arm.body, return_env, f'{name_hint}_return')
        return_law = P(L(n_cap + 1, encode_name(f'{name_hint}_return'), body_val))

        if env.arity == 0:
            result = return_law
            for fv in free_locals:
                result = A(result, env.globals.get(fv, N(0)))
        else:
            result = return_law
            for fv in free_locals:
                result = bapp(result, N(env.locals[fv]))
        return result

    def _lookup_op_tag(self, op_name: str) -> int:
        """Look up the CPS tag for an effect operation by name."""
        if op_name in self.effect_op_tags:
            return self.effect_op_tags[op_name]
        for k, v in self.effect_op_tags.items():
            if k.endswith('.' + op_name):
                return v
        raise CodegenError(f'codegen: unknown effect operation {op_name!r}')

    def _compile_do(self, expr: ExprDo, env: Env, name_hint: str) -> Any:
        """
        Compile: x ← rhs_comp; body_expr   (effectful bind)

        Result is a CPS computation value (a 2-arg function taking dispatch and k).
        Encoding: λ dispatch k_outer → rhs_comp dispatch (λ x → body_comp dispatch k_outer)

        The inner continuation lambda captures dispatch and k_outer from the outer
        2-arg law via lambda lifting.

        Outer law (arity = n_cap+2):
          [caps...] + [dispatch] + [k_outer]
          body = rhs_val dispatch (inner_cont caps dispatch k_outer)

        Inner continuation law (arity = n_cap+3):
          [caps...] + [dispatch] + [k_outer] + [x]
          body = body_comp dispatch k_outer  (with x bound)
        """
        # Collect free vars from rhs and body (excluding the do-bound name x)
        all_free: set = set()
        self._collect_free(expr.rhs, set(), env, all_free)
        self._collect_free(expr.body, {expr.name}, env, all_free)
        free_locals = [k for k in env.locals if k in all_free]
        n_cap = len(free_locals)

        # Indices in the outer (n_cap+2)-arg law
        outer_dispatch_idx = n_cap + 1
        outer_k_idx        = n_cap + 2

        # Indices in the inner (n_cap+3)-arg continuation law
        inner_dispatch_idx = n_cap + 1
        inner_k_idx        = n_cap + 2
        inner_x_idx        = n_cap + 3

        # --- Build inner continuation law ---
        inner_env = Env(globals=env.globals, arity=n_cap + 3,
                        self_ref_name=env.self_ref_name)
        for i, fv in enumerate(free_locals, 1):
            inner_env.locals[fv] = i
        inner_env.locals['__dispatch__'] = inner_dispatch_idx
        inner_env.locals['__k__']        = inner_k_idx
        inner_env.locals[expr.name]      = inner_x_idx  # do-bound variable

        body_cps = self._compile_expr(expr.body, inner_env, name_hint + '_body')
        # Apply body_cps to dispatch (N(inner_dispatch_idx)) and k_outer (N(inner_k_idx))
        inner_body = bapp(bapp(body_cps, N(inner_dispatch_idx)), N(inner_k_idx))
        inner_law  = P(L(n_cap + 3, encode_name(name_hint + '_cont'), inner_body))

        # --- Build outer 2-arg (+ captures) law ---
        outer_env = Env(globals=env.globals, arity=n_cap + 2,
                        self_ref_name=env.self_ref_name)
        for i, fv in enumerate(free_locals, 1):
            outer_env.locals[fv] = i
        outer_env.locals['__dispatch__'] = outer_dispatch_idx
        outer_env.locals['__k__']        = outer_k_idx

        rhs_val = self._compile_expr(expr.rhs, outer_env, name_hint + '_rhs')

        # Partially apply inner_law to captures + dispatch + k_outer
        inner_cont = inner_law
        for i in range(1, n_cap + 1):
            inner_cont = bapp(inner_cont, N(i))
        inner_cont = bapp(inner_cont, N(outer_dispatch_idx))
        inner_cont = bapp(inner_cont, N(outer_k_idx))

        # Outer body: rhs_comp dispatch inner_cont
        outer_body = bapp(bapp(rhs_val, N(outer_dispatch_idx)), inner_cont)
        do_law     = L(n_cap + 2, encode_name(name_hint + '_do'), outer_body)

        if env.arity == 0:
            # At top level: partially apply to captures (usually none)
            result = do_law
            for fv in free_locals:
                result = A(result, env.globals.get(fv, N(0)))
        else:
            result = P(do_law)
            for fv in free_locals:
                result = bapp(result, N(env.locals[fv]))
        return result

    # -----------------------------------------------------------------------
    # Mutual recursion: dep-graph + Tarjan SCC
    # -----------------------------------------------------------------------

    def _build_dep_graph(self, let_decls: list) -> dict:
        """
        Build a forward-reference dependency graph for DeclLet declarations.
        Returns {fq_name: set_of_fq_names_referenced}.
        Only edges to other DeclLets in this module are included.
        """
        fq_set = {f'{self.module}.{d.name}' for d in let_decls}
        graph: dict = {}
        for d in let_decls:
            fq = f'{self.module}.{d.name}'
            names: set = set()
            self._collect_all_names(d.body, names)
            # Normalise short names to FQ
            fq_refs = set()
            for n in names:
                if n in fq_set:
                    fq_refs.add(n)
                candidate = f'{self.module}.{n}'
                if candidate in fq_set:
                    fq_refs.add(candidate)
            graph[fq] = fq_refs & fq_set
        return graph

    def _tarjan_scc(self, graph: dict) -> list:
        """
        Tarjan's strongly-connected-components algorithm.
        Returns a list of SCCs in TOPOLOGICAL ORDER (dependencies first).
        Within each SCC names are sorted lexicographically (canonical order per spec).
        """
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
                sccs.append(sorted(scc))  # canonical order within SCC

        for v in sorted(graph.keys()):  # deterministic traversal
            if v not in index:
                strongconnect(v)

        # Tarjan emits SCCs where deeper dependencies come first (forward-edge DFS),
        # which is already topological order (dependencies before dependents).
        return sccs

    # -----------------------------------------------------------------------
    # Mutual recursion: shared-pin encoding
    # -----------------------------------------------------------------------

    def _compile_mutual_scc(self, scc_names: list, scc_decls: list) -> None:
        """
        Compile a mutual recursion group (n >= 2 definitions) using the
        shared-pin encoding from spec/02-mutual-recursion.md.

        Encoding overview:
          shared_pin = P(selector_law  law_0  law_1  ...  law_{n-1})

        selector_law = L(n+1, 0, body) where body dispatches on the index
        argument (last arg) and returns the corresponding law arg.

        Each law_i is lambda-lifted: arity becomes 1 + original_arity.
        The new first arg (index 1) is the shared pin.  All SCC references
        (including self) go via the shared pin.

        After building the shared pin, wrapper laws of the original arity are
        emitted so external callers see the expected signature.
        """
        n = len(scc_names)
        assert n >= 2
        assert scc_names == sorted(scc_names), "must be in canonical order"

        scc_indices = {name: i for i, name in enumerate(scc_names)}

        # --- Build selector law ---
        # arity = n+1; args: law_0 .. law_{n-1}, index_i
        # body: nat dispatch on N(n+1) returning N(1)..N(n)
        selector_env = Env(globals=self.env.globals, arity=n + 1)
        tag_val_pairs = [(j, N(j + 1)) for j in range(n)]
        dispatch_body = self._build_precompiled_nat_dispatch(
            tag_val_pairs, None, None, N(n + 1), selector_env, '_selector'
        )
        selector_law = L(n + 1, 0, dispatch_body)

        # --- Lambda-lift each law ---
        laws = []
        for decl in scc_decls:
            law = self._compile_mutual_law(decl, scc_names, scc_indices)
            laws.append(law)

        # --- Build shared row (partially-applied selector) ---
        # shared_row = selector_law applied to all laws; arity is 1 (awaits index).
        # We do NOT wrap in P() because exec_() cannot handle P(App).
        # The raw App chain is used directly in law bodies and wrapper laws.
        shared_row: Any = selector_law
        for law in laws:
            shared_row = A(shared_row, law)

        # --- Emit wrapper bindings ---
        for i, (name, decl) in enumerate(zip(scc_names, scc_decls)):
            wrapper = self._build_mutual_wrapper(shared_row, i, decl)
            self.compiled[name] = wrapper
            self.env.globals[name] = wrapper

    def _compile_mutual_law(self, decl, scc_names: list, scc_indices: dict) -> Any:
        """
        Compile one lambda-lifted law in a mutual SCC.

        Argument layout in the compiled law:
          index 0 : self (the lambda-lifted law)
          index 1 : shared_pin
          index 2+: original user arguments

        All SCC member references (including self-calls) compile to:
          bapp(bapp(N(1), literal_j), N(1))
        which evaluates to  (shared_pin j) shared_pin — the j-th law
        partially applied to the shared pin, so callers supply only the
        original user arguments.
        """
        lam = decl.body
        params = self._flatten_params(lam)
        body_expr = self._lambda_body(lam)

        # Build body env with __shared__ at index 1, then original params.
        body_env = Env(globals=dict(self.env.globals), arity=0, self_ref_name='')
        body_env = body_env.bind_param('__shared__')  # index 1
        for pat in params:
            pn = self._pat_var_name(pat)
            body_env = body_env.bind_param(pn)

        # Register every SCC member (including self) as a _MutualRef.
        for name in scc_names:
            j = scc_indices[name]
            body_env.globals[name] = _MutualRef(j)
            short = name.split('.')[-1]
            body_env.globals[short] = _MutualRef(j)

        body_val = self._compile_expr(body_expr, body_env)
        name_nat = encode_name(decl.name)
        return L(1 + len(params), name_nat, body_val)

    def _build_mutual_wrapper(self, shared_pin: Any, index: int, decl) -> Any:
        """
        Build an external-facing wrapper for SCC definition at `index`.

        The wrapper has the original arity u and evaluates:
          (shared_pin index) shared_pin arg1 ... argu
        """
        lam = decl.body
        params = self._flatten_params(lam)
        u = len(params)

        if u == 0:
            # Nullary definition: A(A(shared_pin, index), shared_pin)
            return A(A(shared_pin, N(index)), shared_pin)

        # Build a law of arity u.
        # In the body (arity=u): N(1)=arg1 .. N(u)=argu, shared_pin is a literal.
        # i_val: literal nat `index` inside a law body of arity u.
        # Use quote form A(N(0), N(index)) if index <= u, else N(index).
        i_val = A(N(0), N(index)) if index <= u else N(index)
        extract = bapp(shared_pin, i_val)    # (shared_pin index) = law_index
        call = bapp(extract, shared_pin)     # law_index shared_pin
        for k in range(1, u + 1):
            call = bapp(call, N(k))          # ... arg1 ... argu

        name_nat = encode_name(decl.name)
        return L(u, name_nat, call)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compile_program(program: Program, module: str = 'Main') -> dict[str, Any]:
    """
    Compile a resolved, type-checked program.

    Returns:
        dict mapping FQ name → PLAN value
    """
    compiler = Compiler(module=module)
    return compiler.compile(program)
