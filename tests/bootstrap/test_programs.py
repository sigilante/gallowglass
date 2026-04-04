#!/usr/bin/env python3
"""
Integration test battery — end-to-end program tests via Python bootstrap.

Each test compiles a complete Gallowglass program, evaluates the result
using the PLAN reference evaluator, and asserts correctness.

Programs exercise: self-recursive let, fix expressions, algebraic types,
nested pattern matching, Nat arithmetic via Core.PLAN.inc.

Run: python3 tests/bootstrap/test_programs.py
  or: python3 -m pytest tests/bootstrap/test_programs.py -v
"""

import sys
import os
sys.setrecursionlimit(50000)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program
from dev.harness.plan import P, L, A, N, is_nat, is_pin, is_law, is_app
from dev.harness.plan import apply, evaluate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pipeline(src: str, module: str = 'Test') -> dict:
    prog = parse(lex(src, '<test>'), '<test>')
    resolved, _ = resolve(prog, module, {}, '<test>')
    return compile_program(resolved, module)


def run(src: str, name: str, *args, module: str = 'Test'):
    """Compile, look up name, apply args in order, evaluate."""
    compiled = pipeline(src, module)
    fq = f'{module}.{name}'
    assert fq in compiled, f"'{fq}' not in compiled: {list(compiled.keys())}"
    v = compiled[fq]
    for a in args:
        v = apply(v, a)
    return evaluate(v)


# ---------------------------------------------------------------------------
# Arithmetic primitives (shared across programs)
# ---------------------------------------------------------------------------

_ARITH_SRC = """\
external mod Core.PLAN {
  inc : Nat → Nat
}

-- add: m + n by recursion on n (predecessor via Nat match)
let add : Nat → Nat → Nat
  = λ m n → match n {
    | 0 → m
    | k → Core.PLAN.inc (add m k)
  }

-- mul: m * n
let mul : Nat → Nat → Nat
  = λ m n → match n {
    | 0 → 0
    | k → add (mul m k) m
  }

-- pred: predecessor (pred 0 = 0)
let pred : Nat → Nat
  = λ n → match n { | 0 → 0 | k → k }
"""


# ---------------------------------------------------------------------------
# Fibonacci
# ---------------------------------------------------------------------------

_FIB_SRC = _ARITH_SRC + """\
-- fib: Fibonacci numbers
-- Pattern: p = n-1, q = n-2 (via nested Nat match predecessors)
let fib : Nat → Nat
  = λ n → match n {
    | 0 → 0
    | p → match p {
        | 0 → 1
        | q → add (fib p) (fib q)
      }
  }
"""


def test_fib_zero():
    assert run(_FIB_SRC, 'fib', N(0)) == 0


def test_fib_one():
    assert run(_FIB_SRC, 'fib', N(1)) == 1


def test_fib_two():
    assert run(_FIB_SRC, 'fib', N(2)) == 1


def test_fib_five():
    assert run(_FIB_SRC, 'fib', N(5)) == 5


def test_fib_ten():
    assert run(_FIB_SRC, 'fib', N(10)) == 55


