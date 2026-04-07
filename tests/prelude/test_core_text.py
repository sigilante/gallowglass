#!/usr/bin/env python3
"""
Core.Text tests — harness correctness + planvm seed loading.

Layer 1a: Python harness tests (always run).
  Verifies text_length, text_content, text_is_empty, text_eq, pow2,
  text_concat, sub, div_nat, mod_nat, show_digit, show_nat, show_bool.

Layer 2: planvm seed loading (skipped unless planvm is available).
  Verifies every definition in Core.Text produces a planvm-valid seed.

Core.Text depends on Core.Nat (for arithmetic and Eq), so compilation
uses build_modules with Core.Nat as an upstream dependency.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.plan import A, N, is_app, is_nat
from dev.harness.bplan import bevaluate, register_prelude_jets, _bapply

def apply(f, x):
    """Apply using the jet-aware bplan evaluator."""
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
NAT_PATH  = os.path.join(CORE_DIR, 'Nat.gls')
BOOL_PATH = os.path.join(CORE_DIR, 'Bool.gls')
TEXT_PATH = os.path.join(CORE_DIR, 'Text.gls')
MODULE    = 'Core.Text'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_text():
    """Build Core.Nat + Core.Bool + Core.Text, return compiled dict."""
    from bootstrap.build import build_modules
    with open(NAT_PATH)  as f: nat_src  = f.read()
    with open(BOOL_PATH) as f: bool_src = f.read()
    with open(TEXT_PATH) as f: text_src = f.read()
    return build_modules([
        ('Core.Nat',  nat_src),
        ('Core.Bool', bool_src),
        ('Core.Text', text_src),
    ])


def text_plan(s: str):
    """Build the PLAN encoding of a Python string as a Text value."""
    b = s.encode('utf-8')
    content = int.from_bytes(b, 'little') if b else 0
    return A(N(len(b)), N(content))


def check_text(v, expected: str) -> bool:
    """Return True if PLAN value v is the Text encoding of `expected`."""
    b = expected.encode('utf-8')
    content = int.from_bytes(b, 'little') if b else 0
    return (is_app(v) and v.fun == len(b) and v.arg == content)


# ---------------------------------------------------------------------------
# Layer 1a: harness correctness
# ---------------------------------------------------------------------------

class TestCoreTextHarness(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.compiled = _load_text()
        register_prelude_jets(cls.compiled)

    def fn(self, name):
        fq = f'{MODULE}.{name}'
        self.assertIn(fq, self.compiled, f"'{fq}' not compiled")
        return self.compiled[fq]

    # --- text_length ---

    def test_text_length_empty(self):
        result = evaluate(apply(self.fn('text_length'), text_plan('')))
        self.assertEqual(result, 0)

    def test_text_length_one(self):
        result = evaluate(apply(self.fn('text_length'), text_plan('A')))
        self.assertEqual(result, 1)

    def test_text_length_hello(self):
        result = evaluate(apply(self.fn('text_length'), text_plan('hello')))
        self.assertEqual(result, 5)

    # --- text_content ---

    def test_text_content_empty(self):
        result = evaluate(apply(self.fn('text_content'), text_plan('')))
        self.assertEqual(result, 0)

    def test_text_content_A(self):
        result = evaluate(apply(self.fn('text_content'), text_plan('A')))
        self.assertEqual(result, ord('A'))  # 65

    # --- text_is_empty ---

    def test_text_is_empty_yes(self):
        result = evaluate(apply(self.fn('text_is_empty'), text_plan('')))
        self.assertEqual(result, 1)  # True

    def test_text_is_empty_no(self):
        result = evaluate(apply(self.fn('text_is_empty'), text_plan('x')))
        self.assertEqual(result, 0)  # False

    # --- text_eq ---

    def test_text_eq_same(self):
        eq = self.fn('text_eq')
        result = evaluate(apply(apply(eq, text_plan('hello')), text_plan('hello')))
        self.assertEqual(result, 1)

    def test_text_eq_different(self):
        eq = self.fn('text_eq')
        result = evaluate(apply(apply(eq, text_plan('hello')), text_plan('world')))
        self.assertEqual(result, 0)

    def test_text_eq_empty_empty(self):
        eq = self.fn('text_eq')
        result = evaluate(apply(apply(eq, text_plan('')), text_plan('')))
        self.assertEqual(result, 1)

    def test_text_eq_empty_nonempty(self):
        eq = self.fn('text_eq')
        result = evaluate(apply(apply(eq, text_plan('')), text_plan('a')))
        self.assertEqual(result, 0)

    # --- pow2 ---

    def test_pow2_zero(self):
        result = evaluate(apply(self.fn('pow2'), N(0)))
        self.assertEqual(result, 1)

    def test_pow2_one(self):
        result = evaluate(apply(self.fn('pow2'), N(1)))
        self.assertEqual(result, 2)

    def test_pow2_eight(self):
        result = evaluate(apply(self.fn('pow2'), N(8)))
        self.assertEqual(result, 256)

    def test_pow2_ten(self):
        result = evaluate(apply(self.fn('pow2'), N(10)))
        self.assertEqual(result, 1024)

    # --- text_concat ---

    def test_text_concat_empty_empty(self):
        result = evaluate(apply(apply(self.fn('text_concat'), text_plan('')), text_plan('')))
        self.assertTrue(check_text(result, ''))

    def test_text_concat_empty_hello(self):
        result = evaluate(apply(apply(self.fn('text_concat'), text_plan('')), text_plan('hello')))
        self.assertTrue(check_text(result, 'hello'))

    def test_text_concat_hello_empty(self):
        result = evaluate(apply(apply(self.fn('text_concat'), text_plan('hello')), text_plan('')))
        self.assertTrue(check_text(result, 'hello'))

    def test_text_concat_hello_world(self):
        result = evaluate(apply(apply(self.fn('text_concat'), text_plan('hello')), text_plan(' world')))
        self.assertTrue(check_text(result, 'hello world'))

    def test_text_concat_single_chars(self):
        # "4" ++ "2" = "42"
        result = evaluate(apply(apply(self.fn('text_concat'), text_plan('4')), text_plan('2')))
        self.assertTrue(check_text(result, '42'))

    # --- sub, div_nat, mod_nat (now in Core.Nat, imported by Core.Text) ---

    def nat_fn(self, name):
        fq = f'Core.Nat.{name}'
        self.assertIn(fq, self.compiled, f"'{fq}' not compiled")
        return self.compiled[fq]

    def test_sub_zero(self):
        result = evaluate(apply(apply(self.nat_fn('sub'), N(5)), N(0)))
        self.assertEqual(result, 5)

    def test_sub_basic(self):
        result = evaluate(apply(apply(self.nat_fn('sub'), N(10)), N(3)))
        self.assertEqual(result, 7)

    def test_sub_saturating(self):
        result = evaluate(apply(apply(self.nat_fn('sub'), N(2)), N(5)))
        self.assertEqual(result, 0)

    def test_div_nat_zero(self):
        result = evaluate(apply(apply(self.nat_fn('div_nat'), N(0)), N(3)))
        self.assertEqual(result, 0)

    def test_div_nat_basic(self):
        result = evaluate(apply(apply(self.nat_fn('div_nat'), N(10)), N(3)))
        self.assertEqual(result, 3)

    def test_div_nat_exact(self):
        result = evaluate(apply(apply(self.nat_fn('div_nat'), N(12)), N(4)))
        self.assertEqual(result, 3)

    def test_div_nat_by_ten(self):
        result = evaluate(apply(apply(self.nat_fn('div_nat'), N(42)), N(10)))
        self.assertEqual(result, 4)

    def test_mod_nat_zero(self):
        result = evaluate(apply(apply(self.nat_fn('mod_nat'), N(0)), N(7)))
        self.assertEqual(result, 0)

    def test_mod_nat_basic(self):
        result = evaluate(apply(apply(self.nat_fn('mod_nat'), N(10)), N(3)))
        self.assertEqual(result, 1)

    def test_mod_nat_exact(self):
        result = evaluate(apply(apply(self.nat_fn('mod_nat'), N(12)), N(4)))
        self.assertEqual(result, 0)

    def test_mod_nat_by_ten(self):
        result = evaluate(apply(apply(self.nat_fn('mod_nat'), N(42)), N(10)))
        self.assertEqual(result, 2)

    # --- show_digit ---

    def test_show_digit_zero(self):
        result = evaluate(apply(self.fn('show_digit'), N(0)))
        self.assertTrue(check_text(result, '0'))

    def test_show_digit_nine(self):
        result = evaluate(apply(self.fn('show_digit'), N(9)))
        self.assertTrue(check_text(result, '9'))

    def test_show_digit_five(self):
        result = evaluate(apply(self.fn('show_digit'), N(5)))
        self.assertTrue(check_text(result, '5'))

    # --- show_nat ---

    def test_show_nat_zero(self):
        result = evaluate(apply(self.fn('show_nat'), N(0)))
        self.assertTrue(check_text(result, '0'))

    def test_show_nat_single(self):
        result = evaluate(apply(self.fn('show_nat'), N(7)))
        self.assertTrue(check_text(result, '7'))

    def test_show_nat_nine(self):
        result = evaluate(apply(self.fn('show_nat'), N(9)))
        self.assertTrue(check_text(result, '9'))

    def test_show_nat_ten(self):
        result = evaluate(apply(self.fn('show_nat'), N(10)))
        self.assertTrue(check_text(result, '10'))

    def test_show_nat_two_digits(self):
        result = evaluate(apply(self.fn('show_nat'), N(42)))
        self.assertTrue(check_text(result, '42'))

    def test_show_nat_three_digits(self):
        result = evaluate(apply(self.fn('show_nat'), N(100)))
        self.assertTrue(check_text(result, '100'))

    # --- show_bool ---

    def test_show_bool_true(self):
        result = evaluate(apply(self.fn('show_bool'), N(1)))
        self.assertTrue(check_text(result, 'True'))

    def test_show_bool_false(self):
        result = evaluate(apply(self.fn('show_bool'), N(0)))
        self.assertTrue(check_text(result, 'False'))

    # --- instance Show Bool dispatch ---

    def test_inst_show_bool_method(self):
        """inst_Show_Bool_show dispatches to show_bool."""
        fn = self.compiled.get('Core.Text.inst_Show_Bool_show')
        self.assertIsNotNone(fn, 'Core.Text.inst_Show_Bool_show not found')
        result = evaluate(apply(fn, N(1)))
        self.assertTrue(check_text(result, 'True'))

    # --- instance Show Nat dispatch ---

    def test_inst_show_nat_method(self):
        """inst_Show_Nat_show dispatches to show_nat."""
        fn = self.compiled.get('Core.Text.inst_Show_Nat_show')
        self.assertIsNotNone(fn, 'Core.Text.inst_Show_Nat_show not found')
        result = evaluate(apply(fn, N(42)))
        self.assertTrue(check_text(result, '42'))


# ---------------------------------------------------------------------------
# Layer 2: planvm seed loading
# ---------------------------------------------------------------------------

def _make_seed(name):
    from bootstrap.emit import emit
    compiled = _load_text()
    return emit(compiled, f'{MODULE}.{name}')

def _make_nat_seed(name):
    from bootstrap.emit import emit
    compiled = _load_text()
    return emit(compiled, f'Core.Nat.{name}')


class TestCoreTextSeeds(unittest.TestCase):

    @requires_planvm
    def test_text_length_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('text_length')))

    @requires_planvm
    def test_text_content_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('text_content')))

    @requires_planvm
    def test_text_is_empty_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('text_is_empty')))

    @requires_planvm
    def test_text_eq_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('text_eq')))

    @requires_planvm
    def test_inst_eq_text_eq_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('inst_Eq_Text_eq')))

    @requires_planvm
    def test_inst_eq_text_neq_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('inst_Eq_Text_neq')))

    @requires_planvm
    def test_pow2_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('pow2')))

    @requires_planvm
    def test_text_concat_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('text_concat')))

    @requires_planvm
    def test_sub_seed_loads(self):
        self.assertTrue(seed_loads(_make_nat_seed('sub')))

    @requires_planvm
    def test_div_nat_seed_loads(self):
        self.assertTrue(seed_loads(_make_nat_seed('div_nat')))

    @requires_planvm
    def test_mod_nat_seed_loads(self):
        self.assertTrue(seed_loads(_make_nat_seed('mod_nat')))

    @requires_planvm
    def test_show_digit_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('show_digit')))

    @requires_planvm
    def test_show_nat_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('show_nat')))

    @requires_planvm
    def test_show_bool_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('show_bool')))

    @requires_planvm
    def test_inst_show_bool_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('inst_Show_Bool')))

    @requires_planvm
    def test_inst_show_nat_seed_loads(self):
        self.assertTrue(seed_loads(_make_seed('inst_Show_Nat')))


if __name__ == '__main__':
    unittest.main()
