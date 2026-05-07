#!/usr/bin/env python3
"""
Smoke test: tutorials/*.ipynb cells re-execute cleanly via the kernel.

Each tutorial is re-run cell-by-cell through ``GallowglassEvaluator``.
The committed notebook output is compared against the freshly-
produced output for every code cell. A mismatch means either the
notebook needs regeneration (``python3 tutorials/_build_lesson_*.py``)
or the kernel's behaviour drifted in a user-visible way — both
worth catching before merge.

This intentionally does *not* shell out to ``jupyter nbconvert``:

* Faster — no kernel-spawn overhead per test.
* No external dependency on a working kernelspec install.
* Exercises the same evaluator path the real kernel uses, since
  ``GallowglassKernel.do_execute`` delegates to ``GallowglassEvaluator``.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.jupyter_kernel import GallowglassEvaluator


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
TUTORIALS_DIR = os.path.join(REPO_ROOT, 'tutorials')


def _load_notebook(name: str) -> dict:
    path = os.path.join(TUTORIALS_DIR, name)
    with open(path) as f:
        return json.load(f)


def _code_cells(nb: dict):
    """Yield (index, source, recorded_text_output) for each code cell."""
    for i, cell in enumerate(nb['cells']):
        if cell['cell_type'] != 'code':
            continue
        # Notebook source is stored as a list of lines or a single
        # string depending on writer.
        src = cell['source']
        if isinstance(src, list):
            src = ''.join(src)
        outputs = cell.get('outputs', [])
        text_out = ''
        if outputs:
            data = outputs[0].get('data', {})
            text_out = data.get('text/plain', '')
            if isinstance(text_out, list):
                text_out = ''.join(text_out)
        yield i, src, text_out


class _TutorialNotebookTestMixin:
    """Shared assertions for one notebook's cell-by-cell behaviour.

    Subclasses set ``NOTEBOOK`` (the .ipynb filename) and ``BUILD``
    (the matching ``_build_lesson_NN.py`` for the regeneration hint
    in the failure message). The test methods walk every code cell
    in the notebook and check (a) it evaluates without raising and
    (b) its committed text/plain output matches what the live
    evaluator produces.
    """

    NOTEBOOK: str = ''
    BUILD: str = ''

    @classmethod
    def _load(cls):
        return _load_notebook(cls.NOTEBOOK)

    def test_cells_execute_without_error(self):
        nb = self._load()
        ev = GallowglassEvaluator()
        for idx, src, _expected in _code_cells(nb):
            result = ev.eval_cell(src)
            self.assertIsNone(
                result.error,
                f'{self.NOTEBOOK} cell {idx} errored: {result.error}\n'
                f'source:\n{src}',
            )

    def test_cell_outputs_match_recorded(self):
        nb = self._load()
        ev = GallowglassEvaluator()
        for idx, src, expected in _code_cells(nb):
            result = ev.eval_cell(src)
            actual = result.value_text or ''
            self.assertEqual(
                actual, expected,
                f'{self.NOTEBOOK} cell {idx} output drift:\n'
                f'  expected: {expected!r}\n'
                f'  actual:   {actual!r}\n'
                f'  source:\n{src}\n'
                f'(re-run `python3 tutorials/{self.BUILD}` to '
                f'regenerate the committed outputs)',
            )


class TestLesson01HelloGallowglass(_TutorialNotebookTestMixin, unittest.TestCase):
    NOTEBOOK = '01-hello-gallowglass.ipynb'
    BUILD = '_build_lesson_01.py'


class TestLesson02Typeclasses(_TutorialNotebookTestMixin, unittest.TestCase):
    NOTEBOOK = '02-typeclasses.ipynb'
    BUILD = '_build_lesson_02.py'


class TestLesson03GlassIR(_TutorialNotebookTestMixin, unittest.TestCase):
    NOTEBOOK = '03-glass-ir.ipynb'
    BUILD = '_build_lesson_03.py'


class TestLesson04EffectsAndHandlers(_TutorialNotebookTestMixin, unittest.TestCase):
    NOTEBOOK = '04-effects-and-handlers.ipynb'
    BUILD = '_build_lesson_04.py'


if __name__ == '__main__':
    unittest.main()
