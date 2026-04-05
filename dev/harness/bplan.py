"""
BPLAN harness: PLAN evaluator extended with native jets.

Mirrors BPLAN (Bootstrap PLAN) where registered Laws dispatch to native
implementations instead of being interpreted.  Used by the compiler test
suite to eliminate the O(n) arithmetic recursion that otherwise exceeds
Python's recursion limit for multi-byte outputs.

Architecture
------------
Jets are registered by Python object identity (id(L_object)).  The bootstrap
codegen embeds global function references as P(law_value) in Law bodies at
compile time, sharing the same Python L object everywhere a function is used.
Identifying by id() is therefore exact and O(1) — no content hash needed.

The BPLAN evaluator (bevaluate) is a complete re-implementation of plan.py's
evaluate/apply/exec_/kal/judge that:
  1. Checks _JET_REGISTRY in _bexec when a P(Law) is saturated — dispatches
     native if registered, otherwise interprets normally.
  2. Uses _bapply / _bkal / _bjudge / _bop / _bmatch throughout, so jets also
     fire correctly inside Case_ handler arms (which plan.py's apply would miss).

Jets never affect correctness — they return the same value as the PLAN law,
just faster.  M8.8 self-hosting validation is the definitive correctness gate.
"""

from dev.harness.plan import A, L, P, N, is_nat, is_pin, is_law, is_app
from dev.harness.plan import arity as _plan_arity


# ---------------------------------------------------------------------------
# J: jet sentinel
# ---------------------------------------------------------------------------

class J:
    """Native jet.  Replaces a PLAN Law with a Python callable."""
    __slots__ = ('arity', 'name', 'fn')

    def __init__(self, arity: int, name: str, fn):
        self.arity = arity
        self.name = name
        self.fn = fn  # Python fn(*evaluated_args) → PLAN value

    def __repr__(self):
        return f'<jet:{self.name}>'


def _is_jet(x):
    return isinstance(x, J)


def _unwrap(x):
    """Unwrap P(k) quoted-nat pins to bare int k.

    Inside law bodies, nat literal k with k <= arity is encoded as P(N(k))
    (a quoted pin) by body_nat().  When such a literal is passed as an
    argument to a jet, _bexec evaluates it to P(k).  Arithmetic jets expect
    plain ints, so we strip the outer P here.
    """
    if isinstance(x, P) and is_nat(x.val):
        return x.val
    return x


# ---------------------------------------------------------------------------
# Jet registry: id(L_object) → J
# Populated once by register_jets(); never modified after that.
# ---------------------------------------------------------------------------

_JET_REGISTRY: dict = {}


def register_jets(compiled_dict: dict) -> None:
    """
    Populate _JET_REGISTRY from compiled_dict + the built-in jet table.

    compiled_dict maps FQ names (e.g. 'Compiler.add') to PLAN values.
    Jets are registered by Python identity of the Law object so that
    _bexec can recognise P(law) pins in compiled law bodies.
    """
    global _JET_REGISTRY
    _JET_REGISTRY = {}

    for fq_name, (arity, fn) in _COMPILER_JETS.items():
        val = compiled_dict.get(fq_name)
        if val is None:
            continue
        jet = J(arity, fq_name, fn)
        # compiled value is either L(…) directly or P(L(…))
        if isinstance(val, L):
            _JET_REGISTRY[id(val)] = jet
        elif isinstance(val, P) and isinstance(val.val, L):
            _JET_REGISTRY[id(val.val)] = jet


# ---------------------------------------------------------------------------
# BPLAN evaluator
# ---------------------------------------------------------------------------

def _barity(x) -> int:
    if _is_jet(x):
        return x.arity
    if is_app(x):
        a = _barity(x.fun)
        return 0 if a == 0 else a - 1
    return _plan_arity(x)


def _bapply(f, x):
    if _barity(f) != 1:
        return A(f, x)
    return _bexec(f, [x])


def _bexec(f, e):
    if _is_jet(f):
        evaluated = [_unwrap(bevaluate(a)) for a in e]
        return f.fn(*evaluated)
    if is_app(f):
        return _bexec(f.fun, [f.arg] + e)
    if is_pin(f):
        inner = f.val
        if _is_jet(inner):
            evaluated = [_unwrap(bevaluate(a)) for a in e]
            return inner.fn(*evaluated)
        if isinstance(inner, L):
            jet = _JET_REGISTRY.get(id(inner))
            if jet is not None:
                evaluated = [_unwrap(bevaluate(a)) for a in e]
                return jet.fn(*evaluated)
            return _bjudge(inner.arity, list(reversed([f] + e)), inner.body)
        if is_nat(inner):
            return _bop(inner, e)
        raise ValueError(f'_bexec: bad pin content {inner!r}')
    if is_law(f):
        return _bjudge(f.arity, list(reversed([f] + e)), f.body)
    raise ValueError(f'_bexec: not executable {f!r}')


