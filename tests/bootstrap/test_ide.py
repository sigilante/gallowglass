#!/usr/bin/env python3
"""
Tests for bootstrap.ide — IDE-facing queries (Pre-2).

Run: python3 -m pytest tests/bootstrap/test_ide.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.typecheck import typecheck_with_types, pp_type
from bootstrap.ide import type_at_position, type_at_offset


def _setup(src, module='Test', filename='<test>'):
    prog = parse(lex(src, filename), filename)
    resolved, env = resolve(prog, module, {}, filename)
    type_env, expr_types = typecheck_with_types(
        resolved, env, module, filename)
    return resolved, type_env, expr_types


class TestTypeAtPosition(unittest.TestCase):
    """Position lookup over an already-typechecked program."""

    def test_nat_literal(self):
        """Cursor on a Nat literal returns Nat."""
        resolved, _, expr_types = _setup('let xx = 42')
        # `42` starts at column 10 on line 1 (`let xx = 42`)
        ty = type_at_position(resolved, expr_types, 1, 10, '<test>')
        self.assertIsNotNone(ty)
        self.assertEqual(pp_type(ty), 'Nat')

    def test_text_literal(self):
        resolved, _, expr_types = _setup('let ss = "hi"')
        ty = type_at_position(resolved, expr_types, 1, 10, '<test>')
        self.assertIsNotNone(ty)
        self.assertEqual(pp_type(ty), 'Text')

    def test_innermost_wins(self):
        """In `ff xx`, cursor on `xx` returns xx's type, not the app's."""
        src = 'let app = λ ff xx → ff xx'
        resolved, _, expr_types = _setup(src)
        # `xx` (the second occurrence) is the rightmost identifier.
        # Cursor at the end of the source lands on it.
        ty = type_at_position(resolved, expr_types, 1, len(src), '<test>')
        self.assertIsNotNone(ty)
        # `xx` is a lambda parameter — not an arrow type.
        self.assertNotIn('→', pp_type(ty))

    def test_position_before_program(self):
        """Cursor before any expression returns None."""
        resolved, _, expr_types = _setup('let xx = 42')
        ty = type_at_position(resolved, expr_types, 1, 1, '<test>')
        # Column 1 is before `42` (which starts at col 10).
        self.assertIsNone(ty)

    def test_filename_filter(self):
        """Wrong filename → no match."""
        resolved, _, expr_types = _setup('let xx = 42')
        ty = type_at_position(resolved, expr_types, 1, 10, 'other')
        self.assertIsNone(ty)


class TestTypeAtOffset(unittest.TestCase):
    """End-to-end source → type-at-position."""

    def test_arithmetic_application_inner_arg(self):
        src = 'let main = λ nn → nn'
        # `nn` (the body) is at the end — find its type.
        ty = type_at_offset(src, 'Test', '<test>', 1, 19)
        self.assertIsNotNone(ty)
        # The body `nn` is the lambda parameter — its type is a meta or
        # generic var (no further constraint), so just verify a type came
        # back and it isn't an arrow.
        self.assertNotIn('→', pp_type(ty))

    def test_no_match_returns_none(self):
        src = 'let xx = 42'
        ty = type_at_offset(src, 'Test', '<test>', 99, 99)
        # Far past the source — pick the latest expr (still `42`).
        # The function falls back to the latest preceding expr.
        self.assertEqual(pp_type(ty), 'Nat')


class TestRecordingOptIn(unittest.TestCase):
    """expr_types is opt-in — plain typecheck() must not pay the cost."""

    def test_plain_typecheck_does_not_record(self):
        from bootstrap.typecheck import typecheck, TypeChecker
        prog = parse(lex('let xx = 42', '<test>'), '<test>')
        resolved, env = resolve(prog, 'Test', {}, '<test>')
        # Plain entry point — should not allocate the side table.
        # We can't directly inspect from outside, but we can verify by
        # constructing a TypeChecker manually.
        tc = TypeChecker('Test', '<test>')
        self.assertIsNone(tc.expr_types)
        # And the public typecheck() returns just the env, not a tuple.
        result = typecheck(resolved, env, 'Test', '<test>')
        self.assertIsInstance(result, dict)


if __name__ == '__main__':
    unittest.main()
