#!/usr/bin/env python3
"""
Tests for ``bootstrap.jupyter_kernel.GallowglassEvaluator`` — the
Jupyter-protocol-free core of the kernel.

Covers cell evaluation modes (expression vs program-fragment), state
accumulation across cells, error envelope shape, recursion-limit
surfacing, and the structural value renderer. The Jupyter Kernel
class itself is exercised only at import — its messaging is covered
by ipykernel's own test suite.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.jupyter_kernel import (
    GallowglassEvaluator,
    CellResult,
    _render,
    _format_error_for_stream,
)
from bootstrap.mcp_server import load_prelude


# Build the prelude once for the whole test module — the same snapshot
# is reused across evaluators. Compile time dominates if we don't.
_PRELUDE = load_prelude()


def make_evaluator(module: str = 'Notebook') -> GallowglassEvaluator:
    return GallowglassEvaluator(module=module, prelude=_PRELUDE)


# ---------------------------------------------------------------------------
# Expression mode
# ---------------------------------------------------------------------------

class TestExpressionMode(unittest.TestCase):
    """A cell that is a Gallowglass expression evaluates to a value."""

    def test_simple_arithmetic(self):
        ev = make_evaluator()
        r = ev.eval_cell('1 + 2')
        self.assertEqual(r.value_text, '3')
        self.assertIsNone(r.error)

    def test_precedence(self):
        """`*` binds tighter than `+`."""
        ev = make_evaluator()
        self.assertEqual(ev.eval_cell('2 * 3 + 4').value_text, '10')
        self.assertEqual(ev.eval_cell('2 + 3 * 4').value_text, '14')

    def test_parenthesised(self):
        ev = make_evaluator()
        self.assertEqual(ev.eval_cell('(1 + 2) * 3').value_text, '9')

    def test_let_in_expression(self):
        """Inline `let x = e in body` is an expression."""
        ev = make_evaluator()
        r = ev.eval_cell('let foo = 5 in foo + 10')
        self.assertEqual(r.value_text, '15')

    def test_match_expression(self):
        """Match dispatch on a Nat scrutinee. ``| k → k`` binds the
        predecessor (n-1), per Gallowglass match semantics — the
        non-zero arm pattern variable is the result of one
        ``Dec``-step, so ``pick 7`` evaluates to ``6``."""
        ev = make_evaluator()
        ev.eval_cell(
            'let pick : Nat → Nat = λ nn → match nn { | 0 → 100 | k → k }'
        )
        self.assertEqual(ev.eval_cell('pick 0').value_text, '100')
        self.assertEqual(ev.eval_cell('pick 7').value_text, '6')


# ---------------------------------------------------------------------------
# Program-fragment mode + cross-cell state
# ---------------------------------------------------------------------------

class TestProgramFragmentMode(unittest.TestCase):
    """A cell that is a top-level declaration accumulates into the
    notebook's module source; the next cell can reference it."""

    def test_decl_then_reference(self):
        ev = make_evaluator()
        decl = ev.eval_cell('let foo = 42')
        self.assertTrue(decl.decls_only)
        self.assertIsNone(decl.value_text)
        self.assertIsNone(decl.error)

        ref = ev.eval_cell('foo')
        self.assertEqual(ref.value_text, '42')

    def test_multiple_decls_then_use(self):
        ev = make_evaluator()
        ev.eval_cell('let foo = 5')
        ev.eval_cell('let bar = 10')
        r = ev.eval_cell('foo + bar')
        self.assertEqual(r.value_text, '15')

    def test_function_def_and_call(self):
        ev = make_evaluator()
        ev.eval_cell('let twice : Nat → Nat = λ n → n + n')
        self.assertEqual(ev.eval_cell('twice 21').value_text, '42')

    def test_typed_decl(self):
        ev = make_evaluator()
        r = ev.eval_cell('let bar : Nat = 100')
        self.assertTrue(r.decls_only)
        self.assertEqual(ev.eval_cell('bar').value_text, '100')

    def test_recursive_function(self):
        """`fix` lets a function reference itself; jets keep the
        recursion under Python's stack."""
        ev = make_evaluator()
        ev.eval_cell(
            'let factorial : Nat → Nat '
            '= fix λ self n → match n { | 0 → 1 | k → n * (self k) }'
        )
        self.assertEqual(ev.eval_cell('factorial 10').value_text, '3628800')

    def test_use_import(self):
        """`use Core.X` brings module-qualified names into scope."""
        ev = make_evaluator()
        r = ev.eval_cell('use Core.Pair')
        self.assertTrue(r.decls_only)
        # Constructors are accessible via the module-qualified path.
        result = ev.eval_cell('Pair.MkPair 3 4')
        self.assertIsNone(result.error)
        # Renders as the structural App tree until Show lands.
        self.assertIn('3', result.value_text)
        self.assertIn('4', result.value_text)


