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


if __name__ == '__main__':
    unittest.main()
