"""
PLAN Virtual Machine — Python reference evaluator.

Implements the canonical 3-opcode PLAN ABI (Pin/Law/Elim at 0/1/2) plus the
BPLAN named-op dispatch (op 66 cases), per vendor/reaver/src/hs/Plan.hs at
the SHA pinned in vendor.lock.

Four constructors: Pin(P), Law(L), App(A), Nat(N).
Three core opcodes:
  0  Pin   (arity 1)  — content-address a value
  1  Law   (arity 3)  — construct a named law
  2  Elim  (arity 6)  — dispatch on constructor (formerly xocore Case_)

BPLAN named primitives are dispatched at the special "B" opcode
(strNat("B") = 66), entered when a saturated application has the shape
((P(N(66))) ("Name" arg1 ... argN)). The named op is the head of the inner
App; arguments follow.

This is the authoritative local dev evaluator. CI uses Reaver
(vendor/reaver, plan-assembler) for end-to-end verification.
"""


class P:
    """Pin: content-addressed, globally deduplicated."""
    __slots__ = ('val',)
    def __init__(self, val): self.val = val
    def __eq__(self, other): return isinstance(other, P) and self.val == other.val
    def __repr__(self): return f'<{self.val}>'


class L:
    """Law: named pure function {name arity body}."""
    __slots__ = ('arity', 'name', 'body')
    def __init__(self, arity, name, body):
        self.arity = arity
        self.name = name
        self.body = body
    def __eq__(self, other):
        return isinstance(other, L) and self.arity == other.arity \
               and self.name == other.name and self.body == other.body
    def __repr__(self): return f'{{{self.name} {self.arity} {self.body}}}'


class A:
    """App: function application."""
    __slots__ = ('fun', 'arg')
    def __init__(self, fun, arg):
        self.fun = fun
        self.arg = arg
    def __eq__(self, other):
        return isinstance(other, A) and self.fun == other.fun and self.arg == other.arg
    def __repr__(self): return f'({self.fun} {self.arg})'


def N(n):
    """Nat: natural number. Represented as Python int."""
    return n


def is_nat(x):
    return isinstance(x, int)


def is_pin(x):
    return isinstance(x, P)


def is_law(x):
    return isinstance(x, L)


def is_app(x):
    return isinstance(x, A)


def nat(x):
    """Extract nat value, defaulting to 0."""
    return x if is_nat(x) else 0


def str_nat(s: str) -> int:
    """Encode s as a little-endian nat (Reaver's strNat)."""
    return int.from_bytes(s.encode('utf-8'), 'little')


