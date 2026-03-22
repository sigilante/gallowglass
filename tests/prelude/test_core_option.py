#!/usr/bin/env python3
"""
Core.Option planvm seed validation tests.

Verifies that every definition in prelude/src/Core/Option.gls compiles and
produces a seed accepted by planvm.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.planvm.test_seed_planvm import requires_planvm, seed_loads

MODULE = 'Core.Option'
SRC_PATH = os.path.join(os.path.dirname(__file__), '..', '..',
                         'prelude', 'src', 'Core', 'Option.gls')


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


class TestCoreOptionSeeds(unittest.TestCase):

    @requires_planvm
    def test_none_constructor_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('None')))

    @requires_planvm
    def test_some_constructor_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('Some')))

    @requires_planvm
    def test_is_none_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('is_none')))

    @requires_planvm
    def test_is_some_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('is_some')))

    @requires_planvm
    def test_with_default_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('with_default')))

    @requires_planvm
    def test_map_option_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('map_option')))

    @requires_planvm
    def test_bind_option_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('bind_option')))


if __name__ == '__main__':
    unittest.main()
