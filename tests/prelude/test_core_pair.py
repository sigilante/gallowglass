#!/usr/bin/env python3
"""
Core.Pair harness correctness tests.

Verifies fst, snd, map_fst, map_snd, swap against the Python PLAN harness.

Encoding: MkPair a b = A(A(tag=0, a), b).  Single constructor, tag always 0.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.plan import A, N, L, P, evaluate as raw_evaluate, make_bplan_law
from dev.harness.eval import bevaluate
from dev.harness.bplan import _bapply

def apply(f, x):
    return _bapply(f, x)

def evaluate(v):
    return bevaluate(v)

CORE_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'prelude', 'src', 'Core')
NAT_PATH = os.path.join(CORE_DIR, 'Nat.gls')
BOOL_PATH = os.path.join(CORE_DIR, 'Bool.gls')
TEXT_PATH = os.path.join(CORE_DIR, 'Text.gls')
PAIR_PATH = os.path.join(CORE_DIR, 'Pair.gls')
MODULE = 'Core.Pair'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compile_pair():
    """Build the Pair module with its `use` dependencies (Core.Text and
    its transitive deps) loaded.  Pair gained a `use Core.Text` for the
    Show / Debug instances; isolated compilation no longer works."""
    from bootstrap.build import build_modules
    with open(NAT_PATH) as f:
        nat_src = f.read()
    with open(BOOL_PATH) as f:
        bool_src = f.read()
    with open(TEXT_PATH) as f:
        text_src = f.read()
    with open(PAIR_PATH) as f:
        pair_src = f.read()
    return build_modules([
        ('Core.Nat', nat_src),
        ('Core.Bool', bool_src),
        ('Core.Text', text_src),
        ('Core.Pair', pair_src),
    ])

_PAIR_COMPILED = None

def _get_pair():
    global _PAIR_COMPILED
    if _PAIR_COMPILED is None:
        _PAIR_COMPILED = _compile_pair()
    return _PAIR_COMPILED


def mk_pair(a, b):
    """Build PLAN encoding of MkPair a b = A(A(0, a), b)."""
    return A(A(0, a), b)


# ---------------------------------------------------------------------------
# Layer 1a: harness correctness
# ---------------------------------------------------------------------------

class TestCorePairHarness(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.c = _get_pair()

    def fn(self, name):
        fq = f'{MODULE}.{name}'
        self.assertIn(fq, self.c, f"'{fq}' not compiled")
        return self.c[fq]

    # --- fst ---

    def test_fst(self):
        result = evaluate(apply(self.fn('fst'), mk_pair(N(10), N(20))))
        self.assertEqual(result, 10)

    def test_fst_zero(self):
        result = evaluate(apply(self.fn('fst'), mk_pair(N(0), N(99))))
        self.assertEqual(result, 0)

    # --- snd ---

    def test_snd(self):
        result = evaluate(apply(self.fn('snd'), mk_pair(N(10), N(20))))
        self.assertEqual(result, 20)

    def test_snd_zero(self):
        result = evaluate(apply(self.fn('snd'), mk_pair(N(99), N(0))))
        self.assertEqual(result, 0)

    # --- map_fst ---

    def test_map_fst(self):
        """map_fst inc (MkPair 10 20) = MkPair 11 20"""
        inc_fn = make_bplan_law("Inc", 1)
        result = evaluate(apply(apply(self.fn('map_fst'), inc_fn), mk_pair(N(10), N(20))))
        # Result should be MkPair 11 20 = A(A(0, 11), 20)
        self.assertTrue(result.type == 'app', f'Expected App, got {result}')
        self.assertTrue(result.head.type == 'app', f'Expected nested App, got {result.head}')
        self.assertEqual(result.head.head, 0)  # tag
        self.assertEqual(result.head.tail, 11)  # first incremented
        self.assertEqual(result.tail, 20)  # second unchanged

    # --- map_snd ---

    def test_map_snd(self):
        """map_snd inc (MkPair 10 20) = MkPair 10 21"""
        inc_fn = make_bplan_law("Inc", 1)
        result = evaluate(apply(apply(self.fn('map_snd'), inc_fn), mk_pair(N(10), N(20))))
        self.assertTrue(result.type == 'app', f'Expected App, got {result}')
        self.assertTrue(result.head.type == 'app', f'Expected nested App, got {result.head}')
        self.assertEqual(result.head.head, 0)  # tag
        self.assertEqual(result.head.tail, 10)  # first unchanged
        self.assertEqual(result.tail, 21)  # second incremented

    # --- swap ---

    def test_swap(self):
        """swap (MkPair 10 20) = MkPair 20 10"""
        result = evaluate(apply(self.fn('swap'), mk_pair(N(10), N(20))))
        self.assertTrue(result.type == 'app', f'Expected App, got {result}')
        self.assertTrue(result.head.type == 'app', f'Expected nested App, got {result.head}')
        self.assertEqual(result.head.head, 0)  # tag
        self.assertEqual(result.head.tail, 20)  # was second
        self.assertEqual(result.tail, 10)  # was first

    def test_swap_same(self):
        """swap (MkPair 5 5) = MkPair 5 5"""
        result = evaluate(apply(self.fn('swap'), mk_pair(N(5), N(5))))
        self.assertTrue(result.type == 'app', f'Expected App, got {result}')
        self.assertEqual(result.head.tail, 5)
        self.assertEqual(result.tail, 5)


if __name__ == '__main__':
    unittest.main()
