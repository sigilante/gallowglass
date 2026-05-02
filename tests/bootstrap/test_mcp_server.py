#!/usr/bin/env python3
"""
Tests for bootstrap.mcp_server — the four MCP tool implementations.

These exercise the dict-in / dict-out tool functions directly. Stdio /
JSON-RPC transport is exercised separately by ``test_mcp_stdio.py`` (which
is skipped if the ``mcp`` package isn't installed).

Run: python3 -m pytest tests/bootstrap/test_mcp_server.py -v
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.mcp_server import (
    get_prelude, load_prelude, _PRELUDE,
    tool_compile_snippet, tool_infer_type,
    tool_explain_effect_row, tool_render_fragment,
)


# Force the module-level prelude cache to populate once per test process —
# the build is idempotent and we want to share it across test classes.
get_prelude()


class TestCompileSnippet(unittest.TestCase):
    """compile_snippet — happy path and structured errors."""

    def test_simple_snippet_returns_ir_and_pins(self):
        r = tool_compile_snippet({'source': 'let xx = 42'})
        self.assertNotIn('error', r)
        self.assertIn('let Snippet.xx', r['ir'])
        self.assertIn(': Nat', r['ir'])
        self.assertIn('Snippet.xx', r['pins'])
        # Pin hash is a 64-char hex string.
        self.assertEqual(len(r['pins']['Snippet.xx']), 64)

    def test_caller_supplied_module_name(self):
        r = tool_compile_snippet(
            {'source': 'let foo = 1', 'module': 'MyApp.Parser'})
        self.assertNotIn('error', r)
        self.assertIn('let MyApp.Parser.foo', r['ir'])

    def test_uses_prelude_via_fq_reference(self):
        """Snippets reference the cached prelude by FQ name without rebuild."""
        src = 'let inc = λ nn -> Core.Nat.add nn 1'
        r = tool_compile_snippet({'source': src})
        self.assertNotIn('error', r)
        self.assertIn('Core.Nat.add', r['ir'])

    def test_loc_and_pin_in_ir(self):
        """Pre-3 Loc + Pre-1 type annotations + pin hashes all surface."""
        r = tool_compile_snippet({'source': 'let xx = 42'})
        self.assertIn('-- @ <Snippet>:1:1', r['ir'])
        self.assertIn('[pin#', r['ir'])
        self.assertIn(': Nat', r['ir'])

    def test_parse_error_envelope(self):
        r = tool_compile_snippet({'source': 'let xx = '})
        self.assertIn('error', r)
        self.assertEqual(r['error']['stage'], 'parse')
        self.assertIsNotNone(r['error']['loc'])
        self.assertEqual(r['error']['loc']['line'], 1)

    def test_scope_error_envelope(self):
        r = tool_compile_snippet({'source': 'let xx = unknown_name'})
        self.assertIn('error', r)
        self.assertEqual(r['error']['stage'], 'scope')
        self.assertIn("unbound name 'unknown_name'", r['error']['message'])
        self.assertIsNotNone(r['error']['loc'])

    def test_typecheck_error_envelope(self):
        src = 'let foo : Nat -> Nat\n  = λ nn -> "hello"'
        r = tool_compile_snippet({'source': src})
        self.assertIn('error', r)
        self.assertEqual(r['error']['stage'], 'typecheck')
        self.assertIsNotNone(r['error']['loc'])


class TestInferType(unittest.TestCase):
    """infer_type — position-based hover."""

    def test_nat_at_position(self):
        r = tool_infer_type(
            {'source': 'let xx = 42', 'line': 1, 'col': 10})
        self.assertEqual(r, {'type': 'Nat'})

    def test_text_at_position(self):
        r = tool_infer_type(
            {'source': 'let ss = "hi"', 'line': 1, 'col': 10})
        self.assertEqual(r, {'type': 'Text'})

    def test_no_match_returns_null(self):
        r = tool_infer_type(
            {'source': 'let xx = 42', 'line': 1, 'col': 1})
        self.assertEqual(r, {'type': None})

    def test_propagates_parse_error(self):
        r = tool_infer_type(
            {'source': 'let xx = ', 'line': 1, 'col': 1})
        self.assertIn('error', r)
        self.assertEqual(r['error']['stage'], 'parse')


class TestExplainEffectRow(unittest.TestCase):
    """explain_effect_row — pure vs effectful."""

    def test_pure_function_marked_pure(self):
        r = tool_explain_effect_row(
            {'source': 'let inc : Nat -> Nat = λ nn -> nn',
             'fn_name': 'inc'})
        self.assertEqual(r['effects'], [])
        self.assertTrue(r['pure'])
        self.assertEqual(r['full_type'], 'Nat → Nat')

    def test_io_function_lists_effect(self):
        src = '''external mod IoExt {
  print_line : Text -> {IO} (⊤)
}

let greet : Text -> {IO} (⊤)
  = λ nn -> IoExt.print_line nn'''
        r = tool_explain_effect_row(
            {'source': src, 'fn_name': 'greet'})
        self.assertEqual(r['effects'], ['IO'])
        self.assertFalse(r['pure'])
        self.assertIn('{IO}', r['full_type'])

    def test_unknown_definition_returns_lookup_error(self):
        r = tool_explain_effect_row(
            {'source': 'let foo = 1', 'fn_name': 'bar'})
        self.assertIn('error', r)
        self.assertEqual(r['error']['stage'], 'lookup')


class TestRenderFragment(unittest.TestCase):
    """render_fragment — single-definition IR with deps + budget."""

    def test_fragment_with_prelude_dep(self):
        src = 'let inc = λ nn -> Core.Nat.add nn 1'
        r = tool_render_fragment(
            {'source': src, 'fn_name': 'inc'})
        self.assertNotIn('error', r)
        self.assertIn('-- Snapshot: pin#', r['ir'])
        self.assertIn('-- Source: Snippet.inc', r['ir'])
        # The dep on Core.Nat.add is wired up.
        self.assertIn('Core.Nat.add', r['deps'])
        self.assertIn('@![pin#', r['ir'])

    def test_no_budget_means_no_truncation(self):
        src = 'let foo = 42'
        r = tool_render_fragment(
            {'source': src, 'fn_name': 'foo'})
        self.assertNotIn('truncated', r['ir'])

    def test_tight_budget_truncates(self):
        """A tight budget causes deepest deps + body to be cut, with a marker."""
        src = 'let inc = λ nn -> Core.Nat.add nn 1'
        r = tool_render_fragment(
            {'source': src, 'fn_name': 'inc', 'budget': 8})
        self.assertNotIn('error', r)
        self.assertIn('truncated', r['ir'])

    def test_unknown_fn_returns_lookup_error(self):
        r = tool_render_fragment(
            {'source': 'let foo = 1', 'fn_name': 'bar'})
        self.assertIn('error', r)
        self.assertEqual(r['error']['stage'], 'lookup')


class TestPreludeCache(unittest.TestCase):
    """The prelude is built once and reused across tool calls."""

    def test_get_prelude_returns_same_instance(self):
        a = get_prelude()
        b = get_prelude()
        self.assertIs(a, b)

    def test_prelude_has_core_modules(self):
        p = get_prelude()
        # All 8 Core modules should have at least some entries.
        for mod in ('Core.Nat', 'Core.Bool', 'Core.List',
                    'Core.Option', 'Core.Result', 'Core.Text',
                    'Core.Pair', 'Core.Combinators'):
            prefix = f'{mod}.'
            self.assertTrue(
                any(k.startswith(prefix) for k in p.type_env),
                f"prelude missing entries for {mod}",
            )
            self.assertTrue(
                any(k.startswith(prefix) for k in p.pin_ids),
                f"prelude missing pin hashes for {mod}",
            )


if __name__ == '__main__':
    unittest.main()
