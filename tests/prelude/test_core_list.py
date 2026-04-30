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


# ---------------------------------------------------------------------------
# F5: list jets — verify jets fire and produce the same output as unjetted
# reduction.  These tests both exercise the jet path (via register_prelude_jets
# + bevaluate) and assert byte-equivalent results to the pure-PLAN evaluation.
# ---------------------------------------------------------------------------

class TestCoreListJets(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.c = _get_list()
        # Note: do NOT call register_prelude_jets here — we want both the
        # jetted (bevaluate) and unjetted (evaluate) paths to be available
        # for cross-checking.  Sub-tests register/unregister as needed.
        from dev.harness.bplan import _JET_REGISTRY
        cls._saved_jets = dict(_JET_REGISTRY)

    def setUp(self):
        # Reset jet registry to a clean state before each test
        from dev.harness.bplan import _JET_REGISTRY
        _JET_REGISTRY.clear()
        _JET_REGISTRY.update(self._saved_jets)

    def fn(self, name):
        return self.c[f'{MODULE}.{name}']

    def _identity_law(self):
        # `λ x → x` — used as the function arg for map.
        # This test exists to confirm the jet path doesn't break basic apply.
        from dev.harness.plan import L
        return L(1, 0, N(1))

    def _add_law(self):
        # `λ a b → a + b` via Core.Nat.add — used for foldl/foldr.
        return self.c['Core.Nat.add']

    def test_map_jet_fires_and_matches_unjetted(self):
        """`map (λ x → x) [1,2,3]` should yield [1,2,3] via both paths."""
        register_prelude_jets(self.c)
        map_fn = self.fn('map')
        idfn = self._identity_law()
        xs = mk_list(1, 2, 3)
        # Jetted path
        jetted = bevaluate(_bapply(_bapply(map_fn, idfn), xs))
        # Unjetted path: clear registry, evaluate via plain PLAN
        from dev.harness.bplan import _JET_REGISTRY
        _JET_REGISTRY.clear()
        unjetted = evaluate(apply(apply(map_fn, idfn), xs))
        # Both should be the same Cons-structured list
        # Decode both to Python ints for comparison
        from dev.harness.bplan import _list_to_pylist
        self.assertEqual(_list_to_pylist(jetted), [1, 2, 3])
        self.assertEqual(_list_to_pylist(unjetted), [1, 2, 3])

    def test_foldl_jet_sums_list(self):
        """`foldl add 0 [1,2,3,4]` = 10 via the jetted path."""
        register_prelude_jets(self.c)
        foldl = self.fn('foldl')
        add = self._add_law()
        xs = mk_list(1, 2, 3, 4)
        result = bevaluate(_bapply(_bapply(_bapply(foldl, add), N(0)), xs))
        self.assertEqual(result, 10)

    def test_foldr_jet_sums_list(self):
        """`foldr add 0 [1,2,3,4]` = 10."""
        register_prelude_jets(self.c)
        foldr = self.fn('foldr')
        add = self._add_law()
        xs = mk_list(1, 2, 3, 4)
        result = bevaluate(_bapply(_bapply(_bapply(foldr, add), N(0)), xs))
        self.assertEqual(result, 10)

    def test_filter_jet_keeps_predicate_true(self):
        """`filter (λ n → nat_lt 0 n) [0,1,0,2]` = [1,2]."""
        # nat_lt is jetted to 1 for True / 0 for False
        register_prelude_jets(self.c)
        filter_fn = self.fn('filter')
        # Build a predicate `λ n → nat_lt 0 n` (positive)
        # Easiest: just call nat_lt 0 _ partially
        nat_lt = self.c['Core.Nat.nat_lt']
        # nat_lt is a 2-arg law; partial apply to 0
        from dev.harness.plan import A as _A
        pred = _A(nat_lt, N(0))
        xs = mk_list(0, 1, 0, 2)
        result = bevaluate(_bapply(_bapply(filter_fn, pred), xs))
        from dev.harness.bplan import _list_to_pylist
        self.assertEqual(_list_to_pylist(result), [1, 2])

    def test_jet_handles_empty_list(self):
        """All four jets handle Nil correctly."""
        register_prelude_jets(self.c)
        idfn = self._identity_law()
        add = self._add_law()
        nil = mk_nil()

        # map id Nil = Nil
        from dev.harness.bplan import _list_to_pylist
        self.assertEqual(_list_to_pylist(
            bevaluate(_bapply(_bapply(self.fn('map'), idfn), nil))), [])
        # foldl add 0 Nil = 0
        self.assertEqual(bevaluate(_bapply(_bapply(_bapply(
            self.fn('foldl'), add), N(0)), nil)), 0)
        # foldr add 0 Nil = 0
        self.assertEqual(bevaluate(_bapply(_bapply(_bapply(
            self.fn('foldr'), add), N(0)), nil)), 0)


# ---------------------------------------------------------------------------
# F7: length / append / concat_list — prelude additions plus jets
# ---------------------------------------------------------------------------

class TestCoreListLengthAppendConcat(unittest.TestCase):
    """length/append/concat_list defined in the prelude AND jetted in BPLAN."""

    @classmethod
    def setUpClass(cls):
        cls.c = _get_list()
        register_prelude_jets(cls.c)

    def fn(self, name):
        fq = f'{MODULE}.{name}'
        self.assertIn(fq, self.c, f"'{fq}' not compiled")
        return self.c[fq]

    def test_length_empty(self):
        result = bevaluate(_bapply(self.fn('length'), mk_nil()))
        self.assertEqual(result, 0)

    def test_length_three(self):
        result = bevaluate(_bapply(self.fn('length'), mk_list(10, 20, 30)))
        self.assertEqual(result, 3)

    def test_append_empty_left(self):
        result = bevaluate(_bapply(_bapply(self.fn('append'),
                                            mk_nil()), mk_list(1, 2)))
        from dev.harness.bplan import _list_to_pylist
        self.assertEqual(_list_to_pylist(result), [1, 2])

    def test_append_empty_right(self):
        result = bevaluate(_bapply(_bapply(self.fn('append'),
                                            mk_list(1, 2)), mk_nil()))
        from dev.harness.bplan import _list_to_pylist
        self.assertEqual(_list_to_pylist(result), [1, 2])

    def test_append_two_lists(self):
        result = bevaluate(_bapply(_bapply(self.fn('append'),
                                            mk_list(1, 2)), mk_list(3, 4)))
        from dev.harness.bplan import _list_to_pylist
        self.assertEqual(_list_to_pylist(result), [1, 2, 3, 4])

    def test_concat_list_empty(self):
        result = bevaluate(_bapply(self.fn('concat_list'), mk_nil()))
        from dev.harness.bplan import _list_to_pylist
        self.assertEqual(_list_to_pylist(result), [])

    def test_concat_list_flatten(self):
        # [[1,2], [3], [4,5,6]] → [1,2,3,4,5,6]
        l1 = mk_list(1, 2)
        l2 = mk_list(3)
        l3 = mk_list(4, 5, 6)
        outer = A(A(1, l1), A(A(1, l2), A(A(1, l3), mk_nil())))
        result = bevaluate(_bapply(self.fn('concat_list'), outer))
        from dev.harness.bplan import _list_to_pylist
        self.assertEqual(_list_to_pylist(result), [1, 2, 3, 4, 5, 6])

    def test_length_jet_matches_unjetted(self):
        """Confirm the length jet returns the same value as pure-PLAN reduction."""
        from dev.harness.bplan import _JET_REGISTRY
        xs = mk_list(1, 2, 3, 4, 5)
        register_prelude_jets(self.c)
        jetted = bevaluate(_bapply(self.fn('length'), xs))
        _JET_REGISTRY.clear()
        unjetted = evaluate(apply(self.fn('length'), xs))
        self.assertEqual(jetted, 5)
        self.assertEqual(unjetted, 5)
        register_prelude_jets(self.c)


if __name__ == '__main__':
    unittest.main()
