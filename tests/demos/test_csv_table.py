#!/usr/bin/env python3
"""
CSV table demo tests (demos/csv_table.gls).

Compiles the CSV table demo with the Python bootstrap and evaluates
the four hardcoded result values against expected outputs.

Table under test:
  age | score | rank
   25 |    87 |    3
   30 |    95 |    1
   22 |    91 |    2

All tests run in the harness (no planvm required).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.plan import A, evaluate

SRC_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'demos', 'csv_table.gls')
MODULE = 'CsvTable'

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


class TestCsvTableCompilation(unittest.TestCase):
    """All top-level names compile without errors."""

    def test_table_present(self):
        self.assertIn('CsvTable.table', compile_demo())

    def test_results_present(self):
        c = compile_demo()
        for name in ('row_count_result', 'col_count_result',
                     'top_score_result', 'get_field_result'):
            with self.subTest(name=name):
                self.assertIn(f'CsvTable.{name}', c)


class TestCsvTableUtilities(unittest.TestCase):
    """List and Nat utilities produce correct results."""

    def test_list_length_nil(self):
        """list_length Nil = 0"""
        c = compile_demo()
        nil = evaluate(c['CsvTable.Nil'])
        result = evaluate(A(c['CsvTable.list_length'], nil))
        self.assertEqual(result, 0)

    def test_list_length_three(self):
        """list_length of a 3-element list = 3"""
        c = compile_demo()
        result = evaluate(c['CsvTable.row0'])
        length = evaluate(A(c['CsvTable.list_length'], result))
        self.assertEqual(length, 3)

    def test_max_nat(self):
        """max_nat 3 8 = 8"""
        c = compile_demo()
        result = evaluate(A(A(c['CsvTable.max_nat'], 3), 8))
        self.assertEqual(result, 8)

    def test_max_nat_first_larger(self):
        """max_nat 8 3 = 8"""
        c = compile_demo()
        result = evaluate(A(A(c['CsvTable.max_nat'], 8), 3))
        self.assertEqual(result, 8)


class TestCsvTableResults(unittest.TestCase):
    """Hardcoded table results are correct."""

    def test_row_count(self):
        """Table has 3 rows."""
        self.assertEqual(eval_name('row_count_result'), 3)

    def test_col_count(self):
        """Each row has 3 columns."""
        self.assertEqual(eval_name('col_count_result'), 3)

    def test_top_score(self):
        """Maximum score (column 1) across all rows = 95."""
        self.assertEqual(eval_name('top_score_result'), 95)

    def test_get_field(self):
        """Field at row 2, column 1 = 91 (score of rank-2 entry)."""
        self.assertEqual(eval_name('get_field_result'), 91)


if __name__ == '__main__':
    unittest.main()
