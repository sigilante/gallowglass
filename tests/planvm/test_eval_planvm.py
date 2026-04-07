#!/usr/bin/env python3
"""
Layer 3: planvm evaluation tests.

Compiles small Gallowglass programs, runs them on planvm, and asserts the
computed result matches expected values.  This goes beyond seed_loads() (Layer 2)
to verify that planvm actually evaluates compiled code correctly.

planvm behavior: forces seed value, casts result to Nat, exits with it as
the process exit code.  Tests use values 0-255 (exit code range).

Run:
    PLANVM=planvm python3 -m pytest tests/planvm/test_eval_planvm.py -v
  or:
    make test-eval-docker
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.planvm.test_seed_planvm import (
    requires_planvm, compile_to_seed, eval_seed,
)


class TestEvalNat(unittest.TestCase):
    """Evaluate bare Nat literals."""

    @requires_planvm
    def test_nat_zero(self):
        seed = compile_to_seed('let main = 0', 'main')
        self.assertEqual(eval_seed(seed), 0)

    @requires_planvm
    def test_nat_42(self):
        seed = compile_to_seed('let main = 42', 'main')
        self.assertEqual(eval_seed(seed), 42)

    @requires_planvm
    def test_nat_255(self):
        seed = compile_to_seed('let main = 255', 'main')
        self.assertEqual(eval_seed(seed), 255)


class TestEvalLambda(unittest.TestCase):
    """Evaluate lambda applications."""

    @requires_planvm
    def test_identity(self):
        src = '''
let id_fn = λ x → x
let main = id_fn 7
'''
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 7)

    @requires_planvm
    def test_const(self):
        src = '''
let const_fn = λ x y → x
let main = const_fn 10 20
'''
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 10)

    @requires_planvm
    def test_nested_app(self):
        src = '''
let add1 = λ x → Core.PLAN.inc x

external mod Core.PLAN {
  inc : Nat → Nat
}

let main = add1 (add1 5)
'''
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 7)


class TestEvalMatch(unittest.TestCase):
    """Evaluate pattern matching."""

    @requires_planvm
    def test_nat_match_zero(self):
        src = '''
let classify = λ n → match n {
  | 0 → 10
  | _ → 20
}
let main = classify 0
'''
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 10)

    @requires_planvm
    def test_nat_match_nonzero(self):
        src = '''
let classify = λ n → match n {
  | 0 → 10
  | _ → 20
}
let main = classify 5
'''
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 20)

    @requires_planvm
    def test_constructor_match(self):
        src = '''
type Color =
  | Red
  | Green
  | Blue

let to_num = λ c → match c {
  | Red → 1
  | Green → 2
  | Blue → 3
}
let main = to_num Green
'''
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 2)


class TestEvalIfThenElse(unittest.TestCase):
    """Evaluate if/then/else."""

    @requires_planvm
    def test_if_true(self):
        src = 'let main = if 1 then 10 else 20'
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 10)

    @requires_planvm
    def test_if_false(self):
        src = 'let main = if 0 then 10 else 20'
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 20)


class TestEvalArithmetic(unittest.TestCase):
    """Evaluate arithmetic via Core.PLAN.inc."""

    @requires_planvm
    def test_inc(self):
        src = '''
external mod Core.PLAN {
  inc : Nat → Nat
}
let main = Core.PLAN.inc 41
'''
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 42)


class TestEvalRecursion(unittest.TestCase):
    """Evaluate recursive functions."""

    @requires_planvm
    def test_fix_factorial_base(self):
        """fix-based recursion: fac(0) = 1."""
        src = '''
external mod Core.PLAN {
  inc : Nat → Nat
}

let fac : Nat → Nat
  = fix λ self n →
      match n {
        | 0 → 1
        | _ → 0
      }

let main = fac 0
'''
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 1)

    @requires_planvm
    def test_fix_countdown(self):
        """fix-based recursion: count down to 0, return 0."""
        src = '''
external mod Core.PLAN {
  inc : Nat → Nat
}

let countdown : Nat → Nat
  = fix λ self n →
      match n {
        | 0 → 42
        | k → self k
      }

let main = countdown 5
'''
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 42)


class TestEvalEffects(unittest.TestCase):
    """Evaluate effect handler programs."""

    @requires_planvm
    def test_pure_run(self):
        """run (pure 42) = 42."""
        src = '''
external mod Core.PLAN {
  inc : Nat → Nat
}

eff Ask {
  ask : Nat → Nat
}

let main : Nat
  = run (pure 42)
'''
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 42)

    @requires_planvm
    def test_handle_return(self):
        """handle (pure 10) { return x → x | ask a k → 0 } = 10."""
        src = '''
external mod Core.PLAN {
  inc : Nat → Nat
}

eff Ask {
  ask : Nat → Nat
}

let main : Nat
  = run (handle (pure 10) {
      | return x → x
      | ask aa kk → kk 0
    })
'''
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 10)

    @requires_planvm
    def test_handle_effect_op(self):
        """handle (ask 5) { return x → x | ask a k → 99 } = 99."""
        src = '''
external mod Core.PLAN {
  inc : Nat → Nat
}

eff Ask {
  ask : Nat → Nat
}

let main : Nat
  = run (handle (ask 5) {
      | return x → x
      | ask aa kk → 99
    })
'''
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 99)

    @requires_planvm
    def test_handle_resume(self):
        """handle (ask 5) { return x → x | ask a k → k (inc a) } = 6."""
        src = '''
external mod Core.PLAN {
  inc : Nat → Nat
}

eff Ask {
  ask : Nat → Nat
}

let main : Nat
  = run (handle (ask 5) {
      | return x → x
      | ask aa kk → kk (Core.PLAN.inc aa)
    })
'''
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 6)

    @requires_planvm
    def test_do_bind(self):
        """x ← pure 10 in pure x = 10."""
        src = '''
external mod Core.PLAN {
  inc : Nat → Nat
}

eff Ask {
  ask : Nat → Nat
}

let main : Nat
  = run (handle (xx ← pure 10 in pure xx) {
      | return x → x
      | ask aa kk → kk 0
    })
'''
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 10)


class TestEvalConstructors(unittest.TestCase):
    """Evaluate constructor field extraction."""

    @requires_planvm
    def test_option_some_extract(self):
        """Extract value from Some constructor."""
        src = '''
type Option =
  | None
  | Some Nat

let from_some = λ o → match o {
  | None → 0
  | Some x → x
}
let main = from_some (Some 77)
'''
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 77)

    @requires_planvm
    def test_nested_constructor(self):
        """Nested type matching."""
        src = '''
external mod Core.PLAN {
  inc : Nat → Nat
}

type Result =
  | Ok Nat
  | Err Nat

let unwrap = λ r → match r {
  | Ok x → x
  | Err e → 0
}
let main = unwrap (Ok 33)
'''
        self.assertEqual(eval_seed(compile_to_seed(src, 'main')), 33)


if __name__ == '__main__':
    unittest.main()
