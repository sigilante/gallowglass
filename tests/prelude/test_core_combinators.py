#!/usr/bin/env python3
"""
Core.Combinators harness correctness tests.

Verifies that every definition in prelude/src/Core/Combinators.gls
compiles and evaluates correctly under the Python PLAN harness.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

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


# ---------------------------------------------------------------------------
# Layer 1a: harness correctness
# ---------------------------------------------------------------------------

from dev.harness.plan import A, N, L, P, evaluate, apply as plan_apply, make_bplan_law


class TestCoreCombinatorsHarness(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.c = compile_module()

    def fn(self, name):
        return self.c[f'{MODULE}.{name}']

    def test_pipe(self):
        """pipe 10 inc = 11"""
        inc_fn = make_bplan_law("Inc", 1)
        result = evaluate(plan_apply(plan_apply(self.fn('pipe'), N(10)), inc_fn))
        self.assertEqual(result, 11)

    def test_compose(self):
        """compose inc inc 10 = 12"""
        inc_fn = make_bplan_law("Inc", 1)
        result = evaluate(plan_apply(plan_apply(plan_apply(
            self.fn('compose'), inc_fn), inc_fn), N(10)))
        self.assertEqual(result, 12)

    def test_id(self):
        result = evaluate(plan_apply(self.fn('id'), N(42)))
        self.assertEqual(result, 42)

    def test_const(self):
        result = evaluate(plan_apply(plan_apply(self.fn('const'), N(1)), N(2)))
        self.assertEqual(result, 1)

    def test_flip(self):
        """flip const 1 2 = const 2 1 = 2"""
        result = evaluate(plan_apply(plan_apply(plan_apply(
            self.fn('flip'), self.fn('const')), N(1)), N(2)))
        self.assertEqual(result, 2)


if __name__ == '__main__':
    unittest.main()
