#!/usr/bin/env python3
"""
Core.Option tests — harness correctness + planvm seed loading.

Layer 1a: Python harness tests (always run).
  Verifies Eq Option instance: eq on None/Some values.

Layer 2: planvm seed loading (skipped unless planvm is available).

Core.Option depends on Core.Nat (for Eq class), so compilation
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
OPTION_PATH = os.path.join(CORE_DIR, 'Option.gls')
MODULE = 'Core.Option'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BOOL_PATH = os.path.join(CORE_DIR, 'Bool.gls')
TEXT_PATH = os.path.join(CORE_DIR, 'Text.gls')


def _load_option():
    from bootstrap.build import build_modules
    with open(NAT_PATH) as f:
        nat_src = f.read()
    with open(BOOL_PATH) as f:
        bool_src = f.read()
    with open(TEXT_PATH) as f:
        text_src = f.read()
    with open(OPTION_PATH) as f:
        opt_src = f.read()
    return build_modules([
        ('Core.Nat', nat_src),
        ('Core.Bool', bool_src),
        ('Core.Text', text_src),
        ('Core.Option', opt_src),
    ])

_OPTION_COMPILED = None

def _get_option():
    global _OPTION_COMPILED
    if _OPTION_COMPILED is None:
        _OPTION_COMPILED = _load_option()
    return _OPTION_COMPILED


def mk_none():
    return N(0)

def mk_some(val):
    return A(1, val)


# ---------------------------------------------------------------------------
# Layer 1a: harness correctness
# ---------------------------------------------------------------------------

def check_text(v, expected: str) -> bool:
    b = expected.encode('utf-8')
    content = int.from_bytes(b, 'little') if b else 0
    return (is_app(v) and v.fun == len(b) and v.arg == content)


class TestCoreOptionHarness(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.c = _get_option()
        register_prelude_jets(cls.c)

    def fn(self, name):
        fq = f'{MODULE}.{name}'
        self.assertIn(fq, self.c, f"'{fq}' not compiled")
        return self.c[fq]

    def nat_fn(self, name):
        fq = f'Core.Nat.{name}'
        self.assertIn(fq, self.c, f"'{fq}' not compiled")
        return self.c[fq]

    # --- Eq Option ---

    def _eq_option(self, x, y):
        eq_opt = self.fn('inst_Eq_Option_eq')
        nat_eq = self.nat_fn('inst_Eq_Nat_eq')
        nat_neq = self.nat_fn('inst_Eq_Nat_neq')
        return evaluate(apply(apply(apply(apply(eq_opt, nat_eq), nat_neq), x), y))

    def test_eq_none_none(self):
        self.assertEqual(self._eq_option(mk_none(), mk_none()), 1)

    def test_eq_none_some(self):
        self.assertEqual(self._eq_option(mk_none(), mk_some(N(5))), 0)

    def test_eq_some_none(self):
        self.assertEqual(self._eq_option(mk_some(N(5)), mk_none()), 0)

    def test_eq_some_equal(self):
        self.assertEqual(self._eq_option(mk_some(N(5)), mk_some(N(5))), 1)

    def test_eq_some_unequal(self):
        self.assertEqual(self._eq_option(mk_some(N(5)), mk_some(N(3))), 0)

    # --- Show Option ---

    def _show_option(self, v):
        show_opt = self.fn('inst_Show_Option_show')
        show_nat = self.c['Core.Text.inst_Show_Nat_show']
        return bevaluate(_bapply(_bapply(show_opt, show_nat), v))

    def test_show_none(self):
        self.assertTrue(check_text(self._show_option(mk_none()), 'None'))

    def test_show_some(self):
        self.assertTrue(check_text(self._show_option(mk_some(N(42))), 'Some(42)'))

    def test_show_some_zero(self):
        self.assertTrue(check_text(self._show_option(mk_some(N(0))), 'Some(0)'))

    # --- Debug Option ---

    def _debug_option(self, v):
        debug_opt = self.fn('inst_Debug_Option_debug')
        debug_nat = self.c['Core.Text.inst_Debug_Nat_debug']
        return bevaluate(_bapply(_bapply(debug_opt, debug_nat), v))

    def test_debug_none(self):
        self.assertTrue(check_text(self._debug_option(mk_none()), 'None'))

    def test_debug_some(self):
        self.assertTrue(check_text(self._debug_option(mk_some(N(42))), 'Some 42'))


# ---------------------------------------------------------------------------
# Layer 2: planvm seed loading
# ---------------------------------------------------------------------------

def _make_seed(name):
    from bootstrap.emit import emit
    compiled = _get_option()
    return emit(compiled, f'{MODULE}.{name}')


class TestCoreOptionSeeds(unittest.TestCase):

    @requires_planvm
    def test_none_constructor_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('None')))

    @requires_planvm
    def test_some_constructor_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('Some')))

    @requires_planvm
    def test_is_none_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('is_none')))

    @requires_planvm
    def test_is_some_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('is_some')))

    @requires_planvm
    def test_with_default_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('with_default')))

    @requires_planvm
    def test_map_option_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('map_option')))

    @requires_planvm
    def test_bind_option_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('bind_option')))

    @requires_planvm
    def test_inst_eq_option_eq_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('inst_Eq_Option_eq')))

    @requires_planvm
    def test_inst_eq_option_neq_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('inst_Eq_Option_neq')))

    @requires_planvm
    def test_inst_show_option_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('inst_Show_Option_show')))

    @requires_planvm
    def test_inst_debug_option_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('inst_Debug_Option_debug')))


if __name__ == '__main__':
    unittest.main()
