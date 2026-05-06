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
    def __init__(self, msg: str, loc=None):
        if loc is not None:
            super().__init__(f"{loc.file}:{loc.line}:{loc.col}: error: {msg}")
        else:
            super().__init__(msg)
        self.loc = loc


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
    whose arity is `arity`. Nats 0..arity are de Bruijn slot indices in
    the law evaluator, so a literal k <= arity must escape the slot
    interpretation. Use the quote form `A(N(0), N(k))` — `(0 k)` in
    Plan-Asm syntax — which the runtime interprets as a constant value
    `k` regardless of slot count.

    Previously this used `P(N(k))` (an opcode-pin), but per Reaver's
    runtime `arity (P _ _ _) = 1`: every Pin'd Nat is a saturating
    opcode pin, so `P(N(0))` triggers `op 0` dispatch on application —
    and Reaver has no `op 0` case, crashing with `no primop ... of
    size = ...` when the placeholder gets used as a function. Quote
    form is unambiguously a value and never dispatches.
    """
    if k <= arity:
        return A(N(0), N(k))
    return N(k)


def _subst_virtual_resume(expr: Any, virtual_idx: int, replacement: Any) -> Any:
    """Replace N(virtual_idx) with replacement in a PLAN expression tree.

    Only recurses through App nodes; does not enter Law or Pin (different scope).
    Used to substitute the virtual resume index with the actual open-continuation
    application in dispatch arm bodies.
    """
    if isinstance(expr, int):
        return replacement if expr == virtual_idx else expr
    if isinstance(expr, A):
        return A(_subst_virtual_resume(expr.fun, virtual_idx, replacement),
                 _subst_virtual_resume(expr.arg, virtual_idx, replacement))
    # L, P — don't recurse (different scope / pinned content)
    return expr


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

    # True iff the next `_compile_expr` call from this Env is the
    # syntactic root of a law's body (or a continuation of a let-chain
    # rooted there).  Reaver's `lawExp` text-form parser only accepts
    # the `(1 rhs body)` bind form at that position; nested lets need
    # to be lambda-lifted into a sub-law.  Default False so any code
    # path that doesn't deliberately mark itself as "law-root" treats
    # the let as an expression-position let.  See AUDIT.md D8.
    top_of_law: bool = False

    def child(self) -> 'Env':
        """Return a shallow copy for a new scope.

        `top_of_law` is intentionally NOT propagated — `child()` is used
        for arm bodies, sub-expression contexts, etc., where the fresh
        env is no longer at the law's body root.  Callers that need to
        preserve `top_of_law` (currently `_compile_local_let` for the
        let-chain body, and `_compile_expr_pin` for the pin form's
        continuation) set it explicitly after `child()`.
        """
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
    type_name: str = ''   # short type name (e.g., 'List', 'Option')


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

    def __init__(self, module: str = 'Main', pre_compiled: dict | None = None,
                 pre_class_methods: dict | None = None,
                 pre_class_defaults: dict | None = None,
                 pre_class_constraints: dict | None = None,
                 pre_con_info: dict | None = None,
                 expr_types: dict | None = None):
        self.module = module
        # id(expr) -> typecheck monotype, populated by typecheck_with_types.
        # When present, _infer_type_key prefers this over its surface heuristics.
        self.expr_types: dict | None = expr_types
        # map fq_name → PLAN value (not pinned yet)
        self.compiled: dict[str, Any] = {}
        # constructor info table: fq_name → ConInfo
        self.con_info: dict[str, ConInfo] = {}
        # global env (fills in as we compile)
        self.env = Env()
        # effect op tag lookup: fq_op_name → tag (and short_name → tag)
        self.effect_op_tags: dict[str, int] = {}
        # Global op tag counter — ensures unique tags across all effects
        self._next_op_tag: int = 0
        # Typeclass info: class_fq → ordered list of method short names
        self._class_methods: dict[str, list[str]] = {}
        # Superclass constraints: class_fq → [(superclass_short, type_args)]
        self._class_constraints: dict[str, list] = {}
        # Default method bodies: class_fq → {method_short → resolved Expr}
        self._class_defaults: dict[str, dict[str, Any]] = {}
        # Constrained lets: fq → [(class_fq, [method_fq, ...])] per constraint
        self._constrained_lets: dict[str, list] = {}
        # SCC groups: list of lists of FQ names (multi-member SCCs only)
        self.scc_groups: list[list[str]] = []
        # Register builtin constructors from scope resolver.
        # Bool: False = tag 0, True = tag 1 (conventional ordering)
        # These match the scope resolver's pre-declared 'True'/'False' bindings.
        self._register_builtins()
        # Pre-populate globals with values from already-compiled upstream modules.
        # This allows cross-module let references to resolve in _compile_var.
        if pre_compiled:
            self.env.globals.update(pre_compiled)
        # Pre-populate class metadata from upstream modules.
        # pre_class_methods: {class_fq: set(method_fq_names)} (Env.class_methods format)
        # Convert to {class_fq: [method_short_names]} (Compiler._class_methods format).
        if pre_class_methods:
            for class_fq, method_fqs in pre_class_methods.items():
                if class_fq not in self._class_methods:
                    shorts = [m.split('.')[-1] for m in sorted(method_fqs)]
                    self._class_methods[class_fq] = shorts
        # Pre-populate class defaults from upstream modules.
        if pre_class_defaults:
            for class_fq, defaults in pre_class_defaults.items():
                if class_fq not in self._class_defaults:
                    self._class_defaults[class_fq] = defaults
        # Pre-populate class constraints from upstream modules.
        if pre_class_constraints:
            for class_fq, constraints in pre_class_constraints.items():
                if class_fq not in self._class_constraints:
                    self._class_constraints[class_fq] = constraints
        # Pre-populate constructor info from upstream modules so cross-module
        # `match` patterns on imported ADTs (e.g. `Cons`, `Nil` from Core.List)
        # can be looked up by their FQ name during pattern compilation.
        if pre_con_info:
            for con_fq, info in pre_con_info.items():
                if con_fq not in self.con_info:
                    self.con_info[con_fq] = info

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
        # Law of arity 3: (value, dispatch, k_open) → k_open(dispatch, value)
        # N(1)=value, N(2)=dispatch, N(3)=k_open
        # Open-continuation protocol: k_open is 2-arg (dispatch, value).
        pure_law = L(3, encode_name('pure'), bapp(bapp(N(3), N(2)), N(1)))
        self.env.globals['pure'] = pure_law
        # `run comp` — execute a CPS computation with null dispatch and id_open.
        # N(1)=comp, P(_NULL_DISPATCH)=dispatch_parent, P(_ID_OPEN)=open continuation
        run_law = L(1, encode_name('run'),
                     bapp(bapp(N(1), P(self._NULL_DISPATCH)), P(self._ID_OPEN)))
        self.env.globals['run'] = run_law

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
            info = ConInfo(tag=tag, arity=n_fields, fq_name=fq_con,
                          type_name=decl.name)
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

    # Core.PLAN primitives: each Core.PLAN.<name> external item maps to a
    # BPLAN named primitive — a Pin'd Law that delegates to the (P("B")) ABI
    # at runtime. Per `vendor/reaver/src/hs/Plan.hs` op 66 cases.
    #
    # Migration note (2026-04-30): the canonical 3-opcode ABI keeps `Pin`,
    # `Law`, and `Elim` as opcodes (0/1/2), but Reaver's runtime today
    # dispatches them only via the BPLAN op-66 path, mirroring how
    # `vendor/reaver/src/plan/boot.plan` builds them. We follow Reaver:
    # all five Core.PLAN primitives become BPLAN-named Pin'd Laws.
    _CORE_PLAN_BPLAN_PRIMS: dict[str, tuple[str, int]] = {
        'Core.PLAN.pin':     ('Pin',   1),
        'Core.PLAN.mk_law':  ('Law',   3),
        'Core.PLAN.inc':     ('Inc',   1),
        'Core.PLAN.reflect': ('Elim',  6),
        'Core.PLAN.force':   ('Force', 1),
    }

    # Core.PLAN named jets: dispatched by law name+arity in the harness.
    _CORE_PLAN_NAMED_JETS: dict[str, tuple] = {
        'Core.PLAN.unpin': (1, 'Unpin'),  # extract inner value from Pin
    }

    # Core.IO.Prim: I/O primitives mapped to planvm jet.primtab entries.
    # write_op = P(N(9)) = WriteOp; takes a size-4 closure (fd, buf, count, offset).
    #
    # NOTE: `P(N(9))` is an opcode pin for the legacy planvm path. Reaver
    # has no `op 9` dispatch (only `op 66` BPLAN and `op 82` RPLAN), so
    # any program that compiles + runs through Reaver and reaches this
    # value crashes with `no primop 9 ...`. Currently only referenced by
    # `Compiler.run_main` (the deferred Path A entry point), which isn't
    # exercised under Reaver — Phase G's `Compiler.main` re-shape will
    # replace this entirely with `Reaver.RPLAN.output`. Tracked in
    # ROADMAP.md §"Phase G".
    _CORE_IO_PRIMITIVES: dict[str, Any] = {
        'Core.IO.Prim.write_op': P(N(9)),
    }

    # Reaver.RPLAN named ops: each Reaver.RPLAN.<source_name> external
    # item maps to an RPLAN named primitive — a Pin'd Law that delegates
    # to the (P("R")) ABI at runtime. Per `vendor/reaver/src/hs/Plan.hs`
    # op 82 cases, surfaced by the `(rplan ...)` macro in
    # `vendor/reaver/src/plan/boot.plan`.
    #
    # Source-side names are lower-snake_case per gallowglass naming
    # convention; runtime names are the PascalCase RPLAN ops as
    # enumerated by Plan.hs:rplan. The arity column matches the
    # case-pattern shape there exactly; drift is caught by
    # `tests/sanity/test_rplan_deps.py`.
    #
    # Per Sol (2026-04-30) RPLAN is *tentative*. Bump this table in
    # lockstep with vendor.lock SHA changes; the canary test will fail
    # loudly if you forget.
    _REAVER_RPLAN_PRIMS: dict[str, tuple[str, int]] = {
        'Reaver.RPLAN.input':     ('Input',    1),
        'Reaver.RPLAN.output':    ('Output',   1),
        'Reaver.RPLAN.warn':      ('Warn',     1),
        'Reaver.RPLAN.read_file': ('ReadFile', 1),
        'Reaver.RPLAN.print':     ('Print',    1),
        'Reaver.RPLAN.stamp':     ('Stamp',    1),
        'Reaver.RPLAN.now':       ('Now',      1),
    }

    # The "B" opcode pin — gateway to BPLAN named-op dispatch (see
    # vendor/reaver/src/hs/Plan.hs op 66). Saturating ((<B>) ("Name" args))
    # triggers dispatch.
    _BPLAN_PIN: Any = P(N(int.from_bytes(b'B', 'little')))

    # The "R" opcode pin — gateway to RPLAN named-op dispatch (see
    # vendor/reaver/src/hs/Plan.hs op 82). Same shape as BPLAN's
    # gateway but routes through the RPLAN dispatch table for stdio /
    # filesystem / clock primitives.
    _RPLAN_PIN: Any = P(N(int.from_bytes(b'R', 'little')))

    @classmethod
    def _make_bplan_prim(cls, name: str, prim_arity: int,
                         gateway_pin: Any | None = None) -> Any:
        """Build the Pin'd Law for a BPLAN- or RPLAN-named primitive.

        Mirrors `vendor/reaver/src/plan/boot.plan`'s `bplan` / `rplan`
        macro expansion: produces

            (#pin (#law "Name" (Name a1 ... aN) ((#pin G) ("Name" a1 ... aN))))

        where G is "B" for BPLAN ops (default) or "R" for RPLAN ops.

        In our PLAN representation:
            P(L(arity, strNat(name),
                  bapp(gateway_pin, bapp(quoted_name, slot_1, ..., slot_arity))))

        where every literal nat in body context is quote-wrapped (per the
        always-quote-wrap discipline from PR #48). Pass
        `gateway_pin=cls._RPLAN_PIN` for RPLAN ops; the default is the
        BPLAN gateway preserved for the existing Core.PLAN call sites.
        """
        if gateway_pin is None:
            gateway_pin = cls._BPLAN_PIN
        name_nat = encode_name(name)
        # Quote-wrapped name nat (constant in body context).
        quoted_name = A(N(0), N(name_nat))
        # Inner App: ("Name" arg1 arg2 ... argN). Built via bapp so kal
        # substitutes slot refs at evaluation time; the result after kal
        # is a flat App spine that the dispatcher can unapp.
        slots = [N(k) for k in range(1, prim_arity + 1)]
        inner = bapp(quoted_name, *slots)
        # Outer apply: ((<G>) inner) — saturating <G> with one arg fires
        # op(strNat("B"|"R"), [inner]) → BPLAN/RPLAN dispatch.
        body = bapp(gateway_pin, inner)
        return P(L(prim_arity, name_nat, body))

    @staticmethod
    def _make_core_text_primitives() -> dict:
        """
        Build pre-compiled PLAN laws for Core.Text.Prim operations.

        Text encoding: A(byte_length, content_nat) — a raw App of two Nats.
        This is NOT the GLS tagged-pair/tuple encoding A(A(0, a), b).

        mk_text len content → A(len, content)
          Law body (arity=2): bapp(N(1), N(2)) = A(A(N(0), N(1)), N(2))
          In kal: apply(e[1], e[0]) = A(len_val, content_val) ✓

        text_len t → byte_length
          Law body (arity=1): Case_ dispatch; App branch returns first component.

        text_nat t → content_nat
          Law body (arity=1): Case_ dispatch; App branch returns second component.
        """
        # Helpers for Case_ construction inside a 1-arity law body
        # const0_1: 1-arg law returning literal 0 (for pin/succ cases)
        const0_1 = P(L(1, 0, A(N(0), N(0))))   # A(N(0),N(0)) in arity-1 body = quote(0) = N(0)
        # const0_3: 3-arg law returning literal 0 (for law case)
        const0_3 = P(L(3, 0, A(N(0), N(0))))
        # app_fun: 2-arg law returning first arg (byte_length)
        app_fun = P(L(2, 0, N(1)))
        # app_arg: 2-arg law returning second arg (content_nat)
        app_arg = P(L(2, 0, N(2)))
        # zero_case: literal 0 inside arity-1 law body
        zero_lit = A(N(0), N(0))

        def make_text_field_law(field_selector) -> Any:
            """Build a 1-arg law that extracts a field from a Text App value."""
            body = bapp(P(N(2)), const0_1, const0_3, field_selector, zero_lit, const0_1, N(1))
            return P(L(1, encode_name('text_field'), body))

        # mk_text: arity-2, body = bapp(N(1), N(2)) = (0 1 2) in law body
        mk_text_body = bapp(N(1), N(2))
        mk_text_law = P(L(2, encode_name('mk_text'), mk_text_body))

        return {
            'Core.Text.Prim.mk_text':  mk_text_law,
            'Core.Text.Prim.text_len': make_text_field_law(app_fun),
            'Core.Text.Prim.text_nat': make_text_field_law(app_arg),
        }

    # Lazily initialized at first use to avoid issues at class definition time.
    _CORE_TEXT_PRIMITIVES: dict | None = None

    @classmethod
    def _get_core_text_primitives(cls) -> dict:
        if cls._CORE_TEXT_PRIMITIVES is None:
            cls._CORE_TEXT_PRIMITIVES = cls._make_core_text_primitives()
        return cls._CORE_TEXT_PRIMITIVES

    def _register_ext(self, decl: DeclExt) -> None:
        """Register external module items as opaque values."""
        mod_path = '.'.join(decl.module_path)
        for item in decl.items:
            if item.is_type:
                continue
            fq = f'{mod_path}.{item.name}'
            # Core.PLAN.* operations are BPLAN named primitives —
            # Pin'd Laws that delegate to the (P("B")) ABI at runtime.
            if fq in self._CORE_PLAN_BPLAN_PRIMS:
                bplan_name, bplan_arity = self._CORE_PLAN_BPLAN_PRIMS[fq]
                stub = self._make_bplan_prim(bplan_name, bplan_arity)
            elif fq in self._CORE_PLAN_NAMED_JETS:
                arity, law_name = self._CORE_PLAN_NAMED_JETS[fq]
                # Named jet: BPLAN-named Pin'd Law of the given arity.
                stub = self._make_bplan_prim(law_name, arity)
            elif fq in self._REAVER_RPLAN_PRIMS:
                rplan_name, rplan_arity = self._REAVER_RPLAN_PRIMS[fq]
                # RPLAN named op: same shape as a BPLAN named primitive
                # but routed through the (P("R")) gateway.
                stub = self._make_bplan_prim(
                    rplan_name, rplan_arity, gateway_pin=self._RPLAN_PIN
                )
            elif fq.startswith('Reaver.BPLAN.'):
                # Reaver.BPLAN.<lower> exposes any BPLAN intrinsic in
                # `bplan_deps.PRELUDE_INTRINSICS` as a gallowglass-level
                # name. Source-side names are lower-snake_case;
                # PRELUDE_INTRINSICS keys are PascalCase. Map by case-
                # folding the source name and matching against the
                # PascalCase keys. Any miss falls through to the opaque
                # sentinel (which parses but won't actually run).
                source_short = fq.split('.')[-1]
                target_name = source_short[:1].upper() + source_short[1:]
                from bootstrap.bplan_deps import PRELUDE_INTRINSICS
                if target_name in PRELUDE_INTRINSICS:
                    stub = self._make_bplan_prim(
                        target_name, PRELUDE_INTRINSICS[target_name]
                    )
                else:
                    stub = P(N(encode_name(fq)))
            elif fq in self._CORE_IO_PRIMITIVES:
                stub = self._CORE_IO_PRIMITIVES[fq]
            elif fq in self._get_core_text_primitives():
                stub = self._get_core_text_primitives()[fq]
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
        for op in decl.ops:
            tag = self._next_op_tag
            self._next_op_tag += 1
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
        """Register typeclass method names, superclass constraints, and default bodies."""
        fq_cls = f'{self.module}.{decl.name}'
        methods = [m.name for m in decl.members if isinstance(m, ClassMember)]
        self._class_methods[fq_cls] = methods
        if decl.constraints:
            self._class_constraints[fq_cls] = decl.constraints
        defaults = {}
        for m in decl.members:
            if isinstance(m, ClassMember) and m.default is not None:
                defaults[m.name] = m.default
        if defaults:
            self._class_defaults[fq_cls] = defaults

    def _expand_superclass_constraints(self, constraints: list) -> list:
        """Expand constraints to include superclass methods (flat expansion).

        If constrained by Ord (superclass Eq), expands to [Eq, Ord] so that
        dict params include Eq methods before Ord methods.
        Returns the expanded list of (class_short, type_args) tuples.
        """
        expanded = []
        seen = set()
        for class_short, type_args in constraints:
            class_fq = self._resolve_class_fq(class_short)
            self._expand_one_constraint(class_fq, class_short, type_args, expanded, seen)
        return expanded

    def _expand_one_constraint(self, class_fq: str, class_short: str,
                                type_args: list, expanded: list, seen: set) -> None:
        """Recursively expand one constraint, adding superclasses first."""
        if class_fq in seen:
            return
        # First, expand superclasses
        if class_fq in self._class_constraints:
            for super_short, super_type_args in self._class_constraints[class_fq]:
                super_fq = self._resolve_class_fq(super_short)
                self._expand_one_constraint(super_fq, super_short, type_args, expanded, seen)
        seen.add(class_fq)
        expanded.append((class_short, type_args))

    def _resolve_class_fq(self, class_short: str) -> str:
        """Resolve a short class name to its defining-module FQ form.

        Tries the current module first (single-module programs), then searches
        all known classes for a match by short name (cross-module case).
        Returns '{self.module}.{class_short}' as a final fallback so that
        single-module programs that haven't pre-threaded class metadata still work.
        """
        local_fq = f'{self.module}.{class_short}'
        if local_fq in self._class_methods:
            return local_fq
        for fq in self._class_methods:
            if fq.split('.')[-1] == class_short:
                return fq
        return local_fq

    def _compile_inst(self, decl: DeclInst) -> None:
        """Compile instance methods and emit named dict values.

        Naming convention:
          Module.inst_ClassName_TypeKey_method  — individual method law
          Module.inst_ClassName_TypeKey          — dict (= method law for single-method class)

        For constrained instances (e.g., Eq a => Eq (List a)), each method gets
        extra leading parameters for the constraint dicts, just like constrained lets.
        """
        class_fq = self._resolve_class_fq(decl.class_name)
        type_key = self._typearg_key(decl.type_args[0]) if decl.type_args else 'Unknown'

        methods = self._class_methods.get(class_fq, [])
        compiled_methods: dict[str, Any] = {}

        # Compute constraint dict param FQs (empty for unconstrained instances).
        dict_param_fqs: list[str] = []
        if decl.constraints:
            expanded = self._expand_superclass_constraints(decl.constraints)
            for class_short, _type_args in expanded:
                cfq = self._resolve_class_fq(class_short)
                constraint_methods = self._class_methods.get(cfq, [class_short])
                cmod = cfq.rsplit('.', 1)[0]
                for m in constraint_methods:
                    dict_param_fqs.append(f'{cmod}.{m}')

        # Determine the class module prefix for binding method FQ names.
        class_module = class_fq.rsplit('.', 1)[0]

        # Pass 1: compile explicitly provided instance methods.
        # Each method body can reference class method names (e.g., `eq`) which
        # need to resolve to either:
        #   - A dict param (constrained instances: `eq` is in dict_param_fqs)
        #   - Self-reference via N(0) (the current method calls itself recursively)
        #   - A sibling instance method (already compiled in a prior iteration)
        for member in decl.members:
            if not isinstance(member, InstanceMember):
                continue
            # Bind already-compiled sibling methods under class method FQ names
            # so cross-method references resolve.
            for sib_name, sib_val in compiled_methods.items():
                self.env.globals[f'{class_module}.{sib_name}'] = sib_val
            # For unconstrained instances, self_ref_fq enables law self-reference
            # (N(0)) for recursive methods. For constrained instances, recursion
            # goes through the dict param, so self-ref is not needed.
            method_class_fq = f'{class_module}.{member.name}' if not dict_param_fqs else ''
            val = self._compile_inst_method_body(
                member.body, member.name, dict_param_fqs,
                self_ref_fq=method_class_fq)
            method_fq = f'{self.module}.inst_{decl.class_name}_{type_key}_{member.name}'
            self.compiled[method_fq] = val
            self.env.globals[method_fq] = val
            compiled_methods[member.name] = val

        # Pass 2: fill in missing methods from class defaults.
        # Default bodies may reference other methods of the same class
        # (e.g., neq defaults to `not (eq ...)`), so they are compiled in an
        # environment where already-provided instance methods are in globals
        # under their class method FQ names.
        defaults = self._class_defaults.get(class_fq, {})
        for method_name in methods:
            if method_name in compiled_methods:
                continue
            if method_name not in defaults:
                continue
            # Bind all compiled instance methods under their class method FQ names.
            for sib_name, sib_val in compiled_methods.items():
                self.env.globals[f'{class_module}.{sib_name}'] = sib_val
            method_class_fq = f'{class_module}.{method_name}'
            val = self._compile_inst_method_body(
                defaults[method_name], method_name, dict_param_fqs,
                self_ref_fq=method_class_fq)
            method_fq = f'{self.module}.inst_{decl.class_name}_{type_key}_{method_name}'
            self.compiled[method_fq] = val
            self.env.globals[method_fq] = val
            compiled_methods[method_name] = val

        # For single-method classes: the dict IS the one method.
        # For multi-method classes: each method has its own named law; the
        # dict FQ is not emitted as a bundle (methods are passed flat).
        ordered_vals = [compiled_methods[m] for m in methods if m in compiled_methods]
        if len(ordered_vals) == 1:
            dict_fq = f'{self.module}.inst_{decl.class_name}_{type_key}'
            self.compiled[dict_fq] = ordered_vals[0]
            self.env.globals[dict_fq] = ordered_vals[0]

        # Register constrained instances so call-site dict insertion knows to
        # propagate inner constraint dicts.
        if decl.constraints:
            expanded = self._expand_superclass_constraints(decl.constraints)
            constraint_info = []
            for class_short, _type_args in expanded:
                cfq = self._resolve_class_fq(class_short)
                constraint_methods = self._class_methods.get(cfq, [class_short])
                cmod = cfq.rsplit('.', 1)[0]
                method_fqs = [f'{cmod}.{m}' for m in constraint_methods]
                constraint_info.append((cfq, method_fqs))
            # Store per method so call-site can find them
            for method_name in methods:
                method_fq = f'{self.module}.inst_{decl.class_name}_{type_key}_{method_name}'
                self._constrained_lets[method_fq] = constraint_info
            dict_fq = f'{self.module}.inst_{decl.class_name}_{type_key}'
            if dict_fq in self.env.globals:
                self._constrained_lets[dict_fq] = constraint_info

    def _compile_inst_method_body(self, body_expr: Any, name_hint: str,
                                   dict_param_fqs: list[str],
                                   self_ref_fq: str = '') -> Any:
        """Compile an instance method body, adding constraint dict params if needed.

        For unconstrained instances, compiles the body directly via _compile_expr
        (with self-ref support for recursive methods).
        For constrained instances, wraps the body in a law with extra leading params
        for the constraint method dicts.

        self_ref_fq: the class method FQ name (e.g., 'Test.eq') to enable self-ref
        via N(0) in the law body.
        """
        if not dict_param_fqs:
            # Unconstrained instance: compile body directly (old path).
            # _compile_expr handles lambdas, var refs, etc. naturally.
            env = Env(globals=self.env.globals, arity=0,
                      self_ref_name=self_ref_fq)
            return self._compile_expr(body_expr, env, name_hint=name_hint)

        # Constrained instance: flatten lambda params and prepend dict params.
        if isinstance(body_expr, ExprLam):
            user_params = self._flatten_params(body_expr)
            inner_body = self._lambda_body(body_expr)
        else:
            user_params = []
            inner_body = body_expr

        body_env = Env(globals=self.env.globals, arity=0,
                       self_ref_name=self_ref_fq)
        for method_fq in dict_param_fqs:
            body_env = body_env.bind_param(method_fq)
        for pat in user_params:
            param_name = self._pat_var_name(pat)
            body_env = body_env.bind_param(param_name)

        body_val = self._compile_expr(inner_body, body_env, name_hint)
        total_arity = len(dict_param_fqs) + len(user_params)
        name_nat = encode_name(name_hint)
        return L(total_arity, name_nat, body_val)

    def _typearg_key(self, type_arg: Any) -> str:
        """Convert a type argument AST node to a stable string key for instance names.

        For concrete types: Nat → "Nat", Bool → "Bool".
        For applied types: List a → "List" (outer constructor only; the variable
        is universally quantified so doesn't appear in the key).
        """
        if isinstance(type_arg, str):
            return type_arg
        if hasattr(type_arg, 'fun'):    # TyApp — use only the outer constructor
            return self._typearg_key(type_arg.fun)
        if hasattr(type_arg, 'name'):   # TyCon, TyVar
            return str(type_arg.name)
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
            # Record for call-site dict insertion (with superclass expansion)
            expanded = self._expand_superclass_constraints(constraints)
            constraint_info = []
            for class_short, _type_args in expanded:
                class_fq = self._resolve_class_fq(class_short)
                methods = self._class_methods.get(class_fq, [class_short])
                class_module = class_fq.rsplit('.', 1)[0]
                method_fqs = [f'{class_module}.{m}' for m in methods]
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
        Superclass constraints are expanded: constrained by Ord (superclass Eq)
        adds Eq method params before Ord method params.
        Inside the body, class method FQ names are bound to their dict params.
        """
        # Expand superclass constraints
        expanded = self._expand_superclass_constraints(constraints)
        # Collect dict param FQ names (one per method per constraint, in order)
        dict_param_fqs: list[str] = []
        for class_short, _type_args in expanded:
            class_fq = self._resolve_class_fq(class_short)
            methods = self._class_methods.get(class_fq, [class_short])
            class_module = class_fq.rsplit('.', 1)[0]
            for m in methods:
                dict_param_fqs.append(f'{class_module}.{m}')

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

        body_env.top_of_law = True
        body_val = self._compile_expr(body_expr, body_env, fq)
        total_arity = len(dict_param_fqs) + len(user_params)
        name_nat = encode_name(decl.name)
        return L(total_arity, name_nat, body_val)

    # -----------------------------------------------------------------------
    # Expression compilation
    # -----------------------------------------------------------------------

    def _compile_expr(self, expr: Any, env: Env, name_hint: str = '') -> Any:
        """Compile an expression to a PLAN value.

        Per AUDIT.md D8, the `top_of_law` flag must only stay True while
        we're traversing a chain of lets at the law's body root.  Any
        other expression — match, app, lambda, etc. — means we've left
        the root, and any subsequent let inside us is a nested let
        that needs lambda-lifting.  Flip the flag off here at the
        chokepoint.  ExprLet and ExprPin handle preservation themselves
        (let-chains stay at top, programmer pins are erased into the
        body's globals so the body inherits the position).
        ExprAnn is a transparent type ascription wrapper — preserve
        the flag so `let x = (e : T)` still gets the native form.
        """
        if env.top_of_law and not isinstance(expr, (ExprLet, ExprPin, ExprAnn)):
            env = env.child()
            env.top_of_law = False

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
            f'codegen: unsupported expression {type(expr).__name__}',
            getattr(expr, 'loc', None),
        )

    def _compile_nat_literal(self, value: int, env: Env) -> Any:
        """
        Compile a nat literal.

        In a law body (env.arity > 0), every literal nat must be wrapped in
        the PLAN quote form A(N(0), N(value)) — never emitted as a bare N(k).

        Two reasons:
          1. Slot disambiguation. Bare N(k) in a body is a de Bruijn index
             when k ≤ arity. Quote-wrapping disambiguates literals from
             parameter references regardless of value.
          2. Plan Assembler text emission. The PLAN runtime's `kal` falls
             through on N(k) for k > arity and treats it as the constant k.
             But the Plan Assembler emitter sees a bare PNat and renders
             it as `_k` (a slot reference) — there is no out-of-band signal
             for "this PNat is a constant, not a slot." Always quote-wrapping
             eliminates the ambiguity at the source.

        Outside a law body, N(value) is safe.
        """
        if env.arity > 0:
            return A(N(0), N(value))   # quote form: returns literal nat value
        return N(value)

    def _compile_bytes_literal(self, expr: Any, env: Env) -> Any:
        """Compile a bytes/text literal to a (byte_length, content_nat) PLAN pair.

        Text encoding per spec §6 / spec/00-primitives.md §6:
          Text = A(byte_length : Nat, content_nat : Nat)
        where content_nat is the UTF-8 byte sequence as a little-endian nat.

        Bytes uses the same structural pair encoding (no UTF-8 invariant).

        At top level (env.arity == 0):
          A(N(byte_length), N(content_nat))

        Inside a law body (env.arity > 0):
          bapp(body_nat(byte_length), body_nat(content_nat))
          which evaluates to A(byte_length_val, content_nat_val).
        """
        if isinstance(expr, ExprText):
            if isinstance(expr.value, str):
                b = expr.value.encode('utf-8')
            else:
                # interpolated — not supported in bootstrap codegen
                raise CodegenError(
                    'codegen: interpolated strings not supported',
                    getattr(expr, 'loc', None),
                )
        elif isinstance(expr, (ExprBytes, ExprHexBytes)):
            b = expr.value
        else:
            b = b''
        byte_length = len(b)
        content = int.from_bytes(b, 'little') if b else 0
        bl_val = self._compile_nat_literal(byte_length, env)
        cn_val = self._compile_nat_literal(content, env)
        if env.arity == 0:
            return A(N(byte_length), N(content))
        else:
            return bapp(bl_val, cn_val)

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
                        f'codegen: mutual SCC reference {fq!r} used outside a law body',
                        getattr(expr, 'loc', None),
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
                # Apps (partial applications) are safe as law body literals — kal
                # returns them as-is.  Pinning them creates Pin(App(...)) which the
                # evaluator can't exec when later saturated.
                if is_app(val) or is_pin(val):
                    return val
                return P(val)

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
                        f'codegen: mutual SCC reference {short!r} used outside a law body',
                        getattr(expr, 'loc', None),
                    )
            if env.arity == 0:
                return val
            if is_nat(val):
                return self._compile_nat_literal(val, env)
            if is_app(val) or is_pin(val):
                return val
            return P(val)

        raise CodegenError(
            f'codegen: unbound variable {fq!r}',
            getattr(expr, 'loc', None),
        )

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
        fn_val = self._compile_global_ref(fq, env, getattr(expr, 'loc', None))
        result = fn_val

        # Apply dict args (one per method per constraint)
        for class_fq, method_fqs in constraint_info:
            class_short = class_fq.split('.')[-1]
            if type_key is None:
                raise CodegenError(
                    f'codegen: cannot determine instance type for constraint {class_short!r} '
                    f'at call to {fq!r} — use explicit dict passing',
                    getattr(expr, 'loc', None),
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
                    # Cross-module fallback: search all globals for any module's
                    # instance of this class+type combination.
                    method_suffix = f'.inst_{class_short}_{type_key}_{method_short}'
                    dict_suffix   = f'.inst_{class_short}_{type_key}'
                    found_key = next(
                        (k for k in self.env.globals if k.endswith(method_suffix)),
                        None,
                    )
                    if found_key is None:
                        found_key = next(
                            (k for k in self.env.globals if k.endswith(dict_suffix)
                             and not k.split('.')[-1].startswith('inst_' + class_short + '_' + type_key + '_')),
                            None,
                        )
                    if found_key is not None:
                        dict_val = self._compile_global_ref(found_key, env)
                    else:
                        raise CodegenError(
                            f'codegen: no instance {class_short} {type_key} '
                            f'(looked for {inst_method_key!r} or cross-module equivalent)',
                            getattr(expr, 'loc', None),
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

    def _compile_global_ref(self, fq: str, env: Env, loc=None) -> Any:
        """Compile a reference to a global value, respecting body vs. top-level context."""
        val = self.env.globals.get(fq)
        if val is None:
            raise CodegenError(f'codegen: unknown global {fq!r}', loc)
        if env.arity == 0:
            return val
        if is_nat(val):
            return self._compile_nat_literal(val, env)
        if is_app(val) or is_pin(val):
            return val
        return P(val)

    def _type_to_instance_key(self, ty: Any) -> str | None:
        """Convert a typecheck monotype to an instance-key string.

        Mirrors `_typearg_key`: for `TApp` use the outer constructor only, and
        strip module qualification (`Core.List` → `List`) so the key matches
        the form used at instance registration. Returns None when the type
        carries no usable head constructor (unresolved meta, type variable,
        function, tuple, etc.).
        """
        from bootstrap.typecheck import TCon, TApp, TMeta, TComp
        # Walk meta chains so we read through resolved unification variables.
        while isinstance(ty, TMeta) and ty.ref is not None:
            ty = ty.ref
        if isinstance(ty, TCon):
            return ty.name.rsplit('.', 1)[-1]
        if isinstance(ty, TApp):
            return self._type_to_instance_key(ty.fun)
        if isinstance(ty, TComp):
            # Computation type {row} t — instance lookup uses the value type.
            return self._type_to_instance_key(ty.ty)
        return None

    def _infer_type_key(self, expr: Any, env: Env) -> str | None:
        """Determine the type key of an expression for instance lookup.

        Prefers the typecheck-supplied `expr_types` map (populated by
        `typecheck_with_types` and threaded in via `Compiler.expr_types`) so
        non-trivial expressions — constructor results, applied combinators,
        let-bound intermediates — get their real inferred type. Falls back to
        a small surface-syntax heuristic for cases the typecheck pass either
        doesn't see (compile-only callers) or didn't resolve to a concrete
        head.

        Returns None if no concrete head constructor can be determined.
        """
        if self.expr_types is not None:
            ty = self.expr_types.get(id(expr))
            if ty is not None:
                key = self._type_to_instance_key(ty)
                if key is not None:
                    return key
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
            # Check if it's a nullary constructor
            if fq in self.con_info:
                return self.con_info[fq].type_name
            # Check if it's a global with known Nat value
            val = self.env.globals.get(fq)
            if val is not None and is_nat(val):
                return 'Nat'
        if isinstance(expr, ExprApp):
            # Constructor application: Cons 1 Nil → type_name of Cons
            root = expr
            while isinstance(root, ExprApp):
                root = root.fun
            if isinstance(root, ExprVar):
                root_fq = str(root.name)
                if root_fq in self.con_info:
                    return self.con_info[root_fq].type_name
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

        body_env.top_of_law = True
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

        lifted_env.top_of_law = True
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
    # Null dispatch: 3-arg law that returns 0. Used as dispatch_parent at the
    # outermost handler level. Should never be reached in a well-typed program,
    # but if it is, the body must be a value Reaver can return safely. Quote
    # form `A(N(0), N(0))` is the literal Nat 0; `P(N(0))` would be the
    # un-dispatchable Pin opcode pin (see `body_nat`).
    _NULL_DISPATCH = L(3, encode_name('_null_dispatch'), A(N(0), N(0)))
    # Compose: L(3, name, f(g(x))) — compose(f, g, x) = f(g(x))
    # Used by handle to build composed return continuations.
    _COMPOSE = L(3, encode_name('_compose'), bapp(N(1), bapp(N(2), N(3))))

    # Open-continuation CPS helpers (M13.3: shallow handler support).
    # Continuations are 2-arg: (dispatch, value) → result.
    # id_open: (dispatch, value) → value — root continuation for `run`
    _ID_OPEN = L(2, 0, N(2))
    # compose_open: (f_open, g, dispatch', x) → f_open(dispatch', g(x))
    # Used by handle to build composed open return continuations.
    _COMPOSE_OPEN = L(4, encode_name('_compose_open'),
                       bapp(bapp(N(1), N(3)), bapp(N(2), N(4))))
    # forward_k: (k_open, dispatch_fn_base, dispatch', v) → k_open(dispatch_fn_base(dispatch'), v)
    # Used when forwarding an unhandled op to the parent dispatch.
    # dispatch_fn_base is the current handler's dispatch partially applied with
    # captures but NOT dp — applying dispatch' to it reinstalls the handler with
    # dispatch' as the new parent.  This preserves nested handler layering.
    _FORWARD_K = L(4, encode_name('_forward_k'),
                    bapp(bapp(N(1), bapp(N(2), N(3))), N(4)))

    def _compile_if(self, expr: ExprIf, env: Env, name_hint: str) -> Any:
        """
        Compile if/then/else as a Bool dispatch (False=0, True=1).

        Both branches are lambda-lifted into Pin'd 1-arg thunk laws so
        neither is forced by the surrounding `kal` walk.  Op2 selects which
        Pin to apply based on the condition; the dummy argument enters the
        chosen law's body, evaluating only the selected branch.

        Without this lifting (the previous encoding inlined both branches
        as `bapp(const2_pin, body)` chains), `kal`'s recursion through the
        `(0 f x)` shape would force a recursive call in either branch
        before op2 could dispatch — a silent infinite loop at evaluation
        time, even when the branch should not have been taken (issue #1b).
        """
        cond_body = self._compile_expr(expr.cond, env)

        # Lift each branch into a 1-arg thunk law (Pin'd, so kal won't recurse).
        # The 1-arg parameter is unused inside the body — it exists only to
        # keep the law a Pin until selected and applied.
        then_thunk = self._make_pred_succ_law(expr.then_, '__if_thunk__', env,
                                              name_hint + '_then')
        else_thunk = self._make_pred_succ_law(expr.else_, '__if_thunk__', env,
                                              name_hint + '_else')

        const2_pin = P(self._CONST2_LAW)
        # Op2(zero_val=else_thunk, succ=const2(then_thunk), cond) selects a
        # thunk Pin: returns else_thunk for cond=0, returns then_thunk for
        # cond=succ (because const2 ignores the predecessor).  Applying the
        # selected Pin to a dummy argument enters its body and evaluates the
        # corresponding branch.
        if env.arity == 0:
            selected = A(A(A(A(A(A(P(N(2)), P(self._ID_LAW)),
                                P(self._ID_LAW)), P(self._ID_LAW)),
                            else_thunk), A(const2_pin, then_thunk)), cond_body)
            return A(selected, N(0))
        else:
            selected = self._make_op2_dispatch(else_thunk,
                                               bapp(const2_pin, then_thunk),
                                               cond_body, env)
            return bapp(selected, N(0))

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
        match_loc = getattr(expr, 'loc', None)

        # Classify the match based on the first meaningful pattern
        first_pat = arms[0][0] if arms else None

        if self._is_nat_match(arms):
            return self._compile_nat_match(scrutinee, arms, env, name_hint, loc=match_loc)
        elif self._is_con_match(arms):
            return self._compile_con_match(scrutinee, arms, env, name_hint, loc=match_loc)
        elif self._is_tuple_match(arms):
            return self._compile_tuple_match(scrutinee, arms, env, name_hint, loc=match_loc)
        else:
            # Wildcard or variable match: just bind the scrutinee
            return self._compile_fallback_match(scrutinee, arms, env, name_hint, loc=match_loc)

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

    def _compile_nat_match(self, scrutinee: Any, arms: list, env: Env, name_hint: str, loc=None) -> Any:
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

        if not nat_arms:
            # Only wildcard
            return self._compile_expr(wild_body, env, name_hint)

        # Sort arms by tag and dispatch.  Top-level and law-body contexts
        # share the same dispatch shape; `_build_nat_dispatch` handles the
        # `env.arity == 0` vs `env.arity > 0` split internally.
        nat_arms = sorted(nat_arms, key=lambda t: t[0])
        return self._build_nat_dispatch(
            nat_arms, wild_body, wild_var, scrutinee, env, name_hint
        )

    def _make_op2_dispatch(self, zero_val, succ_body, scrutinee_body, env: Env) -> Any:
        """
        Build an Elim dispatch: if scrutinee==0 return zero_val, else apply succ_body to pred.

        Uses canonical opcode 2 (Elim, formerly Case_) with 6 separate args:
        (p, l, a, z, m, o). p/l/a handlers are identity (unused for Nat scrutinee).
        """
        id_pin = P(self._ID_LAW)
        if env.arity == 0:
            return A(A(A(A(A(A(P(N(2)), id_pin), id_pin), id_pin),
                        zero_val), succ_body), scrutinee_body)
        else:
            step = P(N(2))
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

        Each succ_law is lambda-lifted from its enclosing env: it captures
        self_ref_name and free outer locals as leading parameters, then takes
        the predecessor as its final parameter.  The outer call site partial-
        applies the lifted law with the captured values.  This is what allows
        arm[1+] bodies in a recursive function to reference self and outer
        lambda params (issue #1a).
        """
        const2_pin = P(self._CONST2_LAW)

        def remaining_bodies(start_idx: int) -> list:
            bs = [arms_sorted[i][1] for i in range(start_idx, len(arms_sorted))]
            if wild_body is not None:
                bs.append(wild_body)
            return bs

        def make_succ_law(idx: int, outer_env: Env) -> Any:
            """Build a succ law for arm[idx..], lambda-lifting from outer_env."""
            if outer_env.arity == 0:
                # Top-level dispatch: no outer locals to lift.  self_ref_name
                # resolves via globals at this level, so propagation suffices.
                pred_env = Env(globals=outer_env.globals, arity=1,
                               self_ref_name=outer_env.self_ref_name)
                body = dispatch(idx, N(1), pred_env)
                return P(L(1, 0, body))

            # In-law: collect captures across all bodies in this dispatch chain.
            bound_set: set = {wild_var} if wild_var is not None else set()
            free_set: set = set()
            for b in remaining_bodies(idx):
                self._collect_free(b, bound_set, outer_env, free_set)
            free_locals = [k for k in outer_env.locals if k in free_set]

            uses_self = bool(outer_env.self_ref_name) and any(
                self._body_uses_self_ref(b, outer_env) for b in remaining_bodies(idx)
            )

            # Build lifted_env layout: [self?][captures...][predecessor]
            lifted_env = Env(globals=outer_env.globals, arity=0)
            lifted_env.self_ref_name = ''
            if uses_self:
                lifted_env.arity += 1
                si = lifted_env.arity
                lifted_env.locals[outer_env.self_ref_name] = si
                short = outer_env.self_ref_name.split('.')[-1]
                lifted_env.locals[short] = si
            for fv in free_locals:
                lifted_env.arity += 1
                lifted_env.locals[fv] = lifted_env.arity
            lifted_env.arity += 1
            pred_idx = lifted_env.arity

            body = dispatch(idx, N(pred_idx), lifted_env)
            name_nat = encode_name(f'{name_hint}_succ_{idx}')
            lifted_law = P(L(lifted_env.arity, name_nat, body))

            # Partial-apply at outer_env's perspective.
            result = lifted_law
            if uses_self:
                result = bapp(result, N(0))
            for fv in free_locals:
                result = bapp(result, N(outer_env.locals[fv]))
            return result

        def dispatch(idx: int, scr: Any, cur_env: Env) -> Any:
            """Build op2 dispatch for arm[idx], scrutinee=scr, in cur_env."""
            tag, body_expr = arms_sorted[idx]
            zero_val = self._compile_expr(body_expr, cur_env, f'{name_hint}_{tag}')

            if idx + 1 < len(arms_sorted):
                # More named arms: succ law dispatches on predecessor
                succ = make_succ_law(idx + 1, cur_env)
            elif wild_body is not None:
                if cur_env.arity > 0:
                    # In-law wildcard: lambda-lift outer-local captures and self-ref
                    # via _make_pred_succ_law. PatWild gets a synthetic var name —
                    # it occupies the predecessor slot but is never referenced.
                    pred_name = wild_var if wild_var is not None else '__pat_wild__'
                    succ = self._make_pred_succ_law(wild_body, pred_name, cur_env, f'{name_hint}_wild')
                else:
                    wild_val = self._compile_expr(wild_body, cur_env, f'{name_hint}_wild')
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

        # Outer level: arm[0] with the original scrutinee.
        tag0, body0 = arms_sorted[0]

        # When the smallest arm tag is positive, no named arm matches
        # scrutinee==0, so dispatch must route the wildcard there. Mirror
        # `_build_tag_chain`'s `first_tag > 0` handling: shift every tag
        # down by 1, recurse on the shifted arms with the predecessor as
        # the new scrutinee, and use the recursive result as the
        # succ-law for the outer op2. Without this, the all_nullary
        # path silently swaps the named-arm's body with the wildcard
        # body — the bug surfaced as D5 in AUDIT.md (4-level nested
        # match dropping outer-bound slot).
        if tag0 > 0:
            wild_val = (
                self._compile_expr(wild_body, env, f'{name_hint}_wild')
                if wild_body is not None
                else (A(N(0), N(0)) if env.arity > 0 else N(0))
            )
            shifted = [(t - 1, b) for t, b in arms_sorted]
            if env.arity == 0:
                pred_env = Env(globals=env.globals, arity=1,
                               self_ref_name=env.self_ref_name)
                inner = self._build_nat_dispatch(
                    shifted, wild_body, wild_var, N(1), pred_env,
                    f'{name_hint}_shifted',
                )
                succ = P(L(1, 0, inner))
                return self._make_op2_dispatch(wild_val, succ, scrutinee, env)
            # In-law: lambda-lift outer-local captures and self-ref into
            # the shifted-dispatch sub-law, parallel to `make_succ_law`.
            bound_set: set = {wild_var} if wild_var is not None else set()
            free_set: set = set()
            for _, b in shifted:
                self._collect_free(b, bound_set, env, free_set)
            if wild_body is not None:
                self._collect_free(wild_body, bound_set, env, free_set)
            free_locals = [k for k in env.locals if k in free_set]
            uses_self = bool(env.self_ref_name) and (
                any(self._body_uses_self_ref(b, env) for _, b in shifted)
                or (wild_body is not None
                    and self._body_uses_self_ref(wild_body, env))
            )
            lifted_env = Env(globals=env.globals, arity=0)
            lifted_env.self_ref_name = ''
            if uses_self:
                lifted_env.arity += 1
                si = lifted_env.arity
                lifted_env.locals[env.self_ref_name] = si
                short = env.self_ref_name.split('.')[-1]
                lifted_env.locals[short] = si
            for fv in free_locals:
                lifted_env.arity += 1
                lifted_env.locals[fv] = lifted_env.arity
            lifted_env.arity += 1
            pred_idx = lifted_env.arity
            inner = self._build_nat_dispatch(
                shifted, wild_body, wild_var, N(pred_idx), lifted_env,
                f'{name_hint}_shifted',
            )
            lifted_law = P(L(lifted_env.arity, 0, inner))
            succ = lifted_law
            if uses_self:
                succ = bapp(succ, N(0))
            for fv in free_locals:
                succ = bapp(succ, N(env.locals[fv]))
            return self._make_op2_dispatch(wild_val, succ, scrutinee, env)

        zero_val0 = self._compile_expr(body0, env, f'{name_hint}_{tag0}')

        if len(arms_sorted) == 1:
            # Single named arm + optional wildcard
            if wild_body is not None:
                if env.arity > 0:
                    # In-law: lambda-lift outer-local captures and self-ref. PatWild
                    # gets a synthetic var name in the predecessor slot.
                    pred_name = wild_var if wild_var is not None else '__pat_wild__'
                    succ = self._make_pred_succ_law(wild_body, pred_name, env, f'{name_hint}_wild')
                else:
                    wild_val = self._compile_expr(wild_body, env, f'{name_hint}_wild')
                    succ = A(const2_pin, wild_val)
            else:
                fallback = A(N(0), N(0)) if env.arity > 0 else N(0)
                succ = bapp(const2_pin, fallback) if env.arity > 0 else A(const2_pin, N(0))
            return self._make_op2_dispatch(zero_val0, succ, scrutinee, env)
        else:
            # Multiple arms: arm[1:] become succ laws that use the predecessor
            succ = make_succ_law(1, env)
            return self._make_op2_dispatch(zero_val0, succ, scrutinee, env)

    def _compile_con_match(self, scrutinee: Any, arms: list, env: Env, name_hint: str, loc=None) -> Any:
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
                    raise CodegenError(
                        f'codegen: unknown constructor {fq!r}',
                        getattr(pat, 'loc', None) or loc,
                    )
                con_arms.append((info, pat.args, body))
            elif isinstance(pat, (PatWild, PatVar)):
                wild_arm = (pat, body)

        if not con_arms:
            return self._compile_fallback_match(scrutinee, arms, env, name_hint, loc=loc)

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

        # When the explicit con_arms are all nullary but the matched type has
        # field-bearing sibling constructors (so the scrutinee can show up as
        # an App), `_build_nat_dispatch` alone is wrong: it builds an Elim
        # whose app-case is `id_pin`, which returns the App unchanged instead
        # of firing the wildcard.  Route through `_compile_adt_dispatch` in
        # that case so the App branch invokes the wildcard arm.  The fast
        # `_build_nat_dispatch` path is preserved for pure-nullary types
        # (Bool, fully-enumerated Tokens with no wildcard, etc.) where the
        # scrutinee is guaranteed to be a Nat tag.
        if all_nullary:
            wild_body = wild_arm[1] if wild_arm else None
            wild_var_con = wild_arm[0].name if (wild_arm and isinstance(wild_arm[0], PatVar)) else None
            type_has_field_sibling = False
            if wild_arm is not None:
                arm_type = next((info.type_name for info, _, _ in con_arms if info.type_name), '')
                if arm_type:
                    for ci in self.con_info.values():
                        if ci.type_name == arm_type and ci.arity > 0:
                            type_has_field_sibling = True
                            break
            if type_has_field_sibling:
                return self._compile_adt_dispatch(
                    scrutinee, con_arms, wild_arm, env, name_hint, loc=loc
                )
            tag_val = scrutinee
            arms_sorted = [(info.tag, body) for info, _, body in con_arms]
            return self._build_nat_dispatch(arms_sorted, wild_body, wild_var_con, tag_val, env, name_hint)
        else:
            # Constructor with fields: basic support
            # For a single-constructor type (like a record/singleton), just bind fields
            if len(con_arms) == 1:
                info, field_pats, body = con_arms[0]
                return self._compile_single_arm_field_bind(
                    scrutinee, info, field_pats, body, env, name_hint, wild_arm, loc=loc
                )
            else:
                return self._compile_adt_dispatch(scrutinee, con_arms, wild_arm, env, name_hint, loc=loc)

    def _compile_single_arm_field_bind(self, scrutinee, info, field_pats, body, env, name_hint,
                                      wild_arm=None, loc=None):
        """
        Bind field patterns of a constructor and compile the body.
        Uses _compile_adt_dispatch for single-constructor field extraction.
        wild_arm: the wildcard arm from the enclosing match, if any; passed through so
        non-matching constructors fall to the default instead of returning P(0).
        """
        if info.arity == 0:
            return self._compile_expr(body, env, name_hint)

        # Use a single-arm version of _compile_adt_dispatch, preserving wild_arm
        # so that constructors other than `info` dispatch to the wildcard body.
        single_arm = [(info, field_pats, body)]
        return self._compile_adt_dispatch(scrutinee, single_arm, wild_arm, env, name_hint, loc=loc)

    def _compile_adt_dispatch(self, scrutinee: Any, con_arms, wild_arm, env: Env, name_hint: str, loc=None) -> Any:
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
            # Build a succ law that dispatches on predecessor (= tag - 1).
            #
            # In an in-law context (env.arity > 0) the arm bodies and the
            # wildcard body may reference outer-lambda locals or the
            # enclosing function's self-ref.  Naively wrapping in
            # `P(L(1, ..., body))` would discard env.locals and fail to
            # compile any such reference (AUDIT.md A1).  Mirror the
            # capture-and-partial-apply pattern from `make_succ_law`
            # (~lines 1770-1816): collect free vars across all bodies in
            # the dispatch chain, build a lifted law of arity
            # `n_cap + 1` (captures plus predecessor), compile the inner
            # dispatch in the lifted env, then partial-apply the law to
            # env's bindings of the captures so the m_body slot receives
            # an arity-1 function on predecessor.
            if env.arity == 0:
                pred_env = Env(globals=env.globals, arity=1,
                               self_ref_name=env.self_ref_name)
                m_inner_body = self._build_nat_dispatch(
                    remaining_nullary, wild_body, wild_var_con, N(1), pred_env,
                    f'{name_hint}_tag_succ'
                )
                m_body = P(L(1, encode_name(f'{name_hint}_m'), m_inner_body))
            else:
                # Collect captures across remaining_nullary arm bodies and
                # the wildcard body.  Nullary constructors bind nothing, so
                # bound_set starts with only wild_var_con (the case3
                # wildcard's pattern var, if any).
                bound_set: set = {wild_var_con} if wild_var_con is not None else set()
                free_set: set = set()
                for _, b in remaining_nullary:
                    self._collect_free(b, bound_set, env, free_set)
                if wild_body is not None:
                    self._collect_free(wild_body, bound_set, env, free_set)
                free_locals = [k for k in env.locals if k in free_set]

                uses_self = bool(env.self_ref_name) and (
                    any(self._body_uses_self_ref(b, env) for _, b in remaining_nullary)
                    or (wild_body is not None and self._body_uses_self_ref(wild_body, env))
                )

                # Build lifted_env layout: [self?][captures...][predecessor]
                lifted_env = Env(globals=env.globals, arity=0)
                lifted_env.self_ref_name = ''
                if uses_self:
                    lifted_env.arity += 1
                    si = lifted_env.arity
                    lifted_env.locals[env.self_ref_name] = si
                    short = env.self_ref_name.split('.')[-1]
                    lifted_env.locals[short] = si
                for fv in free_locals:
                    lifted_env.arity += 1
                    lifted_env.locals[fv] = lifted_env.arity
                lifted_env.arity += 1
                pred_idx = lifted_env.arity

                m_inner_body = self._build_nat_dispatch(
                    remaining_nullary, wild_body, wild_var_con, N(pred_idx),
                    lifted_env, f'{name_hint}_tag_succ'
                )
                lifted_law = P(L(lifted_env.arity, encode_name(f'{name_hint}_m'), m_inner_body))

                # Partial-apply at env's perspective so the m slot is a
                # 1-arity function on predecessor.
                m_body = lifted_law
                if uses_self:
                    m_body = bapp(m_body, N(0))
                for fv in free_locals:
                    m_body = bapp(m_body, N(env.locals[fv]))
        elif wild_body is not None:
            wv = self._compile_expr(wild_body, env, f'{name_hint}_wild')
            m_body = A(const2_pin, wv) if env.arity == 0 else bapp(const2_pin, wv)
        else:
            m_body = A(const2_pin, body_nat(0, 0)) if env.arity == 0 else bapp(const2_pin, A(N(0), N(0)))

        # --- Build the app handler ---
        if not field_arms:
            # No explicit field-bearing arms.  When a wildcard arm exists, an
            # App scrutinee must fire it (otherwise `id_pin` would return the
            # App unchanged, which user code never expects).  Build a 2-arg
            # const-law over the wildcard body, lifting captures + self-ref
            # the same way `_build_field_arm_law` does.
            if wild_body is None:
                app_handler = id_pin
            else:
                app_handler = self._build_wild_app_handler(
                    wild_body, env, name_hint
                )
        else:
            app_handler = self._build_field_arm_law(field_arms, wild_body, wild_var_con, env, name_hint, loc=loc)

        # --- Assemble Case_ dispatch ---
        return self._make_reflect_dispatch(app_handler, z_body, m_body, scrutinee, env)

    def _build_wild_app_handler(self, wild_body, env: Env, name_hint: str) -> Any:
        """
        Build a 2-arg law that ignores its arguments and evaluates `wild_body`,
        for use as the App branch of an Elim dispatch when the match has no
        explicit field-bearing arms but does have a wildcard.

        Mirrors the capture-and-partial-apply pattern used in
        `_build_field_arm_law` so references to outer-lambda locals and the
        enclosing function's self-ref resolve correctly.
        """
        const2_pin = P(self._CONST2_LAW)

        if env.arity == 0:
            # Top-level: no locals or self-ref to lift.  const2_pin twice
            # over wild_body absorbs the (outer_fun, outer_arg) pair.
            wv = self._compile_expr(wild_body, env, f'{name_hint}_wild')
            return A(const2_pin, A(const2_pin, wv))

        # In-law: lift outer captures + self-ref into a (n_cap + 2)-arity
        # law that ignores its last two args (outer_fun, outer_arg).
        names: set = set()
        self._collect_all_names(wild_body, names)
        free_locals = [fv for fv in env.locals if fv in names]
        uses_self = bool(env.self_ref_name) and self._body_uses_self_ref(wild_body, env)

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

        # Final two slots are the unused (outer_fun, outer_arg) pair.
        handler_env.arity = n_cap + 2

        wv = self._compile_expr(wild_body, handler_env, f'{name_hint}_wild')
        lifted = P(L(handler_env.arity, encode_name(f'{name_hint}_wild_app'), wv))

        # Partial-apply at env's perspective so the slot in the parent law is
        # a 2-arg function over (outer_fun, outer_arg).
        out = lifted
        if uses_self:
            out = bapp(out, N(0))
        for fv in free_locals:
            out = bapp(out, N(env.locals[fv]))
        return out

    def _build_field_arm_law(self, field_arms, wild_body, wild_var_con, env: Env, name_hint: str, loc=None) -> Any:
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
                        m_inner = self._build_tag_chain(
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
                handler_body = self._build_tag_chain(
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
                inner_law_body = self._build_tag_chain(
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
                m_dispatch = self._build_tag_chain(
                    m_tag_val_pairs, wild_body, wild_var_con,
                    pred_ref_m, m_ext_env, f'{name_hint}_m_tag'
                )
                m_succ_law = P(L(n_cap + 1, 0, m_dispatch))
                m_body = m_succ_law
                for i in range(1, n_cap + 1):
                    m_body = bapp(m_body, N(i))
            else:
                m_body = (bapp(const2_pin, A(N(0), N(0))) if handler_env.arity > 0
                          else A(const2_pin, N(0)))

            handler_body = self._make_reflect_dispatch(
                inner_applied, z_body, m_body, N(fun_idx), handler_env
            )
        else:
            raise CodegenError(
                f'codegen: constructors with arity > 2 not yet supported in bootstrap match',
                loc,
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
            return A(A(A(A(A(A(P(N(2)), id_pin), id_pin), app_handler),
                        z_body), m_body), scrutinee)
        else:
            step = P(N(2))
            step = bapp(step, id_pin)
            step = bapp(step, id_pin)
            step = bapp(step, app_handler)
            step = bapp(step, z_body)
            step = bapp(step, m_body)
            step = bapp(step, scrutinee)
            return step

    def _build_tag_chain(self, tag_val_pairs: list, wild_body, wild_var,
                                         scrutinee: Any, env: Env, name_hint: str,
                                         wild_precompiled: Any = None) -> Any:
        """
        Build a Case_ dispatch on pre-compiled (tag, value) pairs.
        Like _build_nat_dispatch but tag_val_pairs are already-compiled PLAN values.

        wild_precompiled: optional pre-compiled PLAN value to use as the default
        (fallback) case when no arm matches. Takes precedence over wild_body.
        """
        const2_pin = P(self._CONST2_LAW)

        def _get_wild_val(e: Env) -> Any:
            """Resolve the wildcard/default value for the given env."""
            if wild_precompiled is not None:
                return wild_precompiled
            if wild_body is not None:
                return self._compile_expr(wild_body, e, name_hint + '_wild')
            return None

        if not tag_val_pairs:
            wv = _get_wild_val(env)
            if wv is not None:
                return wv
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
                body = self._build_tag_chain(
                    remaining, wild_body, wild_var, pred_ref, ext_env, name_hint,
                    wild_precompiled=wild_precompiled
                )
            else:
                wv = _get_wild_val(ext_env)
                if wv is not None:
                    body = wv
                else:
                    body = body_nat(0, ext_env.arity)
            succ_law = P(L(ext_env.arity, 0, body))
            return partially_apply(succ_law, env)

        zero_val = tag_val_pairs[0][1]
        first_tag = tag_val_pairs[0][0]

        # Whenever first_tag > 0, the outer op2's zero branch must be wild
        # (no arm matches scrutinee=0); the chain of succ laws steps down
        # one tag at a time until reaching the first arm.  The single-arm
        # case below was already handling this; the multi-arm case
        # previously ignored first_tag and used tag_val_pairs[0][1] as
        # zero_val unconditionally, which silently mis-routes the dispatch.
        if first_tag > 0:
            wild_val = _get_wild_val(env)
            z_val = wild_val if wild_val is not None else body_nat(0, env.arity)
            ext_env, pred_ref = make_ext_env(env)
            shifted = [(t - 1, v) for t, v in tag_val_pairs]
            inner = self._build_tag_chain(
                shifted, wild_body, wild_var,
                pred_ref, ext_env, name_hint,
                wild_precompiled=wild_precompiled,
            )
            succ_law = P(L(ext_env.arity, 0, inner))
            succ = partially_apply(succ_law, env)
            return self._make_op2_dispatch(z_val, succ, scrutinee, env)

        if len(tag_val_pairs) == 1:
            wild_val = _get_wild_val(env)
            if wild_val is not None:
                const_wild = bapp(const2_pin, wild_val) if env.arity > 0 else A(const2_pin, wild_val)
            else:
                const_wild = bapp(const2_pin, A(N(0), N(0))) if env.arity > 0 else A(const2_pin, N(0))
            return self._make_op2_dispatch(zero_val, const_wild, scrutinee, env)

        succ = make_succ_compiled(0)
        return self._make_op2_dispatch(zero_val, succ, scrutinee, env)

    def _build_elim_app_dispatch(self, zero_val: Any, app_handler: Any,
                                    scrutinee: Any, env: Env) -> Any:
        """
        Build Case_ dispatch where the App branch uses app_handler.
        z = zero_val (for Nat 0), a = app_handler, m = const(0) (shouldn't fire), p/l = id.
        """
        id_pin = P(self._ID_LAW)
        const2_pin = P(self._CONST2_LAW)
        m_body = bapp(const2_pin, A(N(0), N(0))) if env.arity > 0 else A(const2_pin, N(0))
        if env.arity == 0:
            return A(A(A(A(A(A(P(N(2)), id_pin), id_pin), app_handler),
                        zero_val), m_body), scrutinee)
        else:
            step = P(N(2))
            step = bapp(step, id_pin)
            step = bapp(step, id_pin)
            step = bapp(step, app_handler)
            step = bapp(step, zero_val)
            step = bapp(step, m_body)
            step = bapp(step, scrutinee)
            return step

    def _compile_fallback_match(self, scrutinee: Any, arms: list, env: Env, name_hint: str, loc=None) -> Any:
        """Match with only wildcard/variable patterns — just use the first arm's body."""
        if not arms:
            raise CodegenError('codegen: empty match', loc)
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

        Three cases:

        - **Top-level (`env.arity == 0`).** Inline as a global binding
          so the body can reference it; no PLAN-level let form needed.
        - **In a law body, at the body root (`env.top_of_law`).** Emit
          the PLAN let form `A(A(N(1), rhs), body)`. Reaver's `lawExp`
          parser recognises this shape only at the law's body root,
          between the sig and the final body form.
        - **In a law body, nested inside an expression** (e.g. the
          body of a match arm). Lambda-lift: `let x = rhs in body`
          becomes `App(Pin(SubLaw), captures…, rhs)` where `SubLaw`
          is a fresh law over `[captures…, x]` whose body is `body`.
          This avoids emitting a `(1 …)` form anywhere Reaver's text
          parser would refuse it (AUDIT.md D8). The capture pattern
          mirrors `_build_field_arm_law` and `_make_pred_succ_law`.
        """
        pat = expr.pattern
        pat_name = self._pat_var_name(pat)

        rhs_val = self._compile_expr(expr.rhs, env, pat_name)

        if env.arity == 0:
            # Top-level: inline the binding as a global so body can reference it
            body_env = env.child()
            body_env.globals[pat_name] = rhs_val
            return self._compile_expr(expr.body, body_env, name_hint)

        if env.top_of_law:
            # Law body root: native PLAN let form. judge processes
            # A(A(N(1), rhs), body) by evaluating rhs, binding it to
            # the NEXT slot, then evaluating body. Preserve top_of_law
            # through the body recursion so a chain of top-level lets
            # all use this fast path.
            body_env = env.child()
            new_idx = env.arity + 1
            body_env.locals[pat_name] = new_idx
            body_env.arity = new_idx
            body_env.top_of_law = True
            body_val = self._compile_expr(expr.body, body_env, name_hint)
            return A(A(N(1), rhs_val), body_val)

        # Nested let: lambda-lift the body into a sub-law that takes
        # captures + the let-bound name as parameters.
        bound = {pat_name}
        free_set: set = set()
        self._collect_free(expr.body, bound, env, free_set)
        free_locals = [k for k in env.locals if k in free_set]
        uses_self = bool(env.self_ref_name) and self._body_uses_self_ref(expr.body, env)

        # Sub-law env layout: [self?][captures…][pat_name]
        sub_env = Env(globals=env.globals, arity=0)
        sub_env.self_ref_name = ''
        sub_env.top_of_law = True

        n_cap = 0
        if uses_self:
            n_cap += 1
            sub_env.arity = n_cap
            sub_env.locals[env.self_ref_name] = n_cap
            short = env.self_ref_name.split('.')[-1]
            sub_env.locals[short] = n_cap
        for fv in free_locals:
            n_cap += 1
            sub_env.arity = n_cap
            sub_env.locals[fv] = n_cap
        n_cap += 1
        sub_env.arity = n_cap
        sub_env.locals[pat_name] = n_cap

        body_val = self._compile_expr(expr.body, sub_env, name_hint)
        sub_law = L(n_cap, encode_name(pat_name) if pat_name else 0, body_val)

        # Apply: Pin(SubLaw) self? captures… rhs.
        # Pin keeps it from being forced before saturation by `kal`.
        result = P(sub_law)
        if uses_self:
            result = bapp(result, N(0))
        for fv in free_locals:
            result = bapp(result, N(env.locals[fv]))
        result = bapp(result, rhs_val)
        return result

    # -----------------------------------------------------------------------
    # Programmer pins
    # -----------------------------------------------------------------------

    def _compile_expr_pin(self, expr: ExprPin, env: Env, name_hint: str) -> Any:
        """Compile @name = rhs  body.

        Programmer pins are erased into the body's globals — the body
        is at the same syntactic position as the pin form, so preserve
        `top_of_law` so a subsequent let-chain in the body still gets
        the native `(1 rhs body)` form.
        """
        rhs_val = self._compile_expr(expr.rhs, env, expr.name)
        pinned = P(rhs_val) if not is_pin(rhs_val) else rhs_val

        body_env = env.child()
        body_env.globals[expr.name] = pinned
        body_env.top_of_law = env.top_of_law
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
            # Negation: not defined for Nat; return 0 as placeholder.
            # Body context uses the quote form `A(N(0), N(0))` (literal 0)
            # rather than `P(N(0))` (Pin opcode pin) — the latter triggers
            # un-dispatchable `op 0` on saturation under Reaver. See
            # `body_nat`.
            return N(0) if env.arity == 0 else A(N(0), N(0))
        if expr.op == '¬':
            # Boolean not: if x then False else True
            # Use the same Case_ dispatch helper (opcode 3, 6 args).
            false_val = N(0) if env.arity == 0 else A(N(0), N(0))
            true_val = N(1) if (env.arity == 0 or 1 > env.arity) else A(N(0), N(1))
            succ_fn = self._make_const_law(N(0), name_hint + '_not_succ') \
                if env.arity == 0 \
                else self._make_const_law_body(A(N(0), N(0)), env, name_hint + '_not_succ')
            return self._make_op2_dispatch(true_val, succ_fn, operand, env)
        return operand

    def _is_tuple_match(self, arms) -> bool:
        for pat, _, _ in arms:
            if isinstance(pat, PatTuple):
                return True
        return False

    def _compile_tuple_match(self, scrutinee, arms, env, name_hint, loc=None):
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
                        f'codegen: only 2-tuples supported in bootstrap, got {len(pat.pats)}-tuple',
                        getattr(pat, 'loc', None) or loc,
                    )
                con_arms.append((pair_info, list(pat.pats), body))
            elif isinstance(pat, (PatWild, PatVar)):
                wild_arm = (pat, body)
        if not con_arms:
            return self._compile_fallback_match(scrutinee, arms, env, name_hint, loc=loc)
        return self._compile_adt_dispatch(scrutinee, con_arms, wild_arm, env, name_hint, loc=loc)

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
            raise CodegenError(
                'codegen: fix requires at least one parameter (self-reference)',
                getattr(expr, 'loc', None),
            )

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

        Produces a CPS value: λ(dp, ko_open) → comp(dispatch(dp), compose_open(ko_open, ret_fn))

        dispatch_fn has arity n_cap+4: [caps] + [dp] + [tag] + [arg] + [k_open]
        Applying dp partially gives a 3-arg dispatch function.
        compose_open(ko_open, ret_fn) gives (dispatch', x) → ko_open(dispatch', ret_fn(x)).

        To run the resulting CPS value, apply it to (null_dispatch, id_open):
          handle_cps(null_dispatch, id_open) → raw value
        Or use the `run` builtin.
        """
        # Collect free variables from all sub-expressions
        all_free: set = set()
        self._collect_free(expr.comp, set(), env, all_free)

        return_arm = next((a for a in expr.arms if isinstance(a, HandlerReturn)), None)
        op_arms    = [a for a in expr.arms if isinstance(a, HandlerOp)]

        if return_arm:
            pat_binds = self._pat_binds(return_arm.pattern)
            self._collect_free(return_arm.body, pat_binds, env, all_free)
        for arm in op_arms:
            arm_bound: set = {arm.resume}
            for p in arm.arg_pats:
                arm_bound |= self._pat_binds(p)
            self._collect_free(arm.body, arm_bound, env, all_free)

        free_locals = [k for k in env.locals if k in all_free]
        n_cap = len(free_locals)

        # CPS handle law: L(n_cap + 2, name, body)
        # Layout: [cap_1..cap_n_cap] + [dp] + [ko]
        cps_env = Env(globals=env.globals, arity=n_cap + 2,
                      self_ref_name=env.self_ref_name)
        for i, fv in enumerate(free_locals, 1):
            cps_env.locals[fv] = i
        dp_idx = n_cap + 1
        ko_idx = n_cap + 2

        # Compile comp, dispatch, return in cps_env
        comp_val    = self._compile_expr(expr.comp, cps_env, name_hint + '_comp')
        dispatch_fn = self._compile_dispatch_fn(op_arms, cps_env, name_hint)
        return_fn   = self._compile_return_fn(return_arm, cps_env, name_hint)

        # dispatch(dp): apply dp as first arg to dispatch_fn
        dispatch_applied = bapp(dispatch_fn, N(dp_idx))

        # composed_k_open = compose_open(ko_open, return_fn)
        # = (dispatch', x) → ko_open(dispatch', return_fn(x))
        composed_k_open = bapp(bapp(P(self._COMPOSE_OPEN), N(ko_idx)), return_fn)

        # CPS body: comp(dispatch(dp), composed_k_open)
        cps_body = bapp(bapp(comp_val, dispatch_applied), composed_k_open)
        cps_law  = L(n_cap + 2, encode_name(f'{name_hint}_handle'), cps_body)

        # Partially apply to captures from outer env
        if env.arity == 0:
            result = cps_law
            for fv in free_locals:
                result = A(result, env.globals.get(fv, N(0)))
        else:
            result = P(cps_law)
            for fv in free_locals:
                result = bapp(result, N(env.locals[fv]))
        return result

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

    # Virtual de Bruijn index for resume during arm body compilation.
    # Substituted with the real resume expression before embedding in the law body.
    _RESUME_VIRTUAL_IDX = 9999999

    def _compile_dispatch_fn(self, op_arms: list, env: Env, name_hint: str) -> Any:
        """
        Build the handler dispatch law: L(n_cap+4, name, body)
        Law args: [cap_1..cap_n_cap] + [dispatch_parent] + [op_tag] + [op_arg] + [k_open]

        Open-continuation protocol (M13.3): k_open is 2-arg (dispatch, value).
        For deep arms: resume = k_open(dispatch_current) — handler reinstalled.
        For once arms: resume = k_open(dispatch_parent) — handler discharged.

        Dispatches on op_tag via nat ladder; each arm binds op_arg and resume.
        Unhandled tags forward to dispatch_parent(op_tag, op_arg, k_open).
        Free outer locals are lambda-lifted as leading parameters.
        """
        if not op_arms:
            # No op arms: pure forwarder — dispatch_parent(tag, arg, k_open_forwarded)
            # Wrap k_open with forward_k to preserve this handler layer.
            # dispatch_fn_base = N(0) (self-ref, no captures)
            # k_open_forwarded = forward_k(k_open, N(0), dispatch', v)
            k_fwd = bapp(bapp(P(self._FORWARD_K), N(4)), N(0))
            return P(L(4, encode_name(f'{name_hint}_dispatch'),
                       bapp(bapp(bapp(N(1), N(2)), N(3)), k_fwd)))

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

        # Build dispatch_env: [caps...] + [dispatch_parent] + [op_tag] + [op_arg] + [k_open]
        dispatch_env = Env(globals=env.globals, arity=n_cap + 4,
                           self_ref_name=env.self_ref_name)
        for i, fv in enumerate(free_locals, 1):
            dispatch_env.locals[fv] = i
        dp_idx     = n_cap + 1
        op_tag_idx = n_cap + 2
        op_arg_idx = n_cap + 3
        k_idx      = n_cap + 4

        # Build dispatch_fn_base: self-ref applied to captures but NOT dp.
        # This is the dispatch function that takes dp as its next argument.
        # Used by forward_k to thread the correct parent dispatch through
        # nested handler layers.
        dispatch_fn_base = N(0)
        for i in range(1, n_cap + 1):
            dispatch_fn_base = bapp(dispatch_fn_base, N(i))

        # dispatch_current = dispatch_fn_base(dp) — full dispatch for deep arms.
        dispatch_current = bapp(dispatch_fn_base, N(dp_idx))

        # Build forwarding default: wrap k_open so nested handlers are preserved.
        # forward_k(k_open, dispatch_fn_base, dispatch', v) = k_open(dispatch_fn_base(dispatch'), v)
        # The parent dispatch applies its own dispatch' to k_open_forwarded,
        # which reinstalls this handler with dispatch' as the new parent.
        k_open_forwarded = bapp(bapp(P(self._FORWARD_K), N(k_idx)), dispatch_fn_base)
        forward_body = bapp(bapp(bapp(N(dp_idx), N(op_tag_idx)), N(op_arg_idx)), k_open_forwarded)

        # Compile each arm's body in arm_env.
        # Resume is compiled at a virtual index, then substituted with the
        # appropriate open-continuation application (deep or shallow).
        tag_val_pairs = []
        for arm in op_arms:
            tag = self._lookup_op_tag(arm.op_name, getattr(arm, 'loc', None))
            arm_env = dispatch_env.child()
            for p in arm.arg_pats:
                pn = self._pat_var_name(p)
                if pn not in ('_wild', '_pat', '__'):
                    arm_env.locals[pn] = op_arg_idx
            arm_env.locals[arm.resume] = self._RESUME_VIRTUAL_IDX
            body_val = self._compile_expr(arm.body, arm_env, f'{name_hint}_op{tag}')

            # Substitute virtual resume with actual open-continuation application.
            if arm.once:
                # Shallow: resume = k_open(dispatch_parent) — handler discharged
                resume_expr = bapp(N(k_idx), N(dp_idx))
            else:
                # Deep: resume = k_open(dispatch_current) — handler reinstalled
                resume_expr = bapp(N(k_idx), dispatch_current)
            body_val = _subst_virtual_resume(body_val, self._RESUME_VIRTUAL_IDX, resume_expr)
            tag_val_pairs.append((tag, body_val))

        tag_val_pairs.sort(key=lambda t: t[0])

        dispatch_body = self._build_tag_chain(
            tag_val_pairs, None, None,
            N(op_tag_idx), dispatch_env, f'{name_hint}_dispatch',
            wild_precompiled=forward_body
        )

        dispatch_law = P(L(n_cap + 4, encode_name(f'{name_hint}_dispatch'), dispatch_body))

        # Partially apply to free_locals in outer env (NOT to dispatch_parent)
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

    def _lookup_op_tag(self, op_name: str, loc=None) -> int:
        """Look up the CPS tag for an effect operation by name."""
        if op_name in self.effect_op_tags:
            return self.effect_op_tags[op_name]
        for k, v in self.effect_op_tags.items():
            if k.endswith('.' + op_name):
                return v
        raise CodegenError(f'codegen: unknown effect operation {op_name!r}', loc)

    def _compile_do(self, expr: ExprDo, env: Env, name_hint: str) -> Any:
        """
        Compile: x ← rhs_comp; body_expr   (effectful bind)

        Result is a CPS computation value (a 2-arg function taking dispatch and k_open).
        Encoding: λ dispatch k_open_outer → rhs dispatch (inner_cont_open caps k_open_outer)

        Open-continuation protocol (M13.3): continuations are 2-arg (dispatch, value).
        The inner continuation is NOT applied with dispatch — it remains "open" so
        the dispatch function can choose deep (current dispatch) or shallow (parent
        dispatch) when handling an operation.

        Outer law (arity = n_cap+2):
          [caps...] + [dispatch] + [k_open_outer]
          body = rhs_val dispatch (inner_cont_open caps k_open_outer)

        Inner continuation law (arity = n_cap+3):
          [caps...] + [k_open_outer] + [dispatch] + [x]
          body = body_comp dispatch k_open_outer  (with x bound)

        Partial application: inner_cont_open(caps, k_open_outer) is 2-arg (dispatch, x).
        """
        # Collect free vars from rhs and body (excluding the do-bound name x)
        all_free: set = set()
        self._collect_free(expr.rhs, set(), env, all_free)
        self._collect_free(expr.body, {expr.name}, env, all_free)
        free_locals = [k for k in env.locals if k in all_free]
        n_cap = len(free_locals)

        # Indices in the outer (n_cap+2)-arg law
        outer_dispatch_idx = n_cap + 1
        outer_k_open_idx   = n_cap + 2

        # Indices in the inner (n_cap+3)-arg continuation law
        # Reordered: k_open_outer BEFORE dispatch so partial application
        # with (caps, k_open_outer) gives a 2-arg open continuation.
        inner_k_open_idx   = n_cap + 1
        inner_dispatch_idx = n_cap + 2
        inner_x_idx        = n_cap + 3

        # --- Build inner continuation law ---
        inner_env = Env(globals=env.globals, arity=n_cap + 3,
                        self_ref_name=env.self_ref_name)
        for i, fv in enumerate(free_locals, 1):
            inner_env.locals[fv] = i
        inner_env.locals['__dispatch__'] = inner_dispatch_idx
        inner_env.locals['__k__']        = inner_k_open_idx
        inner_env.locals[expr.name]      = inner_x_idx  # do-bound variable

        body_cps = self._compile_expr(expr.body, inner_env, name_hint + '_body')
        # Apply body_cps to dispatch and k_open_outer
        inner_body = bapp(bapp(body_cps, N(inner_dispatch_idx)), N(inner_k_open_idx))
        inner_law  = P(L(n_cap + 3, encode_name(name_hint + '_cont'), inner_body))

        # --- Build outer 2-arg (+ captures) law ---
        outer_env = Env(globals=env.globals, arity=n_cap + 2,
                        self_ref_name=env.self_ref_name)
        for i, fv in enumerate(free_locals, 1):
            outer_env.locals[fv] = i
        outer_env.locals['__dispatch__'] = outer_dispatch_idx
        outer_env.locals['__k__']        = outer_k_open_idx

        rhs_val = self._compile_expr(expr.rhs, outer_env, name_hint + '_rhs')

        # Partially apply inner_law to captures + k_open_outer (NOT dispatch).
        # Result is a 2-arg open continuation: (dispatch, x) → body(dispatch, k_open_outer)
        inner_cont_open = inner_law
        for i in range(1, n_cap + 1):
            inner_cont_open = bapp(inner_cont_open, N(i))
        inner_cont_open = bapp(inner_cont_open, N(outer_k_open_idx))

        # Outer body: rhs_comp dispatch inner_cont_open
        outer_body = bapp(bapp(rhs_val, N(outer_dispatch_idx)), inner_cont_open)
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

        # Record this SCC group for Glass IR rendering
        self.scc_groups.append(list(scc_names))

        scc_indices = {name: i for i, name in enumerate(scc_names)}

        # --- Build selector law ---
        # arity = n+1; args: law_0 .. law_{n-1}, index_i
        # body: nat dispatch on N(n+1) returning N(1)..N(n)
        selector_env = Env(globals=self.env.globals, arity=n + 1)
        tag_val_pairs = [(j, N(j + 1)) for j in range(n)]
        dispatch_body = self._build_tag_chain(
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

def compile_program(
    program: Program,
    module: str = 'Main',
    pre_compiled: dict | None = None,
    pre_class_methods: dict | None = None,
    pre_class_defaults: dict | None = None,
    pre_class_constraints: dict | None = None,
    expr_types: dict | None = None,
) -> dict[str, Any]:
    """
    Compile a resolved, type-checked program.

    Parameters:
        program:              Resolved Program AST.
        module:               FQ module name, e.g. 'Core.Nat'.
        pre_compiled:         PLAN values from already-compiled upstream modules,
                              made available as globals so cross-module references
                              compile correctly.  Does NOT appear in the returned dict.
        pre_class_methods:    class_fq → set(method_fq_names) from upstream module
                              Env objects.  Enables cross-module instance resolution:
                              constrained functions can reference classes and instances
                              defined in other modules.
        pre_class_defaults:   class_fq → {method_short → resolved Expr} from upstream
                              Compiler instances. Enables cross-module default method
                              fallback in instances.
        pre_class_constraints: class_fq → [(superclass_short, type_args)] from upstream
                              Compiler instances. Enables cross-module superclass
                              constraint expansion.

    Returns:
        dict mapping FQ name → PLAN value (this module's definitions only)
    """
    compiler = Compiler(module=module, pre_compiled=pre_compiled,
                        pre_class_methods=pre_class_methods,
                        pre_class_defaults=pre_class_defaults,
                        pre_class_constraints=pre_class_constraints,
                        expr_types=expr_types)
    return compiler.compile(program)
