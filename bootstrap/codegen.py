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
    DeclLet, DeclType, DeclExt, DeclClass, DeclInst,
    Constructor,
    ExprVar, ExprApp, ExprLam, ExprLet, ExprMatch, ExprIf,
    ExprNat, ExprText, ExprBytes, ExprHexBytes, ExprUnit, ExprTuple,
    ExprPin, ExprOp, ExprUnary, ExprAnn, ExprFix,
    PatVar, PatWild, PatCon, PatNat, PatTuple,
)
from dev.harness.plan import P, L, A, N, is_nat, is_pin, is_law, is_app


class CodegenError(Exception):
    pass


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

    def child(self) -> 'Env':
        """Return a shallow copy for a new scope."""
        return Env(globals=self.globals, locals=dict(self.locals), arity=self.arity)

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
        # Register builtin constructors from scope resolver.
        # Bool: False = tag 0, True = tag 1 (conventional ordering)
        # These match the scope resolver's pre-declared 'True'/'False' bindings.
        self._register_builtins()

    # -----------------------------------------------------------------------
    # Builtins
    # -----------------------------------------------------------------------

    def _register_builtins(self) -> None:
        """Register builtin constructors (True, False, Unit, Never) in the global env."""
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

        # Pass 3: compile let declarations in order
        for decl in program.decls:
            if isinstance(decl, DeclLet):
                self._compile_let(decl)

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

    def _register_ext(self, decl: DeclExt) -> None:
        """Register external module items as opaque values."""
        mod_path = '.'.join(decl.module_path)
        for item in decl.items:
            if item.is_type:
                continue
            fq = f'{mod_path}.{item.name}'
            # External ops are represented as opaque nats (placeholder).
            # In a real compiler, these would be resolved to BPLAN primitives.
            # For the bootstrap, we use a sentinel pin.
            stub = P(N(encode_name(fq)))
            self.compiled[fq] = stub
            self.env.globals[fq] = stub

    # -----------------------------------------------------------------------
    # Let declarations
    # -----------------------------------------------------------------------

    def _compile_let(self, decl: DeclLet) -> None:
        """Compile a top-level let declaration."""
        fq = f'{self.module}.{decl.name}'
        env = Env(globals=self.env.globals, arity=0)

        val = self._compile_expr(decl.body, env, name_hint=decl.name)

        self.compiled[fq] = val
        self.env.globals[fq] = val

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

        # Local de Bruijn reference
        if fq in env.locals:
            idx = env.locals[fq]
            # Inside a law body, de Bruijn index is just the nat itself
            return N(idx)

        # Global reference (top-level let or constructor)
        if fq in env.globals:
            val = env.globals[fq]
            if env.arity == 0:
                # At top level: return the value directly
                return val
            else:
                # Inside a law body: pin the value to lift it out of de Bruijn range
                return P(val) if not is_pin(val) else val

        # Last resort: try with module prefix stripped (for builtins)
        short = fq.split('.')[-1]
        if short in env.globals:
            val = env.globals[short]
            if env.arity == 0:
                return val
            return P(val) if not is_pin(val) else val

        raise CodegenError(f'codegen: unbound variable {fq!r}')

    def _compile_app(self, expr: ExprApp, env: Env, name_hint: str) -> Any:
        """Compile function application."""
        fn_val = self._compile_expr(expr.fun, env)
        arg_val = self._compile_expr(expr.arg, env)

        if env.arity == 0:
            # At top level: build a direct App node
            return A(fn_val, arg_val)
        else:
            # Inside a law body: use bapp notation (0 f x)
            return bapp(fn_val, arg_val)

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

    def _compile_lam_as_law(self, params: list, body_expr: Any, env: Env, name_hint: str) -> Any:
        """Compile params + body as a top-level (named) law."""
        # Build body env with params bound
        body_env = Env(globals=env.globals, arity=0)
        for pat in params:
            param_name = self._pat_var_name(pat)
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

        Uses opcode 2 (P(N(2))), which is the match/dispatch opcode.
        Op 2 takes ONE packed argument: a 6-deep App chain:
          A(A(A(A(A(A(base, p), l), a), z), m), o)
        where p,l,a handle pin/law/app cases (unused for Bool/Nat),
        z is the zero (False) branch, m is the succ function (receives pred),
        and o is the scrutinee.

        Since op 2 has arity 1 (P(N(2)) applied to the pack), we need to
        build the full 6-arg pack as a single expression.

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
            # Top-level: build pack directly (no de Bruijn resolution needed)
            # const_then = partial application of const2 to then_body
            # When called with predecessor p: const2(then_body, p) = then_body
            const_then = A(const2_pin, then_body)
            dummy = N(999)
            pack = A(A(A(A(A(A(dummy, id_pin), id_pin), id_pin),
                        else_body), const_then), cond_body)
            return A(P(N(2)), pack)
        else:
            # Law body: use bapp chains so de Bruijn refs are resolved by kal.
            # Use a literal nat > env.arity as dummy (guaranteed not a de Bruijn ref)
            dummy = N(env.arity + 100)
            # const_then: bapp(const2_pin, then_body) resolves to A(const2_pin, then_val)
            const_then_body = bapp(const2_pin, then_body)
            # Build the 6-deep pack step by step using bapp
            step = dummy
            step = bapp(step, id_pin)        # p
            step = bapp(step, id_pin)        # l
            step = bapp(step, id_pin)        # a
            step = bapp(step, else_body)     # z (False branch)
            step = bapp(step, const_then_body)  # m (True branch fn)
            step = bapp(step, cond_body)     # o (scrutinee)
            return bapp(P(N(2)), step)

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
            return self._nat_match_top(nat_arms, wild_body, scrutinee, op2, env, name_hint)
        else:
            return self._nat_match_body(nat_arms, wild_body, scrutinee, op2, env, name_hint)

    def _make_op2_dispatch(self, zero_val, succ_body, scrutinee_body, env: Env) -> Any:
        """
        Build an op2 dispatch: if scrutinee==0 return zero_val, else apply succ_body to pred.

        This is the shared helper for nat-match and con-match.
        succ_body must be a PLAN value (or bapp expression) that accepts 1 argument.

        Uses the 6-arg op2 pack — see _compile_if for the rationale.
        """
        id_pin = P(self._ID_LAW)
        if env.arity == 0:
            dummy = N(999)
            pack = A(A(A(A(A(A(dummy, id_pin), id_pin), id_pin),
                        zero_val), succ_body), scrutinee_body)
            return A(P(N(2)), pack)
        else:
            dummy = N(env.arity + 100)
            step = dummy
            step = bapp(step, id_pin)
            step = bapp(step, id_pin)
            step = bapp(step, id_pin)
            step = bapp(step, zero_val)
            step = bapp(step, succ_body)
            step = bapp(step, scrutinee_body)
            return bapp(P(N(2)), step)

    def _build_nat_dispatch(self, arms_sorted, wild_body, scrutinee, env, name_hint):
        """
        Build a nat/con dispatch using opcode 2.

        For a single arm: op2(zero_val, const2(wild_or_0), scrutinee)
        For multiple arms: op2(arm0_val, succ_law_1, scrutinee)
          where succ_law_1 = L(1, 0, op2(arm1_val, succ_law_2, N(1)))
          and N(1) inside each succ law is the predecessor (de Bruijn index 1).

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

    def _nat_match_top(self, nat_arms, wild_body, scrutinee, op2, env, name_hint):
        """Build nat match in top-level (non-law-body) context."""
        nat_arms = sorted(nat_arms, key=lambda t: t[0])
        return self._build_nat_dispatch(nat_arms, wild_body, scrutinee, env, name_hint)

    def _nat_match_body(self, nat_arms, wild_body, scrutinee, op2, env, name_hint):
        """Build nat match inside a law body."""
        nat_arms = sorted(nat_arms, key=lambda t: t[0])
        return self._build_nat_dispatch(nat_arms, wild_body, scrutinee, env, name_hint)

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
            arms_sorted = [(info.tag, body) for info, _, body in con_arms]
            return self._build_nat_dispatch(arms_sorted, wild_body, tag_val, env, name_hint)
        else:
            # Constructor with fields: basic support
            # For a single-constructor type (like a record/singleton), just bind fields
            if len(con_arms) == 1:
                info, field_pats, body = con_arms[0]
                return self._compile_con_body_extraction(
                    scrutinee, info, field_pats, body, env, name_hint
                )
            else:
                raise CodegenError(
                    f'codegen: multi-constructor match with fields not yet supported in bootstrap; '
                    f'use a flat nullary enum or single-constructor type'
                )

    def _compile_con_body_extraction(self, scrutinee, info, field_pats, body, env, name_hint):
        """
        Bind field patterns of a constructor and compile the body.
        Handles up to 2 fields using opcode 1 (reflect).
        """
        if info.arity == 0:
            return self._compile_expr(body, env, name_hint)

        # Build a local let-like environment by threading field extractions
        # For arity 1: scrutinee = A(tag, field) → field = snd(scrutinee)
        # For arity 2: scrutinee = A(A(tag, f1), f2) → f2 = arg(scrutinee), f1 = arg(fun(scrutinee))
        # We don't yet have a clean way to express this without adding more infrastructure.
        # For now: compile body with _ for each field (field values not bound)
        # TODO: full field extraction
        field_env = env.child() if hasattr(env, 'child') else env
        # Bind each field pat as a local let binding the appropriate extracted value
        # For simplicity in bootstrap: bind as the scrutinee itself (imprecise but compiles)
        return self._compile_expr(body, env, name_hint)

    def _compile_fallback_match(self, scrutinee: Any, arms: list, env: Env, name_hint: str) -> Any:
        """Match with only wildcard/variable patterns — just use the first arm's body."""
        if not arms:
            raise CodegenError('codegen: empty match')
        pat, _, body = arms[0]
        arm_env = env.child() if hasattr(env, 'child') else env
        if isinstance(pat, PatVar):
            # Bind pat.name to scrutinee via local let
            # In law body context: this requires extending env
            # For now: treat as wildcard
            pass
        return self._compile_expr(body, arm_env, name_hint)

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
            # Boolean not: (if x then False else True)
            false_val = N(0)
            true_val = N(1)
            op2 = P(N(2))
            succ_fn = self._make_const_law(false_val, name_hint + '_not_succ') \
                if env.arity == 0 \
                else self._make_const_law_body(P(N(0)), env, name_hint + '_not_succ')
            if env.arity == 0:
                return A(A(A(op2, true_val), succ_fn), operand)
            else:
                return bapp(bapp(bapp(op2, true_val), succ_fn), operand)
        return operand

    # -----------------------------------------------------------------------
    # Tuples
    # -----------------------------------------------------------------------

    def _compile_tuple(self, expr: ExprTuple, env: Env, name_hint: str) -> Any:
        """Compile a tuple as a tagged row (tag 0)."""
        # Tuple (a, b) = A(A(N(0), a), b)
        elems = [self._compile_expr(e, env) for e in expr.elems]
        result = P(N(0)) if (env.arity > 0 and 0 <= env.arity) else N(0)
        for e in elems:
            if env.arity == 0:
                result = A(result, e)
            else:
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

        # First param is the self-reference; build a law with arity = len(params)
        body_env = Env(globals=env.globals, arity=0)
        for pat in params:
            pn = self._pat_var_name(pat)
            body_env = body_env.bind_param(pn)

        body_val = self._compile_expr(body_expr, body_env)
        name_nat = encode_name(name_hint) if name_hint else 0
        law = L(len(params), name_nat, body_val)

        if env.arity == 0:
            return law
        else:
            return P(law)


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
