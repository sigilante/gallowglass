#!/usr/bin/env python3
"""
Core.List planvm seed validation tests.

Verifies that every definition in prelude/src/Core/List.gls compiles and
produces a seed accepted by planvm.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.planvm.test_seed_planvm import requires_planvm, seed_loads

MODULE = 'Core.List'
SRC_PATH = os.path.join(os.path.dirname(__file__), '..', '..',
                         'prelude', 'src', 'Core', 'List.gls')


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


class TestCoreListSeeds(unittest.TestCase):

    @requires_planvm
    def test_nil_constructor_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('Nil')))

    @requires_planvm
    def test_cons_constructor_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('Cons')))

    @requires_planvm
    def test_is_nil_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('is_nil')))

    @requires_planvm
    def test_is_cons_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('is_cons')))

    @requires_planvm
    def test_singleton_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('singleton')))

    @requires_planvm
    def test_head_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('head')))

    @requires_planvm
    def test_tail_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('tail')))

    @requires_planvm
    def test_map_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('map')))

    @requires_planvm
    def test_filter_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('filter')))

    @requires_planvm
    def test_foldl_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('foldl')))

    @requires_planvm
    def test_foldr_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('foldr')))


if __name__ == '__main__':
    unittest.main()