def _bkal(n, e, body):
    if is_nat(body):
        b = body
        if b <= n:
            return e[n - b]
        return body
    if is_app(body):
        if is_app(body.fun) and is_nat(body.fun.fun) and body.fun.fun == 0:
            return _bapply(_bkal(n, e, body.fun.arg), _bkal(n, e, body.arg))
        if is_nat(body.fun) and body.fun == 0:
            return body.arg
        return body
    return body


def _bjudge(args, ie, body):
    n = args
    e = list(ie)
    while (is_app(body) and is_app(body.fun)
           and is_nat(body.fun.fun) and body.fun.fun == 1):
        v = body.fun.arg
        k = body.arg
        v_val = _bkal(n, e, v)
        n += 1
        e.insert(0, v_val)
        body = k
    return _bkal(n, e, body)


def _bmatch(p, l, a, z, m, o):
    if is_pin(o):
        return _bapply(p, o.val)
    if is_law(o):
        return _bapply(_bapply(_bapply(l, o.arity), o.name), o.body)
    if is_app(o):
        return _bapply(_bapply(a, o.fun), o.arg)
    if is_nat(o):
        if o == 0:
            return z
        return _bapply(m, o - 1)
    raise ValueError(f'_bmatch: unknown value {o!r}')


def _bop(opcode, e):
    opcode = opcode if is_nat(opcode) else 0
    if opcode == 0:
        return P(e[0])
    if opcode == 1:
        n, a, b = e[0], e[1], e[2]
        return L(a if is_nat(a) else 0, n, b)
    if opcode == 2:
        return (e[0] if is_nat(e[0]) else 0) + 1
    if opcode == 3:
        return _bmatch(e[0], e[1], e[2], e[3], e[4], e[5])
    if opcode == 4:
        return P(e[0])
    raise ValueError(f'_bop: unknown opcode {opcode}')


def bevaluate(val, _depth: int = 0):
    """Force a PLAN value to normal form, dispatching jets where registered."""
    if _depth > 100000:
        return val
    if is_nat(val):
        return val
    if _is_jet(val):
        return val
    if is_pin(val):
        return P(bevaluate(val.val, _depth + 1))
    if is_law(val):
        return L(val.arity,
                 bevaluate(val.name, _depth + 1),
                 bevaluate(val.body, _depth + 1))
    if is_app(val):
        result = _bapply(val.fun, val.arg)
        if result == val:
            new_fun = bevaluate(val.fun, _depth + 1)
            new_arg = bevaluate(val.arg, _depth + 1)
            if new_fun == val.fun and new_arg == val.arg:
                return val
            return bevaluate(A(new_fun, new_arg), _depth + 1)
        return bevaluate(result, _depth + 1)
    return val


# ---------------------------------------------------------------------------
# Jet table for Compiler.gls arithmetic
# ---------------------------------------------------------------------------

def _sat_sub(m, n):
    """Saturating subtraction: max(0, m - n)."""
    return max(0, m - n)


# ---------------------------------------------------------------------------
# Bytes helpers: Pair Nat Nat = A(A(N(0), len), content)
# ---------------------------------------------------------------------------

def _pair_len(v):
    """Extract len field from MkPair len content = A(A(0, len), content)."""
    if isinstance(v, A) and isinstance(v.fun, A) and is_nat(v.fun.fun) and v.fun.fun == 0:
        ln = v.fun.arg
        return ln if is_nat(ln) else 0
    return 0


def _pair_content(v):
    """Extract content field from MkPair len content."""
    if isinstance(v, A) and isinstance(v.fun, A) and is_nat(v.fun.fun) and v.fun.fun == 0:
        c = v.arg
        return c if is_nat(c) else 0
    return 0


def _bytes_length(v):
    return _pair_len(v)


def _bytes_content(v):
    return _pair_content(v)


def _bytes_concat(a, b):
    a_len = _pair_len(a)
    a_content = _pair_content(a)
    b_content = _pair_content(b)
    new_len = a_len + _pair_len(b)
    new_content = a_content | (b_content << (a_len * 8))
    return A(A(0, new_len), new_content)


