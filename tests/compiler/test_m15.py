#!/usr/bin/env python3
"""
GLS M15 tests — surface syntax features in Compiler.gls.

M15.7a: Type aliases — parse_type_decl_body skips alias RHS, emits no-op DLet.
M15.7b: List/Cons syntax (TBD)
M15.7c: Or-patterns (TBD)
M15.7d: Guards (TBD)
M15.7e: String interpolation (TBD)
M15.7f: Records (TBD)

Note: GLS parser functions cannot be reliably evaluated via the Python BPLAN
evaluator due to deep recursion in compiled PLAN code (especially is_arity_stop's
9-level nat_eq chain). GLS parser correctness is validated through:
  1. Compiler.gls self-compilation (selfhost regression)
  2. Compiler.gls containing actual type aliases (type Byte = Nat)
  3. Bootstrap compiler integration tests (M15.2 handles aliases)

Run: python3 -m pytest tests/compiler/test_m15.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.plan import A, N, evaluate, is_nat, is_app
from dev.harness.bplan import bevaluate

MODULE = 'Compiler'
SRC_PATH = os.path.join(os.path.dirname(__file__), '..', '..',
                        'compiler', 'src', 'Compiler.gls')

_COMPILED = None


def compile_module():
    global _COMPILED
    if _COMPILED is not None:
        return _COMPILED
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    from dev.harness.bplan import register_jets
    with open(SRC_PATH) as f:
        src = f.read()
    prog = parse(lex(src, SRC_PATH), SRC_PATH)
    resolved, _ = resolve(prog, MODULE, {}, SRC_PATH)
    _COMPILED = compile_program(resolved, MODULE)
    register_jets(_COMPILED)
    return _COMPILED


def nn(s: str) -> int:
    """Name nat: little-endian encoding of ASCII string."""
    return int.from_bytes(s.encode('ascii'), 'little')


# ============================================================
# M15.7a — Type aliases
# ============================================================

class TestTypeAlias(unittest.TestCase):
    """Type alias support in GLS compiler."""

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module()

    def test_skip_to_decl_boundary_present(self):
        """skip_to_decl_boundary is in compiled output."""
        self.assertIn('Compiler.skip_to_decl_boundary', self.bc)

    def test_parse_type_decl_body_present(self):
        """parse_type_decl_body is in compiled output."""
        self.assertIn('Compiler.parse_type_decl_body', self.bc)

    def test_compiler_compiles_with_type_alias(self):
        """Compiler.gls (containing 'type Byte = Nat') compiles cleanly."""
        self.assertIsNotNone(self.bc)
        self.assertGreater(len(self.bc), 100)

    def test_type_alias_does_not_shadow_definitions(self):
        """Type alias 'type Byte = Nat' does not produce a spurious binding."""
        # The alias should be skipped entirely — no Compiler.Byte in output
        self.assertNotIn('Compiler.Byte', self.bc)

    def test_existing_type_decls_unaffected(self):
        """ADT declarations still compile correctly after alias support."""
        # Token type should still produce its constructors (including new ones)
        for name in ['TkNat', 'TkText', 'TkIdent', 'TkLParen', 'TkRParen',
                      'TkLBrace', 'TkRBrace', 'TkBar', 'TkArrow',
                      'TkEqual', 'TkColon', 'TkLet', 'TkType', 'TkEof',
                      'TkLBracket', 'TkRBracket', 'TkColonColon']:
            fq = f'Compiler.{name}'
            self.assertIn(fq, self.bc, f'{fq} not found in compiled output')

    def test_bootstrap_type_alias(self):
        """Bootstrap compiler handles programs with type aliases (M15.2)."""
        from bootstrap.lexer import lex
        from bootstrap.parser import parse
        from bootstrap.scope import resolve
        from bootstrap.codegen import compile_program
        src = "type MyNat = Nat\nlet main = 42"
        prog = parse(lex(src, '<test>'), '<test>')
        resolved, _ = resolve(prog, 'Test', {}, '<test>')
        compiled = compile_program(resolved, 'Test')
        self.assertIn('Test.main', compiled)
        result = bevaluate(compiled['Test.main'])
        self.assertEqual(result, 42)


# ============================================================
# M15.7b — List/Cons syntax
# ============================================================

class TestListSyntax(unittest.TestCase):
    """List/Cons syntax support in GLS compiler."""

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module()

    def test_new_tokens_present(self):
        """TkLBracket, TkRBracket, TkColonColon are in compiled output."""
        self.assertIn('Compiler.TkLBracket', self.bc)
        self.assertIn('Compiler.TkRBracket', self.bc)
        self.assertIn('Compiler.TkColonColon', self.bc)

    def test_list_helpers_present(self):
        """List desugaring name constants and parser are present."""
        self.assertIn('Compiler.nn_Nil', self.bc)
        self.assertIn('Compiler.nn_Cons', self.bc)
        self.assertIn('Compiler.parse_list_expr_pe', self.bc)

    def test_compiler_compiles_with_list_syntax(self):
        """Compiler.gls compiles cleanly after list syntax additions."""
        self.assertIsNotNone(self.bc)
        self.assertGreater(len(self.bc), 100)

    def test_nn_nil_value(self):
        """nn_Nil encodes 'Nil' as little-endian nat."""
        expected = int.from_bytes(b'Nil', 'little')
        result = bevaluate(self.bc['Compiler.nn_Nil'])
        self.assertEqual(result, expected)

    def test_nn_cons_value(self):
        """nn_Cons encodes 'Cons' as little-endian nat."""
        expected = int.from_bytes(b'Cons', 'little')
        result = bevaluate(self.bc['Compiler.nn_Cons'])
        self.assertEqual(result, expected)


# ============================================================
# M15.7c — Or-patterns
# ============================================================

class TestOrPatterns(unittest.TestCase):
    """Or-pattern support in GLS compiler (arm duplication)."""

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module()

    def test_compiler_compiles_with_or_patterns(self):
        """Compiler.gls compiles cleanly after or-pattern support."""
        self.assertIsNotNone(self.bc)
        self.assertGreater(len(self.bc), 100)

    def test_arm_con_upper_present(self):
        """arm_con_upper_pe (or-pattern handler) is in compiled output."""
        self.assertIn('Compiler.arm_con_upper_pe', self.bc)

    def test_parse_match_arms_pe_present(self):
        """parse_match_arms_pe (with append for or-patterns) is present."""
        self.assertIn('Compiler.parse_match_arms_pe', self.bc)


# ============================================================
# M15.7d — Guards
# ============================================================

class TestGuards(unittest.TestCase):
    """Guard support in GLS compiler match expressions."""

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module()

    def test_compiler_compiles_with_guards(self):
        """Compiler.gls compiles cleanly after guard support."""
        self.assertIsNotNone(self.bc)
        self.assertGreater(len(self.bc), 100)

    def test_guard_helpers_present(self):
        """Guard-related functions are in compiled output."""
        self.assertIn('Compiler.nn___guard_scrut', self.bc)
        self.assertIn('Compiler.has_guard_sentinel', self.bc)
        self.assertIn('Compiler.replace_guard_sentinels', self.bc)

    def test_nn_guard_scrut_value(self):
        """nn___guard_scrut encodes '__gs' as little-endian nat."""
        expected = int.from_bytes(b'__gs', 'little')
        result = bevaluate(self.bc['Compiler.nn___guard_scrut'])
        self.assertEqual(result, expected)


if __name__ == '__main__':
    unittest.main()
