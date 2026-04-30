#!/usr/bin/env python3
"""
F10 regression: `make demo-glass-ir ARGS=...` (i.e. bootstrap.render_demo)
emits a Glass IR rendering of a demo file with the prelude available.
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.render_demo import render_demo_glass_ir, _camel_case_from_path


REPO_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
CALC_PATH = os.path.join(REPO_ROOT, 'demos', 'calculator.gls')
CSV_PATH = os.path.join(REPO_ROOT, 'demos', 'csv_table.gls')


class TestRenderDemo(unittest.TestCase):
    """Glass IR rendering for demo files."""

    def test_camel_case_derivation(self):
        self.assertEqual(_camel_case_from_path('demos/csv_table.gls'), 'CsvTable')
        self.assertEqual(_camel_case_from_path('calculator.gls'), 'Calculator')
        self.assertEqual(_camel_case_from_path('foo/bar/urb_watcher.gls'), 'UrbWatcher')

    def test_render_calculator_emits_decls(self):
        """The calculator demo should render its top-level lets in IR form."""
        ir = render_demo_glass_ir(CALC_PATH)
        # Calculator has `add`, `mul`, `sub`, plus an `Expr` type and `eval`
        self.assertIn('Calculator.add', ir)
        self.assertIn('Calculator.mul', ir)
        # Top-level let prefix
        self.assertIn('let Calculator.', ir)

    def test_render_csv_table_uses_prelude(self):
        """The csv_table demo (post-F8) imports prelude — render must succeed."""
        ir = render_demo_glass_ir(CSV_PATH)
        self.assertIn('CsvTable.', ir)
        # Per F8, csv_table imports `length` from Core.List; the demo's `let`
        # forms reference it.  Confirm the demo's own decls render and prelude
        # FQ names are visible somewhere in the output.
        self.assertIn('row_count_result', ir)


class TestRenderDemoCLI(unittest.TestCase):
    """`python3 -m bootstrap.render_demo <path>` writes IR to stdout."""

    def test_cli_invocation(self):
        result = subprocess.run(
            [sys.executable, '-m', 'bootstrap.render_demo', CALC_PATH],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0,
                         f'render_demo CLI failed: {result.stderr}')
        self.assertIn('Calculator.', result.stdout)

    def test_cli_missing_file(self):
        result = subprocess.run(
            [sys.executable, '-m', 'bootstrap.render_demo',
             '/nonexistent/path/to/file.gls'],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not found', result.stderr.lower())

    def test_cli_no_args(self):
        result = subprocess.run(
            [sys.executable, '-m', 'bootstrap.render_demo'],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