def test_fib_sequence():
    """First 10 Fibonacci numbers: 0,1,1,2,3,5,8,13,21,34."""
    expected = [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
    for i, v in enumerate(expected):
        assert run(_FIB_SRC, 'fib', N(i)) == v, f"fib({i}) = {v}"


# ---------------------------------------------------------------------------
# Fibonacci via fix
# ---------------------------------------------------------------------------

_FIB_FIX_SRC = _ARITH_SRC + """\
-- fib_fix: Fibonacci using fix (anonymous recursion)
let fib_fix : Nat → Nat
  = fix λ fib n → match n {
    | 0 → 0
    | p → match p {
        | 0 → 1
        | q → add (fib p) (fib q)
      }
  }
"""


def test_fib_fix_zero():
    assert run(_FIB_FIX_SRC, 'fib_fix', N(0)) == 0


def test_fib_fix_five():
    assert run(_FIB_FIX_SRC, 'fib_fix', N(5)) == 5


def test_fib_fix_ten():
    assert run(_FIB_FIX_SRC, 'fib_fix', N(10)) == 55


# ---------------------------------------------------------------------------
# Ackermann
# ---------------------------------------------------------------------------

_ACK_SRC = _ARITH_SRC + """\
-- ack: Ackermann function
-- pm = m-1, pn = n-1 (predecessors via Nat match)
let ack : Nat → Nat → Nat
  = λ mm nn → match mm {
    | 0 → Core.PLAN.inc nn
    | pm → match nn {
        | 0 → ack pm 1
        | pn → ack pm (ack mm pn)
      }
  }
"""


def test_ack_0_0():
    """ack(0,0) = 1"""
    assert run(_ACK_SRC, 'ack', N(0), N(0)) == 1


def test_ack_0_n():
    """ack(0,n) = n+1"""
    for n in range(5):
        assert run(_ACK_SRC, 'ack', N(0), N(n)) == n + 1


def test_ack_1_0():
    """ack(1,0) = 2"""
    assert run(_ACK_SRC, 'ack', N(1), N(0)) == 2


def test_ack_1_n():
    """ack(1,n) = n+2"""
    for n in range(5):
        assert run(_ACK_SRC, 'ack', N(1), N(n)) == n + 2


def test_ack_2_3():
    """ack(2,3) = 9"""
    assert run(_ACK_SRC, 'ack', N(2), N(3)) == 9


def test_ack_3_2():
    """ack(3,2) = 29"""
    assert run(_ACK_SRC, 'ack', N(3), N(2)) == 29


# ---------------------------------------------------------------------------
# Sudan function
# ---------------------------------------------------------------------------
# S(0, m, n) = m + n
# S(k+1, m, 0) = m
# S(k+1, m, n+1) = S(k, S(k+1, m, n), S(k+1, m, n) + n + 1)
#
# Sudan grows extremely fast; keep inputs tiny (k <= 1).

_SUDAN_SRC = _ARITH_SRC + """\
-- sudan: Sudan function (hyperoperator family like Ackermann, grows faster)
let sudan : Nat → Nat → Nat → Nat
  = λ kk mm nn → match kk {
    | 0 → add mm nn
    | pk → match nn {
        | 0 → mm
        | pn →
            let prev = sudan kk mm pn in
            sudan pk prev (add prev (Core.PLAN.inc pn))
      }
  }
"""


def test_sudan_0():
    """S(0, m, n) = m + n"""
    assert run(_SUDAN_SRC, 'sudan', N(0), N(3), N(4)) == 7


def test_sudan_1_0():
    """S(1, m, 0) = m"""
    assert run(_SUDAN_SRC, 'sudan', N(1), N(5), N(0)) == 5


def test_sudan_1_1():
    """S(1, 1, 1) = S(0, 1, 2+0) = 1 + 2 = 3? Let me compute:
    S(1,1,1) = S(0, S(1,1,0), S(1,1,0) + 0 + 1)
             = S(0, 1, 1 + 0 + 1) = S(0, 1, 2) = 1+2 = 3
    """
    assert run(_SUDAN_SRC, 'sudan', N(1), N(1), N(1)) == 3


def test_sudan_1_2():
    """S(1, 1, 2):
    S(1,1,2) = S(0, S(1,1,1), S(1,1,1) + 1 + 1)
             = S(0, 3, 3+2) = S(0,3,5) = 3+5 = 8
    """
    assert run(_SUDAN_SRC, 'sudan', N(1), N(1), N(2)) == 8


# ---------------------------------------------------------------------------
# Algebraic types: binary tree
# ---------------------------------------------------------------------------

_TREE_SRC = """\
external mod Core.PLAN {
  inc : Nat → Nat
}

let add : Nat → Nat → Nat
  = λ m n → match n {
    | 0 → m
    | k → Core.PLAN.inc (add m k)
  }

type Tree =
  | Leaf
  | Node Tree (Tree, Nat)

-- size: count internal nodes
let size : Tree → Nat
  = λ t → match t {
    | Leaf → 0
    | Node left pair → match pair {
        | (right, v) → Core.PLAN.inc (add (size left) (size right))
      }
  }

-- depth: max depth
let depth : Tree → Nat
  = λ t → match t {
    | Leaf → 0
    | Node left pair → match pair {
        | (right, v) →
            let dl = depth left in
            let dr = depth right in
            Core.PLAN.inc (match (lte dl dr) {
              | 0 → dl
              | k → dr
            })
      }
  }
"""


# ---------------------------------------------------------------------------
# Mutual recursion: even/odd
# ---------------------------------------------------------------------------

_EVEN_ODD_SRC = """\
external mod Core.PLAN {
  inc : Nat → Nat
}

-- is_even and is_odd are mutually recursive
-- is_even 0 = 1, is_even n = is_odd (n-1)
-- is_odd 0 = 0, is_odd n = is_even (n-1)
let is_even : Nat → Nat
  = λ n → match n {
    | 0 → 1
    | k → is_odd k
  }

let is_odd : Nat → Nat
  = λ n → match n {
    | 0 → 0
    | k → is_even k
  }
"""


def test_even_odd():
    """Even/odd mutual recursion."""
    for n in range(8):
        assert run(_EVEN_ODD_SRC, 'is_even', N(n)) == (1 if n % 2 == 0 else 0)
        assert run(_EVEN_ODD_SRC, 'is_odd', N(n)) == (1 if n % 2 == 1 else 0)


# ---------------------------------------------------------------------------
# Collatz conjecture
# ---------------------------------------------------------------------------

_COLLATZ_SRC = """\
external mod Core.PLAN {
  inc : Nat → Nat
}

let add : Nat → Nat → Nat
  = λ m n → match n {
    | 0 → m
    | k → Core.PLAN.inc (add m k)
  }

-- is_even: 1 if n is even
let is_even : Nat → Nat
  = λ n → match n {
    | 0 → 1
    | k → is_odd k
  }

let is_odd : Nat → Nat
  = λ n → match n {
    | 0 → 0
    | k → is_even k
  }

-- half: n / 2 (floor) via recursion
let half : Nat → Nat
  = λ n → match n {
    | 0 → 0
    | k → match k {
        | 0 → 0
        | j → Core.PLAN.inc (half j)
      }
  }

-- collatz_steps: count steps until reaching 1
-- Even: n/2. Odd: 3n+1 (but in PLAN we can't directly do 3n+1 easily)
-- Simplified: just count until 0 using predecessor steps as proxy
-- Real collatz needs mul3add1; let's use a simpler variant:
-- steps(0) = 0, steps(1) = 0, steps(n even) = 1 + steps(n/2),
-- steps(n odd) = 1 + steps(n - 1)  [simplified — just odd → decrement]
let collatz_steps : Nat → Nat
  = λ n → match n {
    | 0 → 0
    | k → match k {
        | 0 → 0
        | j → Core.PLAN.inc (collatz_steps (match (is_even (Core.PLAN.inc j)) {
            | 0 → j
            | k2 → half (Core.PLAN.inc j)
          }))
      }
  }
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