def nat_str(n: int) -> str:
    """Decode a little-endian nat to its UTF-8 string. Best-effort; returns
    repr-form for non-decodable nats."""
    n = int(n)
    if n == 0:
        return ''
    raw = n.to_bytes((n.bit_length() + 7) // 8, 'little')
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return f'<nat:{n}>'


# Special opcodes:
#   0/1/2: canonical PLAN core.
#   66 = strNat("B"): BPLAN named-op dispatch (vendor/reaver/Plan.hs op 66).
#   82 = strNat("R"): RPLAN dispatch (op 82). Not implemented in the harness;
#                     RPLAN ops are stubbed (see _rplan_stub below).
_BPLAN_OPCODE = str_nat('B')   # 66
_RPLAN_OPCODE = str_nat('R')   # 82

# Arity of each primitive opcode when accessed via Pin.
_OP_ARITY = {
    0: 1,                # Pin
    1: 3,                # Law
    2: 6,                # Elim
    _BPLAN_OPCODE: 1,    # B applied to ("Name" args...) — saturated with 1 arg
    _RPLAN_OPCODE: 1,    # R applied to ("Name" args...)
}


def arity(x):
    """Compute remaining arity of a PLAN value."""
    if is_app(x):
        a = arity(x.fun)
        return 0 if a == 0 else a - 1
    if is_pin(x):
        inner = x.val
        if is_law(inner):
            return inner.arity
        if is_nat(inner) and inner in _OP_ARITY:
            return _OP_ARITY[inner]
        return 1
    if is_law(x):
        return x.arity
    if is_nat(x):
        return 0
    return 0


def match(p, l, a, z, m, o):
    """Elim (canonical opcode 2): dispatch on constructor type.

    The scrutinee ``o`` is forced to WHNF before dispatch so that a
    saturated-but-unevaluated App (e.g. the result of a top-level function
    application used directly as a match scrutinee) is reduced to its actual
    constructor form before the branch is selected.
    """
    o = evaluate(o)
    if is_pin(o):
        return apply(p, o.val)
    if is_law(o):
        return apply(apply(apply(l, o.arity), o.name), o.body)
    if is_app(o):
        return apply(apply(a, o.fun), o.arg)
    if is_nat(o):
        if o == 0:
            return z
        else:
            return apply(m, o - 1)
    raise ValueError(f"match: unknown value {o}")


def apply(f, x):
    """Apply f to x. If f has arity 1, execute; otherwise build App."""
    if arity(f) != 1:
        return A(f, x)
    return exec_(f, [x])


def exec_(f, e):
    """Execute a saturated application."""
    if is_pin(f):
        inner = f.val
        if is_nat(inner):
            return op(inner, e)
        if is_law(inner):
            return judge(inner.arity, list(reversed([f] + e)), inner.body)
        raise ValueError(f"exec: bad pin content {inner}")
    if is_app(f):
        return exec_(f.fun, [f.arg] + e)
    if is_law(f):
        return judge(f.arity, list(reversed([f] + e)), f.body)
    raise ValueError(f"exec: not executable {f}")


def _unapp(v):
    """Flatten an App into [head, arg1, arg2, ...] (left-associated)."""
    out = []
    while is_app(v):
        out.append(v.arg)
        v = v.fun
    out.append(v)
    out.reverse()
    return out


def op(opcode, args):
    """Execute a primitive opcode with its accumulated arguments (a list)."""
    opcode = nat(opcode)
    if opcode == 0:
        # Pin: P(args[0])
        return P(args[0])
    if opcode == 1:
        # Law: (name, arity, body) -> L(arity, name, body).
        # NB: the canonical Plan-spec rule is S₀(1 a m b) = J(Na+1)mb, where
        # the runtime-internal arity is user_arity+1 (slot 0 = self). Our L
        # stores user_arity directly; arity() and exec_() are consistent
        # with that convention. Codegen never emits opcode-1 Law construction
        # for runtime use; this case is here for completeness.
        n, a, b = args[0], args[1], args[2]
        return L(nat(a), n, b)
    if opcode == 2:
        # Elim: dispatch on constructor type (6 args: p, l, a, z, m, o)
        p, l, a_, z, m, o = args[0], args[1], args[2], args[3], args[4], args[5]
        return match(p, l, a_, z, m, o)
    if opcode == _BPLAN_OPCODE:
        # BPLAN named-op dispatch.
        # The single arg is the App ("Name" arg1 ... argN). Unapp to extract
        # the name and the args, then dispatch.
        return _bplan_op(_unapp(args[0]))
    if opcode == _RPLAN_OPCODE:
        # RPLAN named-op dispatch — stubbed; harness has no I/O.
        return _rplan_stub(_unapp(args[0]))
    raise ValueError(f"op: unknown opcode {opcode!r}")


def _bplan_op(parts):
    """Dispatch a BPLAN named operation. ``parts`` is [name_nat, arg1, ...]
    where name_nat is the strNat of the operation name."""
    if not parts:
        raise ValueError('bplan: empty parts')
    name_nat = parts[0]
    args = parts[1:]
    name = nat_str(nat_str_extract(name_nat))
    impl = _BPLAN_IMPLS.get(name)
    if impl is None:
        raise ValueError(f"bplan: unknown op {name!r} (nat {nat_str_extract(name_nat)})")
    return impl(args)


def nat_str_extract(name_nat):
    """name_nat may be a nat directly OR a quoted nat (A(N(0), nat)). Extract
    the underlying nat value either way."""
    # Plan Assembler parses "Name" as (1 strNat-of-Name); kal evaluates the
    # outer (1 ...) to the nat itself, but inside body context our codegen
    # may emit (0 nat). Both shapes reach here with `name_nat` already
    # forced — handle either.
    nv = evaluate(name_nat)
    if is_nat(nv):
        return nv
    # Quoted form A(N(0), nat) — re-fall (kal would have unwrapped this)
    if is_app(nv) and is_nat(nv.fun) and nv.fun == 0:
        return nat(evaluate(nv.arg))
    return nat(nv)


# ---------------------------------------------------------------------------
# BPLAN implementations
#
# Mirrors vendor/reaver/src/hs/Plan.hs `op 66 [...]` cases. Only the subset
# Gallowglass codegen depends on (per bootstrap/bplan_deps.py) is implemented;
# unused cases raise on dispatch.
# ---------------------------------------------------------------------------

def _b_pin(args):
    return P(evaluate(args[0]))

def _b_law(args):
    a, m, b = args
    # Per Plan.hs line 334: L (nat a + 1) m b. We follow the same convention
    # for compatibility with code that constructs Laws via the Law BPLAN op.
    return L(nat(evaluate(a)) + 1, evaluate(m), evaluate(b))

def _b_elim(args):
    p, l, a_, z, m, o = args
    return match(p, l, a_, z, m, o)

def _b_inc(args):
    return nat(evaluate(args[0])) + 1

def _b_dec(args):
    n = nat(evaluate(args[0]))
    return n - 1 if n > 0 else 0

def _b_force(args):
    return evaluate(args[0])

def _b_add(args):
    x = nat(evaluate(args[0]))
    y = nat(evaluate(args[1]))
    return x + y

def _b_sub(args):
    x = nat(evaluate(args[0]))
    y = nat(evaluate(args[1]))
    return 0 if y >= x else x - y

def _b_mul(args):
    return nat(evaluate(args[0])) * nat(evaluate(args[1]))

def _b_div(args):
    x = nat(evaluate(args[0]))
    y = nat(evaluate(args[1]))
    return 0 if y == 0 else x // y

def _b_mod(args):
    x = nat(evaluate(args[0]))
    y = nat(evaluate(args[1]))
    return 0 if y == 0 else x % y

def _b_eq(args):
    return 1 if nat(evaluate(args[0])) == nat(evaluate(args[1])) else 0

def _b_cmp(args):
    a = nat(evaluate(args[0]))
    b = nat(evaluate(args[1]))
    if a < b: return 0
    if a == b: return 1
    return 2

def _b_lsh(args):
    return nat(evaluate(args[0])) << nat(evaluate(args[1]))

def _b_rsh(args):
    return nat(evaluate(args[0])) >> nat(evaluate(args[1]))

def _b_type(args):
    v = evaluate(args[0])
    if is_pin(v): return 1
    if is_law(v): return 2
    if is_app(v): return 3
    if is_nat(v): return 0
    raise ValueError(f'Type: unknown {v!r}')

def _b_is_pin(args): return 1 if is_pin(evaluate(args[0])) else 0
def _b_is_law(args): return 1 if is_law(evaluate(args[0])) else 0
def _b_is_app(args): return 1 if is_app(evaluate(args[0])) else 0
def _b_is_nat(args): return 1 if is_nat(evaluate(args[0])) else 0

def _b_hd(args):
    v = evaluate(args[0])
    return v.fun if is_app(v) else v

def _b_sz(args):
    """Size of an App spine (Reaver's planSz). For non-App, 0; for App,
    length of the App's flattened arg list (head excluded)."""
    v = evaluate(args[0])
    n = 0
    while is_app(v):
        n += 1
        v = v.fun
    return n

def _b_unpin(args):
    v = evaluate(args[0])
    return v.val if is_pin(v) else 0

def _b_seq(args):
    # x `seq` y — force x, return y
    evaluate(args[0])
    return args[1]

def _b_trace(args):
    # No-op in the harness (stdout is reserved for test output).
    return args[1]


_BPLAN_IMPLS = {
    'Pin':   _b_pin,
    'Law':   _b_law,
    'Elim':  _b_elim,
    'Inc':   _b_inc,
    'Dec':   _b_dec,
    'Force': _b_force,
    'Add':   _b_add,
    'Sub':   _b_sub,
    'Mul':   _b_mul,
    'Div':   _b_div,
    'Mod':   _b_mod,
    'Eq':    _b_eq,
    'Cmp':   _b_cmp,
    'Lsh':   _b_lsh,
    'Rsh':   _b_rsh,
    'Type':  _b_type,
    'IsPin': _b_is_pin,
    'IsLaw': _b_is_law,
    'IsApp': _b_is_app,
    'IsNat': _b_is_nat,
    'Hd':    _b_hd,
    'Sz':    _b_sz,
    'Unpin': _b_unpin,
    'Seq':   _b_seq,
    'Trace': _b_trace,
}


def _rplan_stub(parts):
    """RPLAN ops are I/O — the harness has no I/O. Return Nat 0 (no-op)."""
    return 0


def kal(n, e, body):
    """Evaluate a law body with environment e and n bindings."""
    if is_nat(body):
        b = body
        if b <= n:
            return e[n - b]
        return body
    if is_app(body):
        if is_app(body.fun) and is_nat(body.fun.fun) and body.fun.fun == 0:
            # (0 f x) = apply f to x within the law body
            return apply(kal(n, e, body.fun.arg), kal(n, e, body.arg))
        if is_nat(body.fun) and body.fun == 0:
            # (0 x) = quote x
            return body.arg
        return body
    return body


def judge(args, ie, body):
    """Evaluate a law: process let-bindings, then evaluate the body."""
    n = args
    e = list(ie)

    while is_app(body) and is_app(body.fun) and is_nat(body.fun.fun) and body.fun.fun == 1:
        v = body.fun.arg
        k = body.arg
        v_val = kal(n, e, v)
        n += 1
        e.insert(0, v_val)
        body = k

    return kal(n, e, body)


# --- Convenience constructors ---

def law(name, arity, body):
    """Create a PLAN law."""
    return L(arity, name, body)


def pin(val):
    """Create a PLAN pin."""
    return P(val)


def app(f, *args):
    """Left-associative application: app(f, a, b) = A(A(f, a), b)."""
    result = f
    for a in args:
        result = A(result, a)
    return result


def mk_law(name, arity, body):
    """Construct a Law via the canonical opcode-1 dispatch."""
    return op(1, [name, arity, body])


# The pin that gates BPLAN named-op dispatch.  Used by codegen and test
# fixtures that need to construct a BPLAN call shape directly.
B_PIN = P(N(_BPLAN_OPCODE))


def make_bplan_law(prim_name: str, prim_arity: int, law_name=0):
    """Build an unpinned Law that calls BPLAN named primitive `prim_name`.

    Mirrors `vendor/reaver/src/plan/boot.plan`'s `bplan` macro expansion:

        L(prim_arity, law_name, ((P("B")) ("Inc" arg1 ... argN)))

    This is the canonical replacement for the legacy xocore `L(arity, name,
    bapp(opcode_pin, slot_1, ..., slot_N))` shape used in test fixtures.
    Useful in tests that need a direct Law-builder; production codegen uses
    `bootstrap.codegen.Compiler._make_bplan_prim` which returns the pinned
    form."""
    name_nat = str_nat(prim_name)
    quoted = A(N(0), N(name_nat))                      # quoted constant
    inner = quoted
    for k in range(1, prim_arity + 1):
        inner = A(A(N(0), inner), N(k))                # bapp slot k
    body = A(A(N(0), B_PIN), inner)                    # bapp(B_PIN, inner)
    return L(prim_arity, law_name, body)


# --- Evaluation entry point ---

EVALUATE_DEPTH_LIMIT = 10000


def evaluate(val, _depth=0):
    """Force a PLAN value to normal form (recursive).

    When an App is structurally stuck (arity 0), we evaluate sub-expressions
    and retry — this handles cases like A(A(P(0),1), body) where evaluating
    the function sub-part (A(P(0),1) → P(1)) unlocks further reduction.

    Raises RecursionError if PLAN-level recursion exceeds
    EVALUATE_DEPTH_LIMIT.  Previously this returned the partially-evaluated
    `val` silently, which produced wrong results downstream rather than a
    detectable failure (AUDIT.md B1).
    """
    if _depth > EVALUATE_DEPTH_LIMIT:
        raise RecursionError(
            f'PLAN evaluator depth exceeded (limit={EVALUATE_DEPTH_LIMIT})'
        )
    if is_nat(val):
        return val
    if is_pin(val):
        return P(evaluate(val.val, _depth + 1))
    if is_law(val):
        return L(val.arity,
                 evaluate(val.name, _depth + 1),
                 evaluate(val.body, _depth + 1))
    if is_app(val):
        result = apply(val.fun, val.arg)
        if result == val:
            new_fun = evaluate(val.fun, _depth + 1)
            new_arg = evaluate(val.arg, _depth + 1)
            if new_fun == val.fun and new_arg == val.arg:
                return val
            return evaluate(A(new_fun, new_arg), _depth + 1)
        return evaluate(result, _depth + 1)
    return val
