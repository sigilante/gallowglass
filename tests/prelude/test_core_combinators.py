#!/usr/bin/env python3
"""
Core.Combinators planvm seed validation tests.

Verifies that every definition in prelude/src/Core/Combinators.gls compiles
and produces a seed accepted by planvm.

Skipped automatically when planvm is not available.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.planvm.test_seed_planvm import (
    planvm_available, requires_planvm, seed_loads,
    compile_to_seed,
)

MODULE = 'Core.Combinators'
SRC_PATH = os.path.join(os.path.dirname(__file__), '..', '..',
                         'prelude', 'src', 'Core', 'Combinators.gls')


def compile_module():
    """Compile Core.Combinators.gls and return the compiled dict."""
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    with open(SRC_PATH) as f:
        src = f.read()
    prog = parse(lex(src, SRC_PATH), SRC_PATH)
    resolved, _ = resolve(prog, MODULE, {}, SRC_PATH)
    return compile_program(resolved, MODULE)


def make_seed(name):
    """Emit a seed for one definition in the compiled module."""
    from bootstrap.emit import emit
    compiled = compile_module()
    return emit(compiled, f'{MODULE}.{name}')


class TestCoreCombinatorsSeeds(unittest.TestCase):
    """Each definition in Core.Combinators produces a planvm-valid seed."""

    @requires_planvm
    def test_id_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('id')), 'planvm rejected seed for id')

    @requires_planvm
    def test_const_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('const')), 'planvm rejected seed for const')

    @requires_planvm
    def test_flip_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('flip')), 'planvm rejected seed for flip')

    @requires_planvm
    def test_compose_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('compose')), 'planvm rejected seed for compose')

    @requires_planvm
    def test_apply_seed_loads(self):
        self.assertTrue(seed_loads(make_seed('apply')), 'planvm rejected seed for apply')


if __name__ == '__main__':
    unittest.main()
