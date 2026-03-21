"""
PLAN Virtual Machine — Python reference evaluator.

Direct port of xocore-tech/PLAN spec/Plan.hs.
Four constructors: Pin(P), Law(L), App(A), Nat(N).
Five opcodes (0-4).

This is the authoritative local dev evaluator. CI uses the real
xocore-tech/PLAN VM on Linux for final verification.
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


def arity(x):
    """Compute the remaining arity of a PLAN value."""
    if is_app(x):
        a = arity(x.fun)
        return 0 if a == 0 else a - 1
    if is_pin(x):
        if is_law(x.val):
            return x.val.arity
        return 1
    if is_law(x):
        return x.val if is_nat(x.val) else x.arity
    if is_nat(x):
        return 0
    return 0


def arity(x):
    """Compute remaining arity of a PLAN value."""
    if is_app(x):
        a = arity(x.fun)
        return 0 if a == 0 else a - 1
    if is_pin(x):
        inner = x.val
        if is_law(inner):
            return inner.arity
        return 1
    if is_law(x):
        return x.arity
    if is_nat(x):
        return 0
    return 0


def match(p, l, a, z, m, o):
    """Opcode 2: dispatch on constructor type."""
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
            return op(inner, e[0])
        if is_law(inner):
            return judge(inner.arity, list(reversed([f] + e)), inner.body)
        raise ValueError(f"exec: bad pin content {inner}")
    if is_app(f):
        return exec_(f.fun, [f.arg] + e)
    if is_law(f):
        return judge(f.arity, list(reversed([f] + e)), f.body)
    raise ValueError(f"exec: not executable {f}")


def op(opcode, x):
    """Execute a primitive opcode."""
    opcode = nat(opcode)
    if opcode == 0:
        # Opcode 0: pin a value
        return P(x)
    if opcode == 1:
        # Opcode 1: create a law {name arity body}
        # (1 arity name body) but arity is passed as (actual_arity - 1)
        # Actually: op 1 receives (N 0 `A` a `A` m `A` b)
        # which means x = A(A(A(N(0), a), m), b)
        if is_app(x) and is_app(x.fun) and is_app(x.fun.fun):
            b = x.arg
            m = x.fun.arg
            a = x.fun.fun.arg
            return L(nat(a) + 1, m, b)
        raise ValueError(f"op 1: bad args {x}")
    if opcode == 2:
        # Opcode 2: match/dispatch
        # x = A(A(A(A(A(A(N(0), p), l), a), z), m), o)
        if is_app(x) and is_app(x.fun) and is_app(x.fun.fun) \
                and is_app(x.fun.fun.fun) and is_app(x.fun.fun.fun.fun) \
                and is_app(x.fun.fun.fun.fun.fun):
            o = x.arg
            m = x.fun.arg
            z = x.fun.fun.arg
            a_ = x.fun.fun.fun.arg
            l = x.fun.fun.fun.fun.arg
            p = x.fun.fun.fun.fun.fun.arg
            return match(p, l, a_, z, m, o)
        raise ValueError(f"op 2: bad args {x}")
    if opcode == 3:
        # Opcode 3: increment
        return nat(x) + 1
    if opcode == 4:
        # Opcode 4: pin (normalize and content-address)
        return P(x)
    raise ValueError(f"op: unknown opcode {opcode}")


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
    # Build the environment by processing let-bindings
    # Let-binding: (1 value continuation)
    n = args
    e = list(ie)

    while is_app(body) and is_app(body.fun) and is_nat(body.fun.fun) and body.fun.fun == 1:
        v = body.fun.arg
        k = body.arg
        n += 1
        e.insert(0, kal(n, e, v))
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
    """Create a law via opcode 0: evaluates (0 name arity body)."""
    # Opcode 0 with args packaged as nested App
    return op(0, app(0, name, arity, body))


# --- Evaluation entry point ---

def evaluate(val, _depth=0):
    """Force a PLAN value to normal form (recursive).

    When an App is structurally stuck (arity 0), we evaluate sub-expressions
    and retry — this handles cases like A(A(P(0),1), body) where evaluating
    the function sub-part (A(P(0),1) → P(1)) unlocks further reduction.
    """
    if _depth > 10000:
        return val  # guard against runaway reduction
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
            # Structurally stuck: evaluate sub-expressions and retry.
            # This unlocks reductions like A(A(P(0),1), x) → A(P(1), x) → law.
            new_fun = evaluate(val.fun, _depth + 1)
            new_arg = evaluate(val.arg, _depth + 1)
            if new_fun == val.fun and new_arg == val.arg:
                return val  # truly irreducible
            return evaluate(A(new_fun, new_arg), _depth + 1)
        return evaluate(result, _depth + 1)
    return val
