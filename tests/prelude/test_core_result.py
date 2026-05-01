#!/usr/bin/env python3
"""
Core.Result tests — harness correctness + planvm seed loading.

Layer 1a: Python harness tests (always run).
  Verifies is_ok, is_err, with_ok, with_err, map_ok, map_err, bind_result.

Layer 2: planvm seed loading (skipped unless planvm is available).
  Verifies every definition in Core.Result produces a planvm-valid seed.

Encoding: Ok a = A(tag=0, a), Err b = A(tag=1, b).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.plan import A, N, L, P, make_bplan_law
from dev.harness.plan import evaluate as plan_evaluate, apply as plan_apply
from dev.harness.bplan import bevaluate, _bapply

def apply(f, x):
    return _bapply(f, x)

def evaluate(v):
    return bevaluate(v)

try:
    from tests.planvm.test_seed_planvm import requires_planvm, seed_loads
except ImportError:
    def requires_planvm(fn):
        return unittest.skip('planvm not available')(fn)
    def seed_loads(_): return False

CORE_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'prelude', 'src', 'Core')
NAT_PATH = os.path.join(CORE_DIR, 'Nat.gls')
RESULT_PATH = os.path.join(CORE_DIR, 'Result.gls')
MODULE = 'Core.Result'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compile_result():
    from bootstrap.build import build_modules
    with open(NAT_PATH) as f:
        nat_src = f.read()
    with open(RESULT_PATH) as f:
        res_src = f.read()
    return build_modules([('Core.Nat', nat_src), ('Core.Result', res_src)])

_RESULT_COMPILED = None

def _get_result():
    global _RESULT_COMPILED
    if _RESULT_COMPILED is None:
        _RESULT_COMPILED = _compile_result()
    return _RESULT_COMPILED


def mk_ok(val):
    """Ok a = A(tag=0, a)"""
    return A(0, val)

def mk_err(val):
    """Err b = A(tag=1, b)"""
    return A(1, val)


# ---------------------------------------------------------------------------
# Layer 1a: harness correctness
# ---------------------------------------------------------------------------

class TestCoreResultHarness(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.c = _get_result()

    def fn(self, name):
        fq = f'{MODULE}.{name}'
        self.assertIn(fq, self.c, f"'{fq}' not compiled")
        return self.c[fq]

    # --- is_ok ---

    def test_is_ok_true(self):
        result = evaluate(apply(self.fn('is_ok'), mk_ok(N(42))))
        self.assertEqual(result, 1)  # True

    def test_is_ok_false(self):
        result = evaluate(apply(self.fn('is_ok'), mk_err(N(99))))
        self.assertEqual(result, 0)  # False

    # --- is_err ---

    def test_is_err_true(self):
        result = evaluate(apply(self.fn('is_err'), mk_err(N(99))))
        self.assertEqual(result, 1)  # True

    def test_is_err_false(self):
        result = evaluate(apply(self.fn('is_err'), mk_ok(N(42))))
        self.assertEqual(result, 0)  # False

    # --- with_ok ---

    def test_with_ok_present(self):
        result = evaluate(apply(apply(self.fn('with_ok'), N(0)), mk_ok(N(42))))
        self.assertEqual(result, 42)

    def test_with_ok_absent(self):
        result = evaluate(apply(apply(self.fn('with_ok'), N(0)), mk_err(N(99))))
        self.assertEqual(result, 0)  # default

    # --- with_err ---

    def test_with_err_present(self):
        result = evaluate(apply(apply(self.fn('with_err'), N(0)), mk_err(N(99))))
        self.assertEqual(result, 99)

    def test_with_err_absent(self):
        result = evaluate(apply(apply(self.fn('with_err'), N(0)), mk_ok(N(42))))
        self.assertEqual(result, 0)  # default

    # --- map_ok ---

    def test_map_ok_on_ok(self):
        """map_ok inc (Ok 10) = Ok 11"""
        inc_fn = make_bplan_law("Inc", 1)
        result = evaluate(apply(apply(self.fn('map_ok'), inc_fn), mk_ok(N(10))))
        # Result should be Ok 11 = A(0, 11)
        self.assertTrue(hasattr(result, 'fun'), f'Expected App, got {result}')
        self.assertEqual(result.fun, 0)  # tag Ok
        self.assertEqual(result.arg, 11)

    def test_map_ok_on_err(self):
        """map_ok inc (Err 99) = Err 99"""
        inc_fn = make_bplan_law("Inc", 1)
        result = evaluate(apply(apply(self.fn('map_ok'), inc_fn), mk_err(N(99))))
        self.assertTrue(hasattr(result, 'fun'), f'Expected App, got {result}')
        self.assertEqual(result.fun, 1)  # tag Err
        self.assertEqual(result.arg, 99)

    # --- map_err ---

    def test_map_err_on_err(self):
        """map_err inc (Err 10) = Err 11"""
        inc_fn = make_bplan_law("Inc", 1)
        result = evaluate(apply(apply(self.fn('map_err'), inc_fn), mk_err(N(10))))
        self.assertTrue(hasattr(result, 'fun'), f'Expected App, got {result}')
        self.assertEqual(result.fun, 1)  # tag Err
        self.assertEqual(result.arg, 11)

    def test_map_err_on_ok(self):
        """map_err inc (Ok 42) = Ok 42"""
        inc_fn = make_bplan_law("Inc", 1)
        result = evaluate(apply(apply(self.fn('map_err'), inc_fn), mk_ok(N(42))))
        self.assertTrue(hasattr(result, 'fun'), f'Expected App, got {result}')
        self.assertEqual(result.fun, 0)  # tag Ok
        self.assertEqual(result.arg, 42)

    # --- bind_result ---

    def test_bind_result_ok(self):
        """bind_result (Ok 10) (λx → Ok (x+1)) = Ok 11"""
        # Body: Ok (Inc x) — wrap the BPLAN Inc dispatch in the Ok ctor (tag 0).
        # Inner: ((P("B")) ("Inc" slot_1)).
        from dev.harness.plan import B_PIN as _B, str_nat as _strnat
        inc_call = A(A(0, _B), A(A(0, A(0, _strnat('Inc'))), 1))
        ok_inc = L(1, 0, A(A(0, A(0, 0)), inc_call))
        result = evaluate(apply(apply(self.fn('bind_result'), mk_ok(N(10))), ok_inc))
        self.assertTrue(hasattr(result, 'fun'), f'Expected App, got {result}')
        self.assertEqual(result.fun, 0)  # tag Ok
        self.assertEqual(result.arg, 11)

    def test_bind_result_err(self):
        """bind_result (Err 99) f = Err 99 (f not called)"""
        # Body: Ok (Inc x) — wrap the BPLAN Inc dispatch in the Ok ctor (tag 0).
        # Inner: ((P("B")) ("Inc" slot_1)).
        from dev.harness.plan import B_PIN as _B, str_nat as _strnat
        inc_call = A(A(0, _B), A(A(0, A(0, _strnat('Inc'))), 1))
        ok_inc = L(1, 0, A(A(0, A(0, 0)), inc_call))
        result = evaluate(apply(apply(self.fn('bind_result'), mk_err(N(99))), ok_inc))
        self.assertTrue(hasattr(result, 'fun'), f'Expected App, got {result}')
        self.assertEqual(result.fun, 1)  # tag Err
        self.assertEqual(result.arg, 99)

    # --- Eq Result ---

    def _eq_result(self, x, y):
        eq_res = self.c[f'{MODULE}.inst_Eq_Result_eq']
        nat_eq = self.c['Core.Nat.inst_Eq_Nat_eq']
        nat_neq = self.c['Core.Nat.inst_Eq_Nat_neq']
        r = eq_res
        r = plan_apply(r, nat_eq)
        r = plan_apply(r, nat_neq)
        r = plan_apply(r, x)
        r = plan_apply(r, y)
        return plan_evaluate(r)

    def test_eq_ok_ok_equal(self):
        self.assertEqual(self._eq_result(mk_ok(N(5)), mk_ok(N(5))), 1)

    def test_eq_ok_ok_unequal(self):
        self.assertEqual(self._eq_result(mk_ok(N(5)), mk_ok(N(3))), 0)

    def test_eq_ok_err(self):
        self.assertEqual(self._eq_result(mk_ok(N(5)), mk_err(N(9))), 0)

    def test_eq_err_ok(self):
        self.assertEqual(self._eq_result(mk_err(N(9)), mk_ok(N(5))), 0)

    def test_eq_err_err_equal(self):
        self.assertEqual(self._eq_result(mk_err(N(9)), mk_err(N(9))), 1)

    def test_eq_err_err_unequal(self):
        self.assertEqual(self._eq_result(mk_err(N(9)), mk_err(N(7))), 0)


# ---------------------------------------------------------------------------
# Layer 2: planvm seed loading
# ---------------------------------------------------------------------------

def _make_seed(name):
    from bootstrap.emit_seed import emit
    compiled = _get_result()
    return emit(compiled, f'{MODULE}.{name}')


class TestCoreResultSeeds(unittest.TestCase):

    @requires_planvm
    def test_ok_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('Ok')))

    @requires_planvm
    def test_err_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('Err')))

    @requires_planvm
    def test_is_ok_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('is_ok')))

    @requires_planvm
    def test_is_err_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('is_err')))

    @requires_planvm
    def test_with_ok_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('with_ok')))

    @requires_planvm
    def test_with_err_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('with_err')))

    @requires_planvm
    def test_map_ok_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('map_ok')))

    @requires_planvm
    def test_map_err_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('map_err')))

    @requires_planvm
    def test_bind_result_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('bind_result')))

    @requires_planvm
    def test_inst_eq_result_eq_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('inst_Eq_Result_eq')))

    @requires_planvm
    def test_inst_eq_result_neq_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('inst_Eq_Result_neq')))


if __name__ == '__main__':
    unittest.main()
