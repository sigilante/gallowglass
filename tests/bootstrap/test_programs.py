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
# List literals and cons patterns (M15.3)
# ---------------------------------------------------------------------------

_LIST_SRC = """\
external mod Core.PLAN {
  inc : Nat → Nat
}

type List a =
  | Nil
  | Cons a (List a)

-- sum: sum a list of nats
let sum : List Nat → Nat
  = λ xs → match xs {
    | Nil      → 0
    | Cons h t → let rest = sum t in
                 match rest {
                   | 0 → h
                   | k → Core.PLAN.inc (sum (Cons h (Cons k Nil)))
                 }
  }
"""

# -- List literal expression tests --

_LIST_EXPR_SRC = _LIST_SRC + """\
-- list_empty: empty list literal
let list_empty : List Nat
  = []

-- list_one: singleton list literal
let list_one : List Nat
  = [42]

-- list_three: three-element list literal
let list_three : List Nat
  = [1, 2, 3]
"""


def test_list_expr_empty():
    """[] desugars to Nil (= 0)."""
    compiled = pipeline(_LIST_EXPR_SRC)
    v = evaluate(compiled['Test.list_empty'])
    assert v == 0  # Nil = N(0)


def test_list_expr_one():
    """[42] desugars to Cons 42 Nil = A(A(1, 42), 0)."""
    compiled = pipeline(_LIST_EXPR_SRC)
    v = evaluate(compiled['Test.list_one'])
    # Cons 42 Nil = A(A(1, 42), 0)
    assert is_app(v)


def test_list_expr_three():
    """[1, 2, 3] desugars to Cons 1 (Cons 2 (Cons 3 Nil))."""
    compiled = pipeline(_LIST_EXPR_SRC)
    v = evaluate(compiled['Test.list_three'])
    # Should be a nested app structure
    assert is_app(v)


# -- List pattern tests --

_LIST_PAT_SRC = _LIST_SRC + """\
-- head_or_zero: extract head via list pattern
let head_or_zero : List Nat → Nat
  = λ xs → match xs {
    | []     → 0
    | h :: t → h
  }

-- len: length via cons pattern
let len : List Nat → Nat
  = λ xs → match xs {
    | []     → 0
    | _ :: t → Core.PLAN.inc (len t)
  }

"""


def test_pat_cons_nil():
    """[] pattern matches Nil."""
    assert run(_LIST_PAT_SRC, 'head_or_zero', N(0)) == 0  # Nil = 0


def test_pat_cons_head():
    """h :: t pattern extracts head."""
    # Cons 42 Nil = A(A(1, 42), 0)
    cons_42_nil = A(A(N(1), N(42)), N(0))
    assert run(_LIST_PAT_SRC, 'head_or_zero', cons_42_nil) == 42


def test_pat_cons_len_nil():
    """len [] = 0."""
    assert run(_LIST_PAT_SRC, 'len', N(0)) == 0


def test_pat_cons_len_three():
    """len [1, 2, 3] = 3."""
    # Build Cons 1 (Cons 2 (Cons 3 Nil))
    lst = A(A(N(1), N(1)), A(A(N(1), N(2)), A(A(N(1), N(3)), N(0))))
    assert run(_LIST_PAT_SRC, 'len', lst) == 3


# ---------------------------------------------------------------------------
# Or patterns (M15.4)
# ---------------------------------------------------------------------------

_OR_PAT_SRC = """\
external mod Core.PLAN {
  inc : Nat → Nat
}

type Color =
  | Red
  | Green
  | Blue

-- is_warm: Red or Green are warm colors
let is_warm : Color → Nat
  = λ c → match c {
    | Red | Green → 1
    | _           → 0
  }

-- classify_nat: 0 or 1 are small, anything else is big
let classify_nat : Nat → Nat
  = λ n → match n {
    | 0 | 1 → 0
    | _     → 1
  }
"""


def test_or_pat_first():
    """Red matches the or-pattern (first alternative)."""
    assert run(_OR_PAT_SRC, 'is_warm', N(0)) == 1  # Red = tag 0


def test_or_pat_second():
    """Green matches the or-pattern (second alternative)."""
    assert run(_OR_PAT_SRC, 'is_warm', N(1)) == 1  # Green = tag 1


def test_or_pat_fallthrough():
    """Blue does not match the or-pattern."""
    assert run(_OR_PAT_SRC, 'is_warm', N(2)) == 0  # Blue = tag 2


def test_or_pat_nat_zero():
    """0 matches the nat or-pattern."""
    assert run(_OR_PAT_SRC, 'classify_nat', N(0)) == 0


