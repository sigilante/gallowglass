#!/usr/bin/env python3
"""
PLAN dev harness CLI.

Usage:
    python3 -m dev.harness.run eval <plan-expr>
    python3 -m dev.harness.run load <seed-file>
    python3 -m dev.harness.run test
"""

import sys
import os

# Add repo root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.plan import (
    P, L, A, N, apply, evaluate, law, pin, app,
    is_nat, is_pin, is_law, is_app
)
from dev.harness.seed import load_seed, save_seed


def show_plan(val, depth=0):
    """Pretty-print a PLAN value."""
    if depth > 50:
        return "..."
    if is_nat(val):
        return str(val)
    if is_pin(val):
        return f"<{show_plan(val.val, depth+1)}>"
    if is_law(val):
        return f"{{{show_plan(val.name, depth+1)} {val.arity} {show_plan(val.body, depth+1)}}}"
    if is_app(val):
        return f"({show_plan(val.fun, depth+1)} {show_plan(val.arg, depth+1)})"
    return repr(val)


def cmd_load(path):
    """Load and display a seed file."""
    with open(path, 'rb') as f:
        data = f.read()
    val = load_seed(data)
    print(f"Loaded seed: {len(data)} bytes")
    print(f"Value: {show_plan(val)}")
    return val


def cmd_test():
    """Run built-in sanity tests."""
    passed = 0
    failed = 0

    def check(name, got, expected):
        nonlocal passed, failed
        if got == expected:
            print(f"  PASS  {name}")
            passed += 1
        else:
            print(f"  FAIL  {name}: expected {expected}, got {got}")
            failed += 1

    print("=== PLAN Evaluator Tests ===\n")

    # Test 1: Nat identity
    check("nat identity", evaluate(42), 42)

    # Test 2: Increment (opcode 3)
    # Apply P(N(3)) to 41 — opcode 3 increments
    result = apply(P(3), 41)
    check("increment", result, 42)

    # Test 3: Law creation — identity function {1 1 1}
    # A law with name=1, arity=1, body=1 (returns arg 1 = first arg)
    id_law = L(1, 1, 1)
    result = apply(id_law, 99)
    check("identity law", result, 99)

    # Test 4: Pinned law
    id_pinned = P(L(1, 1, 1))
    result = apply(id_pinned, 77)
    check("pinned identity", result, 77)

    # Test 5: Constant function — {2 2 2}
    # name=2, arity=2, body=2 means return arg 2 (first arg in a 2-arity law)
    # args: index 0 = self, index 1 = first_arg, index 2 = second_arg
    # body = 2 means return e[n-2] where n=2, so e[0] = self... wait
    # Actually: body = nat b, kal looks up e[n - b]
    # n = arity = 2, e = [self, arg1, arg2] (reversed in judge)
    # body = 1 → e[2-1] = e[1] = arg1 (first arg)
    # body = 2 → e[2-2] = e[0] = self
    # So to return first arg: body = 1
    const_fn = L(2, N(10), 1)  # name=10, arity=2, body=1 (return first arg)
    result = apply(apply(const_fn, 42), 99)
    check("const function (return first arg)", result, 42)

    # Test 6: Self-reference — a law that returns itself
    self_law = L(1, N(20), 0)  # body=0 means return e[n-0] = e[n] = self
    result = apply(self_law, 123)
    check("self-reference", is_law(result), True)

    # Test 7: Match on nat zero (opcode 2)
    # match p l a z m 0 → z
    # We build this as: ((((((P(2) p) l) a) z) m) 0)
    z_val = 100
    m_val = L(1, N(30), 1)  # identity, would apply to (n-1)
    result = apply(P(2), app(0, 999, 999, 999, z_val, m_val, 0))
    check("match nat zero", result, z_val)

    # Test 8: Match on nat nonzero
    result = apply(P(2), app(0, 999, 999, 999, z_val, m_val, 5))
    check("match nat nonzero", result, 4)  # m applied to (5-1) = 4

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")

    print("\n=== Seed Round-Trip Tests ===\n")

    # Test 9: Seed round-trip for a nat
    seed_bytes = save_seed(42)
    result = load_seed(seed_bytes)
    check("seed round-trip: nat 42", is_nat(result) and result == 42, True)

    # Test 10: Seed round-trip for a larger nat
    seed_bytes = save_seed(1000)
    result = load_seed(seed_bytes)
    check("seed round-trip: nat 1000", result, 1000)

    # Test 11: Seed round-trip for an application
    val = A(1, 2)
    seed_bytes = save_seed(val)
    result = load_seed(seed_bytes)
    check("seed round-trip: app (1 2)",
          is_app(result) and result.fun == 1 and result.arg == 2, True)

    print(f"\n{'='*40}")
    total_passed = passed
    total_failed = failed
    print(f"Total: {total_passed} passed, {total_failed} failed")

    return total_failed == 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'test':
        success = cmd_test()
        sys.exit(0 if success else 1)
    elif cmd == 'load':
        if len(sys.argv) < 3:
            print("Usage: run.py load <seed-file>")
            sys.exit(1)
        cmd_load(sys.argv[2])
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
