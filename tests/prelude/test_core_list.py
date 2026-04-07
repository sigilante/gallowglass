#!/usr/bin/env python3
"""
Core.List tests — harness correctness + planvm seed loading.

Layer 1a: Python harness tests (always run).
  Verifies Eq List instance: eq on Nil/Cons values.

Layer 2: planvm seed loading (skipped unless planvm is available).

Core.List depends on Core.Nat (for Eq class), so compilation
uses build_modules with Core.Nat as an upstream dependency.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.plan import A, N, evaluate, apply, is_app
from dev.harness.bplan import bevaluate, _bapply, register_prelude_jets

try:
    from tests.planvm.test_seed_planvm import requires_planvm, seed_loads
except ImportError:
    def requires_planvm(fn):
        return unittest.skip('planvm not available')(fn)
    def seed_loads(_): return False

CORE_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'prelude', 'src', 'Core')
NAT_PATH = os.path.join(CORE_DIR, 'Nat.gls')
LIST_PATH = os.path.join(CORE_DIR, 'List.gls')
MODULE = 'Core.List'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BOOL_PATH = os.path.join(CORE_DIR, 'Bool.gls')
TEXT_PATH = os.path.join(CORE_DIR, 'Text.gls')


def _load_list():
    from bootstrap.build import build_modules
    with open(NAT_PATH) as f:
        nat_src = f.read()
    with open(BOOL_PATH) as f:
        bool_src = f.read()
    with open(TEXT_PATH) as f:
        text_src = f.read()
    with open(LIST_PATH) as f:
        list_src = f.read()
    return build_modules([
        ('Core.Nat', nat_src),
        ('Core.Bool', bool_src),
        ('Core.Text', text_src),
        ('Core.List', list_src),
    ])

_LIST_COMPILED = None

def _get_list():
    global _LIST_COMPILED
    if _LIST_COMPILED is None:
        _LIST_COMPILED = _load_list()
    return _LIST_COMPILED


def mk_nil():
    return N(0)

def mk_list(*elems):
    """Build PLAN encoding of a list from Python values."""
    result = mk_nil()
    for e in reversed(elems):
        result = A(A(1, N(e)), result)  # Cons e result
    return result


# ---------------------------------------------------------------------------
# Layer 1a: harness correctness
# ---------------------------------------------------------------------------

def check_text(v, expected: str) -> bool:
    b = expected.encode('utf-8')
    content = int.from_bytes(b, 'little') if b else 0
    return (is_app(v) and v.fun == len(b) and v.arg == content)


class TestCoreListHarness(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.c = _get_list()
        register_prelude_jets(cls.c)

    def fn(self, name):
        fq = f'{MODULE}.{name}'
        self.assertIn(fq, self.c, f"'{fq}' not compiled")
        return self.c[fq]

    def nat_fn(self, name):
        fq = f'Core.Nat.{name}'
        self.assertIn(fq, self.c, f"'{fq}' not compiled")
        return self.c[fq]

    # --- Eq List ---

    def _eq_list(self, x, y):
        eq_list = self.fn('inst_Eq_List_eq')
        nat_eq = self.nat_fn('inst_Eq_Nat_eq')
        nat_neq = self.nat_fn('inst_Eq_Nat_neq')
        return evaluate(apply(apply(apply(apply(eq_list, nat_eq), nat_neq), x), y))

    def test_eq_nil_nil(self):
        self.assertEqual(self._eq_list(mk_nil(), mk_nil()), 1)

    def test_eq_nil_cons(self):
        self.assertEqual(self._eq_list(mk_nil(), mk_list(1)), 0)

    def test_eq_cons_nil(self):
        self.assertEqual(self._eq_list(mk_list(1), mk_nil()), 0)

    def test_eq_single_equal(self):
        self.assertEqual(self._eq_list(mk_list(5), mk_list(5)), 1)

    def test_eq_single_unequal(self):
        self.assertEqual(self._eq_list(mk_list(5), mk_list(3)), 0)

    def test_eq_multi_equal(self):
        self.assertEqual(self._eq_list(mk_list(1, 2, 3), mk_list(1, 2, 3)), 1)

    def test_eq_multi_unequal(self):
        self.assertEqual(self._eq_list(mk_list(1, 2, 3), mk_list(1, 2, 4)), 0)

    def test_eq_different_lengths(self):
        self.assertEqual(self._eq_list(mk_list(1, 2), mk_list(1, 2, 3)), 0)

    # --- Show List ---

    def _show_list(self, v):
        show_list = self.fn('inst_Show_List_show')
        show_nat = self.c['Core.Text.inst_Show_Nat_show']
        return bevaluate(_bapply(_bapply(show_list, show_nat), v))

    def test_show_empty(self):
        self.assertTrue(check_text(self._show_list(mk_nil()), '[]'))

    def test_show_singleton(self):
        self.assertTrue(check_text(self._show_list(mk_list(5)), '[5]'))

    def test_show_multi(self):
        self.assertTrue(check_text(self._show_list(mk_list(1, 2, 3)), '[1,2,3]'))

    # --- Debug List ---

    def _debug_list(self, v):
        debug_list = self.fn('inst_Debug_List_debug')
        debug_nat = self.c['Core.Text.inst_Debug_Nat_debug']
        return bevaluate(_bapply(_bapply(debug_list, debug_nat), v))

    def test_debug_nil(self):
        self.assertTrue(check_text(self._debug_list(mk_nil()), 'Nil'))

    def test_debug_single(self):
        self.assertTrue(check_text(self._debug_list(mk_list(5)), 'Cons 5 (Nil)'))

    def test_debug_multi(self):
        self.assertTrue(check_text(self._debug_list(mk_list(1, 2)), 'Cons 1 (Cons 2 (Nil))'))


# ---------------------------------------------------------------------------
# Layer 2: planvm seed loading
# ---------------------------------------------------------------------------

def _make_seed(name):
    from bootstrap.emit import emit
    compiled = _get_list()
    return emit(compiled, f'{MODULE}.{name}')


class TestCoreListSeeds(unittest.TestCase):

    @requires_planvm
    def test_nil_constructor_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('Nil')))

    @requires_planvm
    def test_cons_constructor_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('Cons')))

    @requires_planvm
    def test_is_nil_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('is_nil')))

    @requires_planvm
    def test_is_cons_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('is_cons')))

    @requires_planvm
    def test_singleton_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('singleton')))

    @requires_planvm
    def test_head_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('head')))

    @requires_planvm
    def test_tail_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('tail')))

    @requires_planvm
    def test_map_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('map')))

    @requires_planvm
    def test_filter_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('filter')))

    @requires_planvm
    def test_foldl_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('foldl')))

    @requires_planvm
    def test_foldr_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('foldr')))

    @requires_planvm
    def test_list_eq_go_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('list_eq_go')))

    @requires_planvm
    def test_inst_eq_list_eq_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('inst_Eq_List_eq')))

    @requires_planvm
    def test_inst_eq_list_neq_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('inst_Eq_List_neq')))

    @requires_planvm
    def test_show_list_go_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('show_list_go')))

    @requires_planvm
    def test_inst_show_list_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('inst_Show_List_show')))

    @requires_planvm
    def test_debug_list_go_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('debug_list_go')))

    @requires_planvm
    def test_inst_debug_list_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('inst_Debug_List_debug')))


if __name__ == '__main__':
    unittest.main()
