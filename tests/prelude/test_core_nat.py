#!/usr/bin/env python3
"""
Core.Nat harness correctness tests.

Verifies that every definition in prelude/src/Core/Nat.gls compiles and
evaluates correctly under the Python PLAN harness.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

MODULE = 'Core.Nat'
SRC_PATH = os.path.join(os.path.dirname(__file__), '..', '..',
                         'prelude', 'src', 'Core', 'Nat.gls')


# ---------------------------------------------------------------------------
# Harness evaluation tests
# ---------------------------------------------------------------------------

from dev.harness.plan import A, N, evaluate as raw_evaluate

def _compile_nat():
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    with open(SRC_PATH) as f:
        src = f.read()
    prog = parse(lex(src, SRC_PATH), SRC_PATH)
    resolved, _ = resolve(prog, MODULE, {}, SRC_PATH)
    return compile_program(resolved, MODULE)

_NAT_COMPILED = None

def _get_nat():
    global _NAT_COMPILED
    if _NAT_COMPILED is None:
        _NAT_COMPILED = _compile_nat()
    return _NAT_COMPILED


class TestCoreNatHarness(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.c = _get_nat()

    def fn(self, name):
        return self.c[f'{MODULE}.{name}']

    # --- neq ---

    def test_neq_equal(self):
        result = raw_evaluate(A(A(self.fn('inst_Eq_Nat_neq'), N(3)), N(3)))
        self.assertEqual(result, 0)  # False

    def test_neq_unequal(self):
        result = raw_evaluate(A(A(self.fn('inst_Eq_Nat_neq'), N(3)), N(4)))
        self.assertEqual(result, 1)  # True

    # --- gt ---

    def test_gt_true(self):
        result = raw_evaluate(A(A(self.fn('inst_Ord_Nat_gt'), N(5)), N(3)))
        self.assertEqual(result, 1)

    def test_gt_false(self):
        result = raw_evaluate(A(A(self.fn('inst_Ord_Nat_gt'), N(3)), N(5)))
        self.assertEqual(result, 0)

    def test_gt_equal(self):
        result = raw_evaluate(A(A(self.fn('inst_Ord_Nat_gt'), N(3)), N(3)))
        self.assertEqual(result, 0)

    # --- gte ---

    def test_gte_greater(self):
        result = raw_evaluate(A(A(self.fn('inst_Ord_Nat_gte'), N(5)), N(3)))
        self.assertEqual(result, 1)

    def test_gte_less(self):
        result = raw_evaluate(A(A(self.fn('inst_Ord_Nat_gte'), N(3)), N(5)))
        self.assertEqual(result, 0)

    def test_gte_equal(self):
        result = raw_evaluate(A(A(self.fn('inst_Ord_Nat_gte'), N(3)), N(3)))
        self.assertEqual(result, 1)

    # --- min/max ---

    def test_min(self):
        result = raw_evaluate(A(A(self.fn('inst_Ord_Nat_min'), N(5)), N(3)))
        self.assertEqual(result, 3)

    def test_max(self):
        result = raw_evaluate(A(A(self.fn('inst_Ord_Nat_max'), N(5)), N(3)))
        self.assertEqual(result, 5)

    # --- sub ---

    def test_sub_basic(self):
        result = raw_evaluate(A(A(self.fn('sub'), N(10)), N(3)))
        self.assertEqual(result, 7)

    def test_sub_saturating(self):
        result = raw_evaluate(A(A(self.fn('sub'), N(2)), N(5)))
        self.assertEqual(result, 0)


if __name__ == '__main__':
    unittest.main()
