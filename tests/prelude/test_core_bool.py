#!/usr/bin/env python3
"""
Core.Bool planvm seed validation tests.

Verifies that every definition in prelude/src/Core/Bool.gls compiles and
produces a seed accepted by planvm.

Core.Bool depends on Core.Nat (for the Eq class), so compilation uses
build_modules with Core.Nat as an upstream dependency.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.planvm.test_seed_planvm import requires_planvm, seed_loads

MODULE = 'Core.Bool'
CORE_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'prelude', 'src', 'Core')
NAT_PATH  = os.path.join(CORE_DIR, 'Nat.gls')
BOOL_PATH = os.path.join(CORE_DIR, 'Bool.gls')


def make_seed(name):
    from bootstrap.build import build_modules
    from bootstrap.emit import emit
    with open(NAT_PATH) as f:
        nat_src = f.read()
    with open(BOOL_PATH) as f:
        bool_src = f.read()
    compiled = build_modules([('Core.Nat', nat_src), ('Core.Bool', bool_src)])
    return emit(compiled, f'{MODULE}.{name}')


class TestCoreBoolSeeds(unittest.TestCase):

    @requires_planvm
    def test_not_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('not')))

    @requires_planvm
    def test_and_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('and')))

    @requires_planvm
    def test_or_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('or')))

    @requires_planvm
    def test_xor_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('xor')))

    @requires_planvm
    def test_bool_eq_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('bool_eq')))

    @requires_planvm
    def test_bool_select_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('bool_select')))

    @requires_planvm
    def test_inst_eq_bool_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('inst_Eq_Bool')))


if __name__ == '__main__':
    unittest.main()
