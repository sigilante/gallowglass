#!/usr/bin/env python3
"""
Calculator demo tests (demos/calculator.gls).

Compiles the calculator demo with the Python bootstrap and evaluates
the three hardcoded examples against expected Nat outputs.

All tests run in the harness (no planvm required).  Arithmetic uses
pure PLAN recursion; the example values are small enough that the
default recursion limit is sufficient.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.plan import A, evaluate

SRC_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'demos', 'calculator.gls')
MODULE = 'Calculator'

_COMPILED = None


def compile_demo():
    global _COMPILED
    if _COMPILED is not None:
        return _COMPILED
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    with open(SRC_PATH) as f:
        src = f.read()
    prog = parse(lex(src, SRC_PATH), SRC_PATH)
    resolved, _ = resolve(prog, MODULE, {}, SRC_PATH)
    _COMPILED = compile_program(resolved, MODULE)
    return _COMPILED


def eval_name(name):
    c = compile_demo()
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 10000))
    try:
        return evaluate(c[f'{MODULE}.{name}'])
    finally:
        sys.setrecursionlimit(old)


class TestCalculatorCompilation(unittest.TestCase):
    """All top-level names compile without errors."""

    def test_eval_present(self):
        self.assertIn('Calculator.eval', compile_demo())

    def test_examples_present(self):
        c = compile_demo()
        for name in ('example1', 'example2', 'example3'):
            with self.subTest(name=name):
                self.assertIn(f'Calculator.{name}', c)


class TestCalculatorArithmetic(unittest.TestCase):
    """Arithmetic primitives produce correct results."""

    def test_add(self):
        """add 3 4 = 7"""
        c = compile_demo()
        result = evaluate(A(A(c['Calculator.add'], 3), 4))
        self.assertEqual(result, 7)

    def test_mul(self):
        """mul 3 4 = 12"""
        c = compile_demo()
        result = evaluate(A(A(c['Calculator.mul'], 3), 4))
        self.assertEqual(result, 12)

    def test_sub(self):
        """sub 10 3 = 7"""
        c = compile_demo()
        result = evaluate(A(A(c['Calculator.sub'], 10), 3))
        self.assertEqual(result, 7)

    def test_sub_saturating(self):
        """sub 3 10 = 0  (saturating)"""
        c = compile_demo()
        result = evaluate(A(A(c['Calculator.sub'], 3), 10))
        self.assertEqual(result, 0)


class TestCalculatorExamples(unittest.TestCase):
    """Hardcoded expression examples evaluate to expected results."""

    def test_example1(self):
        """(3 + 4) * 2 = 14"""
        self.assertEqual(eval_name('example1'), 14)

    def test_example2(self):
        """10 - (2 * 3) = 4"""
        self.assertEqual(eval_name('example2'), 4)

    def test_example3(self):
        """(1 + 2) * (3 + 4) = 21"""
        self.assertEqual(eval_name('example3'), 21)


if __name__ == '__main__':
    unittest.main()