# ---------------------------------------------------------------------------
# Errors and recovery
# ---------------------------------------------------------------------------

class TestErrorRecovery(unittest.TestCase):
    """Pipeline errors are surfaced as structured envelopes; the
    accumulator stays consistent so subsequent cells still work."""

    def test_parse_error_surfaces(self):
        ev = make_evaluator()
        r = ev.eval_cell('let foo =')
        self.assertIsNotNone(r.error)
        self.assertEqual(r.error['stage'], 'parse')
        self.assertEqual(r.error['type'], 'ParseError')
        self.assertIn('error', r.error['message'].lower())

    def test_scope_error_surfaces(self):
        ev = make_evaluator()
        r = ev.eval_cell('undefined_name + 1')
        self.assertIsNotNone(r.error)
        self.assertEqual(r.error['stage'], 'scope')

    def test_state_preserved_across_errors(self):
        """A failing cell does not corrupt the accumulator."""
        ev = make_evaluator()
        ev.eval_cell('let foo = 5')
        bad = ev.eval_cell('let foo =')   # parse error
        self.assertIsNotNone(bad.error)
        # Subsequent cell still sees the original `foo`.
        good = ev.eval_cell('foo + 1')
        self.assertEqual(good.value_text, '6')

    def test_error_envelope_has_loc(self):
        ev = make_evaluator()
        r = ev.eval_cell('let foo =')
        self.assertIsNotNone(r.error)
        loc = r.error['loc']
        self.assertIsNotNone(loc)
        self.assertEqual(set(loc), {'file', 'line', 'col'})

    def test_format_error_for_stream(self):
        envelope = {
            'stage': 'parse',
            'message': 'expected expression',
            'type': 'ParseError',
            'loc': {'file': '<cell 1>', 'line': 2, 'col': 5},
        }
        s = _format_error_for_stream(envelope)
        self.assertIn('<cell 1>:2:5:', s)
        self.assertIn('parse error', s)
        self.assertIn('expected expression', s)


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

class TestReset(unittest.TestCase):
    """`reset()` clears accumulated declarations but keeps the
    prelude — equivalent to "Restart Kernel" in Jupyter."""

    def test_reset_clears_decls(self):
        ev = make_evaluator()
        ev.eval_cell('let foo = 5')
        self.assertEqual(ev.eval_cell('foo').value_text, '5')

        ev.reset()
        r = ev.eval_cell('foo')
        self.assertIsNotNone(r.error)
        self.assertEqual(r.error['stage'], 'scope')

    def test_reset_keeps_prelude(self):
        """Prelude bindings (Core.Nat etc.) survive reset — the
        snapshot is shared across resets."""
        ev = make_evaluator()
        ev.reset()
        # Basic arithmetic still works.
        self.assertEqual(ev.eval_cell('1 + 2').value_text, '3')


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

class TestRenderer(unittest.TestCase):
    """The text/plain renderer is best-effort structural until
    M14.5 (Show typeclass) lands."""

    def test_nat_decimal(self):
        self.assertEqual(_render(0, depth=0), '0')
        self.assertEqual(_render(42, depth=0), '42')
        self.assertEqual(_render(10**100, depth=0), str(10**100))

    def test_app_structural(self):
        from dev.harness.plan import A
        v = A(A(0, 3), 4)  # MkPair-shaped: tag 0 applied to 3 and 4
        self.assertEqual(_render(v, depth=0), '((0 3) 4)')

    def test_law_renders_with_arity(self):
        from dev.harness.plan import L
        # name nat for "go" is 28519 (int.from_bytes(b'go', 'little'))
        v = L(2, 28519, 0)
        rendered = _render(v, depth=0)
        self.assertIn('arity=2', rendered)
        self.assertIn("'go'", rendered)

    def test_render_depth_bound(self):
        """Pathological deep App spines render as `...` past the
        depth cap rather than blowing the stack while we're trying to
        show an error."""
        from dev.harness.plan import A
        v = 0
        for _ in range(100):
            v = A(v, 0)
        rendered = _render(v, depth=0)
        self.assertIn('...', rendered)


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------

class TestEmptyInput(unittest.TestCase):
    """A blank cell or whitespace-only cell is a no-op."""

    def test_empty_cell(self):
        ev = make_evaluator()
        r = ev.eval_cell('')
        self.assertTrue(r.decls_only)
        self.assertIsNone(r.value_text)
        self.assertIsNone(r.error)

    def test_whitespace_cell(self):
        ev = make_evaluator()
        r = ev.eval_cell('   \n  \n\t')
        self.assertTrue(r.decls_only)


if __name__ == '__main__':
    unittest.main()
