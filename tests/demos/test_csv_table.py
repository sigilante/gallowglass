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
from dev.harness.bplan import bevaluate, _bapply, register_prelude_jets

SRC_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'demos', 'csv_table.gls')
MODULE = 'CsvTable'

_COMPILED = None


def compile_demo():
    """Compile the demo with the full Core prelude available for `use` imports.

    F4 + F7: the demo now imports `Core.List.length`, `Core.Option`, etc. from
    the prelude instead of redefining them inline.  build_with_prelude prepends
    all eight Core modules and returns the merged compiled dict.
    """
    global _COMPILED
    if _COMPILED is not None:
        return _COMPILED
    from bootstrap.build import build_with_prelude
    with open(SRC_PATH) as f:
        src = f.read()
    _COMPILED = build_with_prelude(MODULE, src)
    # Register list jets so length/foldl etc. dispatch natively in the harness.
    register_prelude_jets(_COMPILED)
    return _COMPILED


def eval_name(name):
    c = compile_demo()
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 10000))
    try:
        return bevaluate(c[f'{MODULE}.{name}'])
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

    def test_prelude_length_nil(self):
        """Core.List.length Nil = 0 (via the demo's `length` import)."""
        c = compile_demo()
        nil = bevaluate(c['Core.List.Nil'])
        result = bevaluate(_bapply(c['Core.List.length'], nil))
        self.assertEqual(result, 0)

    def test_prelude_length_three(self):
        """length of a 3-element list = 3."""
        c = compile_demo()
        row0 = bevaluate(c['CsvTable.row0'])
        result = bevaluate(_bapply(c['Core.List.length'], row0))
        self.assertEqual(result, 3)

    def test_max_nat(self):
        """max_nat 3 8 = 8 (defined locally in the demo)."""
        c = compile_demo()
        result = bevaluate(_bapply(_bapply(c['CsvTable.max_nat'], 3), 8))
        self.assertEqual(result, 8)

    def test_max_nat_first_larger(self):
        """max_nat 8 3 = 8."""
        c = compile_demo()
        result = bevaluate(_bapply(_bapply(c['CsvTable.max_nat'], 8), 3))
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
