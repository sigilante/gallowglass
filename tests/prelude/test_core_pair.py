#!/usr/bin/env python3
"""
Core.Pair tests — harness correctness + planvm seed loading.

Layer 1a: Python harness tests (always run).
  Verifies fst, snd, map_fst, map_snd, swap.

Layer 2: planvm seed loading (skipped unless planvm is available).
  Verifies every definition in Core.Pair produces a planvm-valid seed.

Encoding: MkPair a b = A(A(tag=0, a), b).  Single constructor, tag always 0.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.plan import A, N, L, P, evaluate as raw_evaluate
from dev.harness.bplan import bevaluate, _bapply

def apply(f, x):
    return _bapply(f, x)

def evaluate(v):
    return bevaluate(v)

try:
    from tests.planvm.test_seed_planvm import requires_planvm, seed_loads
except ImportError:
    def requires_planvm(fn):
        return unittest.skip('planvm not available')(fn)
    def seed_loads(_): return False

CORE_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'prelude', 'src', 'Core')
PAIR_PATH = os.path.join(CORE_DIR, 'Pair.gls')
MODULE = 'Core.Pair'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compile_pair():
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    with open(PAIR_PATH) as f:
        src = f.read()
    prog = parse(lex(src, PAIR_PATH), PAIR_PATH)
    resolved, _ = resolve(prog, MODULE, {}, PAIR_PATH)
    return compile_program(resolved, MODULE)

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
        inc_fn = L(1, 0, A(A(0, A(0, P(2))), 1))  # λx → x+1
        result = evaluate(apply(apply(self.fn('map_fst'), inc_fn), mk_pair(N(10), N(20))))
        # Result should be MkPair 11 20 = A(A(0, 11), 20)
        self.assertTrue(hasattr(result, 'fun'), f'Expected App, got {result}')
        self.assertTrue(hasattr(result.fun, 'fun'), f'Expected nested App, got {result.fun}')
        self.assertEqual(result.fun.fun, 0)  # tag
        self.assertEqual(result.fun.arg, 11)  # first incremented
        self.assertEqual(result.arg, 20)  # second unchanged

    # --- map_snd ---

    def test_map_snd(self):
        """map_snd inc (MkPair 10 20) = MkPair 10 21"""
        inc_fn = L(1, 0, A(A(0, A(0, P(2))), 1))  # λx → x+1
        result = evaluate(apply(apply(self.fn('map_snd'), inc_fn), mk_pair(N(10), N(20))))
        self.assertTrue(hasattr(result, 'fun'), f'Expected App, got {result}')
        self.assertTrue(hasattr(result.fun, 'fun'), f'Expected nested App, got {result.fun}')
        self.assertEqual(result.fun.fun, 0)  # tag
        self.assertEqual(result.fun.arg, 10)  # first unchanged
        self.assertEqual(result.arg, 21)  # second incremented

    # --- swap ---

    def test_swap(self):
        """swap (MkPair 10 20) = MkPair 20 10"""
        result = evaluate(apply(self.fn('swap'), mk_pair(N(10), N(20))))
        self.assertTrue(hasattr(result, 'fun'), f'Expected App, got {result}')
        self.assertTrue(hasattr(result.fun, 'fun'), f'Expected nested App, got {result.fun}')
        self.assertEqual(result.fun.fun, 0)  # tag
        self.assertEqual(result.fun.arg, 20)  # was second
        self.assertEqual(result.arg, 10)  # was first

    def test_swap_same(self):
        """swap (MkPair 5 5) = MkPair 5 5"""
        result = evaluate(apply(self.fn('swap'), mk_pair(N(5), N(5))))
        self.assertTrue(hasattr(result, 'fun'), f'Expected App, got {result}')
        self.assertEqual(result.fun.arg, 5)
        self.assertEqual(result.arg, 5)


# ---------------------------------------------------------------------------
# Layer 2: planvm seed loading
# ---------------------------------------------------------------------------

def _make_seed(name):
    from bootstrap.emit import emit
    compiled = _get_pair()
    return emit(compiled, f'{MODULE}.{name}')


class TestCorePairSeeds(unittest.TestCase):

    @requires_planvm
    def test_mkpair_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('MkPair')))

    @requires_planvm
    def test_fst_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('fst')))

    @requires_planvm
    def test_snd_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('snd')))

    @requires_planvm
    def test_map_fst_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('map_fst')))

    @requires_planvm
    def test_map_snd_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('map_snd')))

    @requires_planvm
    def test_swap_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('swap')))


if __name__ == '__main__':
    unittest.main()