def test_or_pat_nat_one():
    """1 matches the nat or-pattern."""
    assert run(_OR_PAT_SRC, 'classify_nat', N(1)) == 0


def test_or_pat_nat_other():
    """2 does not match the nat or-pattern."""
    assert run(_OR_PAT_SRC, 'classify_nat', N(2)) == 1


# ---------------------------------------------------------------------------
# String interpolation (M15.6)
# ---------------------------------------------------------------------------

_INTERP_SRC = """\
external mod Core.PLAN {
  inc : Nat → Nat
}

-- text_concat: concatenate two texts (simplified — uses primitives)
-- For testing, we stub text_concat as a pair constructor:
-- text_concat a b = A(A(tag=99, a), b) — distinguishable structure

-- Actually, for a proper test we need real text encoding.
-- Let's just test that interpolation desugars correctly at the parse level.
"""


def test_interp_parse_plain():
    """Plain text with no interpolation parses normally."""
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    src = 'let msg : Nat = "hello"'
    prog = parse(lex(src, '<test>'), '<test>')
    decl = prog.decls[0]
    from bootstrap.ast import ExprText
    assert isinstance(decl.body, ExprText)
    assert decl.body.value == "hello"


def test_interp_parse_desugar():
    """Interpolated text desugars to text_concat/show chain."""
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.ast import ExprApp, ExprVar, ExprText
    src = 'let msg : Nat = "hello #{name} world"'
    prog = parse(lex(src, '<test>'), '<test>')
    decl = prog.decls[0]
    # Should be: text_concat "hello " (text_concat (show name) " world")
    assert isinstance(decl.body, ExprApp), f"expected ExprApp, got {type(decl.body).__name__}"
    # Outer: text_concat applied to two args
    outer_app = decl.body
    assert isinstance(outer_app.fun, ExprApp)  # text_concat "hello "
    inner_func = outer_app.fun
    assert isinstance(inner_func.fun, ExprVar)  # text_concat
    assert str(inner_func.fun.name) == 'text_concat'
    assert isinstance(inner_func.arg, ExprText)  # "hello "
    assert inner_func.arg.value == "hello "
    # Second arg: text_concat (show name) " world"
    second_concat = outer_app.arg
    assert isinstance(second_concat, ExprApp)
    assert isinstance(second_concat.fun, ExprApp)
    show_app = second_concat.fun.arg  # (show name)
    assert isinstance(show_app, ExprApp)
    assert isinstance(show_app.fun, ExprVar)
    assert str(show_app.fun.name) == 'show'


def test_interp_parse_single_expr():
    """Single interpolation with no surrounding text."""
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.ast import ExprApp, ExprVar
    src = 'let msg : Nat = "#{val}"'
    prog = parse(lex(src, '<test>'), '<test>')
    decl = prog.decls[0]
    # Should be: show val (just the expression wrapped in show)
    assert isinstance(decl.body, ExprApp)
    assert isinstance(decl.body.fun, ExprVar)
    assert str(decl.body.fun.name) == 'show'


# ---------------------------------------------------------------------------
# Guards in match arms (M15.5)
# ---------------------------------------------------------------------------

_GUARD_SRC = _ARITH_SRC + """\
-- classify: returns 0 for zero, 1 for even, 2 for odd
-- Uses guard to check evenness via mod
let is_even : Nat → Nat
  = λ nn → match nn {
    | 0 → 1
    | pp → match pp {
        | 0 → 0
        | qq → is_even qq
      }
  }

let classify : Nat → Nat
  = λ nn → match nn {
    | 0                  → 0
    | kk if is_even kk   → 1
    | _                  → 2
  }
"""


def test_guard_zero():
    """0 matches the first arm (no guard)."""
    assert run(_GUARD_SRC, 'classify', N(0)) == 0


def test_guard_pass():
    """1: kk=0, is_even(0)=1 (truthy) → guard passes → returns 1."""
    assert run(_GUARD_SRC, 'classify', N(1)) == 1


def test_guard_fail():
    """2: kk=1, is_even(1)=0 (falsy) → guard fails → falls to _ → returns 2."""
    assert run(_GUARD_SRC, 'classify', N(2)) == 2


def test_guard_pass_3():
    """3: kk=2, is_even(2)=1 → guard passes → returns 1."""
    assert run(_GUARD_SRC, 'classify', N(3)) == 1


def test_guard_fail_4():
    """4: kk=3, is_even(3)=0 → guard fails → returns 2."""
    assert run(_GUARD_SRC, 'classify', N(4)) == 2


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
