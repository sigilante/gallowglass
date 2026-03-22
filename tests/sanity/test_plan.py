#!/usr/bin/env python3
"""
Sanity tests for the PLAN evaluator.

These tests verify the five opcodes, law evaluation, pattern matching,
and basic PLAN semantics. They run locally (Python harness) and serve
as the baseline correctness check before CI runs the real VM.

Run: python3 -m pytest tests/sanity/test_plan.py -v
  or: python3 tests/sanity/test_plan.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.plan import (
    P, L, A, N, apply, evaluate, law, pin, app,
    is_nat, is_pin, is_law, is_app, arity, match
)


# ============================================================
# Opcode 0: Pin
# ============================================================

def test_opcode_0_pin():
    """Opcode 0 (via P(0)): pins a value."""
    result = apply(P(0), 42)
    assert is_pin(result), f"Expected pin, got {result}"
    assert result.val == 42

def test_opcode_0_pin_law():
    """Pinning a law preserves it."""
    l = L(1, 1, 1)
    result = apply(P(0), l)
    assert is_pin(result)
    assert result.val == l


# ============================================================
# Opcode 2: Increment
# ============================================================

def test_opcode_2_inc_zero():
    """inc 0 = 1."""
    assert apply(P(2), 0) == 1

def test_opcode_2_inc_large():
    """inc 999 = 1000."""
    assert apply(P(2), 999) == 1000

def test_opcode_2_inc_non_nat():
    """inc on non-nat treats it as 0, returns 1."""
    # nat(x) defaults to 0 for non-nats
    assert apply(P(2), A(1, 2)) == 1


# ============================================================
# Opcode 4: Pin (normalize and content-address)
# ============================================================

def test_opcode_4_pin():
    """Opcode 4 pins a value (same as opcode 0 for our purposes)."""
    result = apply(P(4), 42)
    assert is_pin(result)
    assert result.val == 42


# ============================================================
# Law creation and evaluation
# ============================================================

def test_law_identity():
    """Identity law: {name=1, arity=1, body=1} returns its argument."""
    id_fn = L(1, 1, 1)
    assert apply(id_fn, 42) == 42
    assert apply(id_fn, 0) == 0

def test_law_pinned_identity():
    """Pinned identity law works the same."""
    id_fn = P(L(1, 1, 1))
    assert apply(id_fn, 77) == 77

def test_law_const():
    """Const: arity=2, body=1 returns first arg (ignores second)."""
    # body=1 → e[n-1] = e[1] = first arg (in a 2-arity law)
    const = L(2, 10, 1)
    assert apply(apply(const, 42), 99) == 42

def test_law_second_arg():
    """Arity=2, body=2 returns the second argument passed.

    Environment layout for arity-2 law called as f(a1, a2):
      exec_ gets e=[a1, a2], builds ie=reversed([self, a1, a2])=[a2, a1, self]
      n=2, so: body=1 → ie[2-1]=ie[1]=a1, body=2 → ie[2-2]=ie[0]=a2
    """
    snd_fn = L(2, 20, 2)  # body=2 → ie[0] = second argument
    assert apply(apply(snd_fn, 42), 99) == 99
    assert apply(apply(snd_fn, 0), 7) == 7

def test_law_partial_application():
    """Partial application builds an App node."""
    const = L(2, 10, 1)
    partial = apply(const, 42)
    assert is_app(partial), f"Expected App, got {partial}"
    # Applying the second arg completes the function
    assert apply(partial, 99) == 42

def test_law_self_reference():
    """Body=0 returns the law itself (self-reference, index 0)."""
    self_fn = L(1, 30, 0)
    result = apply(self_fn, 123)
    assert result == self_fn

def test_law_let_binding():
    """Laws can have let-bindings: (1 value body) in the law body."""
    # Let-binding: body = A(A(1, value_expr), continuation)
    # This binds value_expr to a new local, then evaluates continuation
    # Let x = 42 in x → body is (1 42 <ref_to_x>)
    # With n=1 (arity 1), after binding: n=2, e grows
    # The let-bound value at index n=2: kal(2, e, 1) → e[2-1] = arg1 (the input)
    # Wait, let me use: bind the literal 42, then return it
    # body = A(A(1, 42), 2)
    # Processing: n starts at 1 (arity)
    # See let-binding (1 val k): n becomes 2, e gets kal(2,e,42)=42 prepended
    # Then body becomes 2, kal(2, e, 2) = e[2-2] = e[0] = 42 (the let-bound val)
    # Hmm, e is built as: initially [self, arg1], then 42 is prepended → [42, self, arg1]
    # Wait, judge reverses ie: for arity=1, ie = reversed([self, arg1]) = [arg1, self]
    # Then let adds: e.insert(0, 42) → [42, arg1, self]
    # n becomes 2
    # kal(2, [42, arg1, self], 2) = e[2-2] = e[0] = 42 ✓
    body = A(A(1, 42), 2)  # let x = 42 in x
    let_fn = L(1, 40, body)
    result = apply(let_fn, 999)  # arg is ignored
    assert result == 42, f"Expected 42, got {result}"


# ============================================================
# Opcode 2: Match / dispatch
# ============================================================

def test_match_nat_zero():
    """match _ _ _ z _ 0 → z."""
    p_fn = L(1, 50, 1)  # identity (unused for nat)
    l_fn = L(1, 50, 1)
    a_fn = L(1, 50, 1)
    z_val = 100
    m_fn = L(1, 50, 1)  # identity

    result = match(p_fn, l_fn, a_fn, z_val, m_fn, 0)
    assert result == z_val

def test_match_nat_nonzero():
    """match _ _ _ _ m n → m (n-1)."""
    p_fn = L(1, 50, 1)
    l_fn = L(1, 50, 1)
    a_fn = L(1, 50, 1)
    z_val = 100
    m_fn = L(1, 60, 1)  # identity: returns its arg

    result = match(p_fn, l_fn, a_fn, z_val, m_fn, 5)
    assert result == 4  # m_fn applied to (5-1) = 4

def test_match_pin():
    """match p _ _ _ _ <v> → p v."""
    p_fn = L(1, 70, 1)  # identity
    l_fn = L(1, 70, 1)
    a_fn = L(1, 70, 1)
    z_val = 0
    m_fn = L(1, 70, 1)

    result = match(p_fn, l_fn, a_fn, z_val, m_fn, P(42))
    assert result == 42  # p applied to inner value 42

def test_match_app():
    """match _ _ a _ _ (f x) → a f x."""
    a_fn = L(2, 80, 1)  # returns first arg (the function part)
    p_fn = L(1, 80, 1)
    l_fn = L(1, 80, 1)
    z_val = 0
    m_fn = L(1, 80, 1)

    val = A(10, 20)
    result = match(p_fn, l_fn, a_fn, z_val, m_fn, val)
    assert result == 10  # a_fn returns first arg = the function part


# ============================================================
# Arity
# ============================================================

def test_arity_nat():
    assert arity(0) == 0
    assert arity(42) == 0

def test_arity_law():
    assert arity(L(1, 1, 1)) == 1
    assert arity(L(3, 1, 1)) == 3

def test_arity_pinned_law():
    assert arity(P(L(2, 1, 1))) == 2

def test_arity_app():
    """Applying one arg to a 2-arity law gives arity 1."""
    f = L(2, 1, 1)
    partial = A(f, 42)
    assert arity(partial) == 1


# ============================================================
# Run as script
# ============================================================

if __name__ == '__main__':
    tests = [(name, obj) for name, obj in globals().items()
             if name.startswith('test_') and callable(obj)]
    passed = failed = 0
    for name, fn in sorted(tests):
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except (AssertionError, Exception) as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