_COMPILER_JETS = {
    # Core arithmetic (O(n) recursive in PLAN)
    'Compiler.add':        (2, lambda m, n: m + n),
    'Compiler.sub':        (2, _sat_sub),
    'Compiler.mul':        (2, lambda m, n: m * n),
    'Compiler.div_nat':    (2, lambda a, b: a // b if b else 0),
    'Compiler.mod_nat':    (2, lambda a, b: a % b if b else 0),

    # Bitwise (O(n) bit decomposition in PLAN)
    'Compiler.pow2':       (1, lambda n: 1 << n),
    'Compiler.bit_or':     (2, lambda a, b: a | b),
    'Compiler.bit_and':    (2, lambda a, b: a & b),
    'Compiler.shift_left': (2, lambda n, k: n << k),
    'Compiler.shift_right':(2, lambda n, k: n >> k),

    # Comparisons (O(n) simultaneous descent in PLAN)
    'Compiler.nat_eq':     (2, lambda m, n: 1 if m == n else 0),
    'Compiler.nat_lt':     (2, lambda m, n: 1 if m < n else 0),
    'Compiler.lte':        (2, lambda m, n: 1 if m <= n else 0),
    'Compiler.gte':        (2, lambda m, n: 1 if m >= n else 0),
    'Compiler.max_nat':    (2, lambda a, b: max(a, b)),
    'Compiler.min_nat':    (2, lambda a, b: min(a, b)),

    # nat_byte_len: number of bytes needed to represent nat n
    # PLAN recursive impl hits Python recursion limit for large nats.
    'Compiler.nat_byte_len':  (1, lambda n: (n.bit_length() + 7) // 8 if n > 0 else 0),

    # Bytes ops (Pair Nat Nat = A(A(N(0), len), content))
    # Without jets, the PLAN Case_ dispatch inside bytes_concat/bytes_length
    # mis-fires on encoded PlanVal App nodes (e.g. PNat n = A(N(0), n)),
    # causing bytes_length to return garbage and add to TypeError.
    'Compiler.bytes_length':  (1, _bytes_length),
    'Compiler.bytes_content': (1, _bytes_content),
    'Compiler.bytes_concat':  (2, _bytes_concat),
    'Compiler.bytes_singleton': (1, lambda b: A(A(0, 1), b & 255)),
}


# ---------------------------------------------------------------------------
# Text helpers: Text = A(N(byte_length), N(content_nat))
# ---------------------------------------------------------------------------

def _text_len(t):
    """Extract byte_length from a Text value A(N(len), N(content))."""
    if isinstance(t, A) and is_nat(t.fun):
        return t.fun
    return 0


def _text_nat(t):
    """Extract content_nat from a Text value A(N(len), N(content))."""
    if isinstance(t, A) and is_nat(t.arg):
        return t.arg
    return 0


def _mk_text(length, content):
    """Construct a Text value: A(N(length), N(content))."""
    return A(length if is_nat(length) else 0, content if is_nat(content) else 0)


# ---------------------------------------------------------------------------
# Prelude jet table: Core.Nat.* and Core.Text.Prim.* operations.
# These have the same implementations as their Compiler.* counterparts but
# are registered under the Core.* FQ names so prelude tests can use bplan.
# ---------------------------------------------------------------------------

_PRELUDE_JETS = {
    # Core.Nat arithmetic
    'Core.Nat.add':     (2, lambda m, n: m + n),
    'Core.Nat.mul':     (2, lambda m, n: m * n),
    'Core.Nat.pred':    (1, lambda n: max(0, n - 1)),
    'Core.Nat.is_zero': (1, lambda n: 1 if n == 0 else 0),
    'Core.Nat.nat_eq':  (2, lambda m, n: 1 if m == n else 0),
    'Core.Nat.nat_lt':  (2, lambda m, n: 1 if m < n else 0),

    # Core.Text arithmetic helpers (also defined in Core.Text)
    'Core.Text.sub':     (2, _sat_sub),
    'Core.Text.pow2':    (1, lambda n: 1 << n),
    'Core.Text.div_nat': (2, lambda a, b: a // b if b else 0),
    'Core.Text.mod_nat': (2, lambda a, b: a % b if b else 0),

    # Core.Text.Prim externals
    'Core.Text.Prim.mk_text':  (2, _mk_text),
    'Core.Text.Prim.text_len': (1, _text_len),
    'Core.Text.Prim.text_nat': (1, _text_nat),
}


def register_prelude_jets(compiled_dict: dict) -> None:
    """Register jets for Core.Nat.* and Core.Text.* into _JET_REGISTRY.

    Must be called after register_jets (or instead of it when no Compiler.*
    names are present).  Uses the same id-based dispatch as register_jets.
    """
    for fq_name, (arity, fn) in _PRELUDE_JETS.items():
        val = compiled_dict.get(fq_name)
        if val is None:
            continue
        jet = J(arity, fq_name, fn)
        if isinstance(val, L):
            _JET_REGISTRY[id(val)] = jet
        elif isinstance(val, P) and isinstance(val.val, L):
            _JET_REGISTRY[id(val.val)] = jet
