#!/usr/bin/env python3
"""
Core.Nat planvm seed validation tests.

Verifies that every definition in prelude/src/Core/Nat.gls compiles and
produces a seed accepted by planvm.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.planvm.test_seed_planvm import requires_planvm, seed_loads

MODULE = 'Core.Nat'
SRC_PATH = os.path.join(os.path.dirname(__file__), '..', '..',
                         'prelude', 'src', 'Core', 'Nat.gls')


def make_seed(name):
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    from bootstrap.emit import emit
    with open(SRC_PATH) as f:
        src = f.read()
    prog = parse(lex(src, SRC_PATH), SRC_PATH)
    resolved, _ = resolve(prog, MODULE, {}, SRC_PATH)
    compiled = compile_program(resolved, MODULE)
    return emit(compiled, f'{MODULE}.{name}')


class TestCoreNatSeeds(unittest.TestCase):

    @requires_planvm
    def test_pred_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('pred')))

    @requires_planvm
    def test_is_zero_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('is_zero')))

    @requires_planvm
    def test_nat_eq_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('nat_eq')))

    @requires_planvm
    def test_nat_lt_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('nat_lt')))

    @requires_planvm
    def test_add_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('add')))

    @requires_planvm
    def test_mul_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('mul')))

    @requires_planvm
    def test_nat_lte_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('nat_lte')))

    @requires_planvm
    def test_inst_eq_nat_eq_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('inst_Eq_Nat_eq')))

    @requires_planvm
    def test_inst_eq_nat_neq_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('inst_Eq_Nat_neq')))

    @requires_planvm
    def test_inst_ord_nat_lt_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('inst_Ord_Nat_lt')))

    @requires_planvm
    def test_inst_ord_nat_lte_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('inst_Ord_Nat_lte')))

    @requires_planvm
    def test_inst_add_nat_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('inst_Add_Nat')))

    @requires_planvm
    def test_sub_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('sub')))

    @requires_planvm
    def test_div_nat_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('div_nat')))

    @requires_planvm
    def test_mod_nat_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('mod_nat')))

    @requires_planvm
    def test_inst_ord_nat_gt_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('inst_Ord_Nat_gt')))

    @requires_planvm
    def test_inst_ord_nat_gte_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('inst_Ord_Nat_gte')))

    @requires_planvm
    def test_inst_ord_nat_min_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('inst_Ord_Nat_min')))

    @requires_planvm
    def test_inst_ord_nat_max_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('inst_Ord_Nat_max')))


# ---------------------------------------------------------------------------
# Layer 1a: harness evaluation tests for M14.1 additions
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
