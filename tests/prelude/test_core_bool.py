#!/usr/bin/env python3
"""
Core.Bool planvm seed validation tests.

Verifies that every definition in prelude/src/Core/Bool.gls compiles and
produces a seed accepted by planvm.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.planvm.test_seed_planvm import requires_planvm, seed_loads

MODULE = 'Core.Bool'
SRC_PATH = os.path.join(os.path.dirname(__file__), '..', '..',
                         'prelude', 'src', 'Core', 'Bool.gls')


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
