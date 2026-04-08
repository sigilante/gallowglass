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


# ============================================================
# M15.7e — String interpolation
# ============================================================

class TestStringInterpolation(unittest.TestCase):
    """String interpolation support in GLS compiler."""

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module()

    def test_compiler_compiles_with_interp(self):
        """Compiler.gls compiles cleanly after string interpolation support."""
        self.assertIsNotNone(self.bc)
        self.assertGreater(len(self.bc), 100)

    def test_interp_helpers_present(self):
        """Interpolation-related functions are in compiled output."""
        self.assertIn('Compiler.find_close_brace', self.bc)
        self.assertIn('Compiler.lex_scan_interp_go', self.bc)
        self.assertIn('Compiler.has_interp', self.bc)
        self.assertIn('Compiler.desugar_interp_frag', self.bc)
        self.assertIn('Compiler.desugar_interp_chain', self.bc)

    def test_nn_text_concat_value(self):
        """nn_text_concat encodes 'text_concat' as little-endian nat."""
        expected = int.from_bytes(b'text_concat', 'little')
        result = bevaluate(self.bc['Compiler.nn_text_concat'])
        self.assertEqual(result, expected)

    def test_nn_show_value(self):
        """nn_show encodes 'show' as little-endian nat."""
        expected = int.from_bytes(b'show', 'little')
        result = bevaluate(self.bc['Compiler.nn_show'])
        self.assertEqual(result, expected)

    def test_tok_eat_interp_token_present(self):
        """tok_eat_interp_token helper is in compiled output."""
        self.assertIn('Compiler.tok_eat_interp_token', self.bc)

    def test_bootstrap_interp_desugar(self):
        """Bootstrap compiler desugars string interpolation correctly."""
        from bootstrap.lexer import lex
        from bootstrap.parser import parse
        from bootstrap.scope import resolve
        from bootstrap.codegen import compile_program
        # Simple interpolation: "hello #{42} world"
        # This uses show(42) which requires Show in scope, so test at parser level
        src = 'use Core.Text unqualified { text_concat, show }\nlet main = "value: #{42}"'
        prog = parse(lex(src, '<test>'), '<test>')
        # Parser should desugar to text_concat chain
        # The main body should be an ExprApp (text_concat chain), not ExprText
        from bootstrap.ast import ExprApp, ExprText
        main_body = prog.decls[1].body
        self.assertIsInstance(main_body, ExprApp,
                              "interpolated text should desugar to ExprApp")


# ============================================================
# M15.7f — Records
# ============================================================

class TestRecords(unittest.TestCase):
    """Record support in GLS compiler."""

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module()

    def test_compiler_compiles_with_records(self):
        """Compiler.gls compiles cleanly after record support."""
        self.assertIsNotNone(self.bc)
        self.assertGreater(len(self.bc), 100)

    def test_record_helpers_present(self):
        """Record rt helpers are in compiled output."""
        self.assertIn('Compiler.rt_lookup', self.bc)
        self.assertIn('Compiler.rt_lookup_subset', self.bc)
        self.assertIn('Compiler.rt_has_field', self.bc)
        self.assertIn('Compiler.rt_all_in', self.bc)
        self.assertIn('Compiler.rt_sets_equal', self.bc)

    def test_record_desugaring_helpers_present(self):
        """Record expression/pattern desugaring helpers are present."""
        self.assertIn('Compiler.rt_find_field_expr', self.bc)
        self.assertIn('Compiler.rt_reorder_exprs', self.bc)
        self.assertIn('Compiler.build_con_app', self.bc)
        self.assertIn('Compiler.collect_field_names', self.bc)
        self.assertIn('Compiler.parse_record_expr_fields_pe', self.bc)
        self.assertIn('Compiler.parse_record_pat_fields', self.bc)

    def test_record_update_helpers_present(self):
        """Record update desugaring helpers are present."""
        self.assertIn('Compiler.rt_find_update_expr', self.bc)
        self.assertIn('Compiler.build_update_body_exprs', self.bc)

    def test_collect_record_types_present(self):
        """collect_record_types is in compiled output."""
        self.assertIn('Compiler.collect_record_types', self.bc)
        self.assertIn('Compiler.collect_record_types_go', self.bc)

    def test_rt_threading_through_parser(self):
        """Key parser functions have been updated (still present in output)."""
        self.assertIn('Compiler.parse_expr', self.bc)
        self.assertIn('Compiler.parse_expr_dispatch', self.bc)
        self.assertIn('Compiler.parse_atom_expr_pe', self.bc)
        self.assertIn('Compiler.parse_app_go_pe', self.bc)
        self.assertIn('Compiler.parse_match_arm_pe', self.bc)
        self.assertIn('Compiler.parse_program', self.bc)

    def test_bootstrap_record_construct(self):
        """Bootstrap compiler handles record construction."""
        from bootstrap.lexer import lex
        from bootstrap.parser import parse
        from bootstrap.scope import resolve
        from bootstrap.codegen import compile_program
        src = "type Point = { x : Nat, y : Nat }\nlet main = { x = 10, y = 20 }"
        prog = parse(lex(src, '<test>'), '<test>')
        resolved, _ = resolve(prog, 'Test', {}, '<test>')
        compiled = compile_program(resolved, 'Test')
        self.assertIn('Test.main', compiled)
        result = bevaluate(compiled['Test.main'])
        # Point 10 20 = App(App(Nat(0), Nat(10)), Nat(20))
        self.assertTrue(is_app(result))

    def test_bootstrap_record_match(self):
        """Bootstrap compiler handles record patterns in match."""
        from bootstrap.lexer import lex
        from bootstrap.parser import parse
        from bootstrap.scope import resolve
        from bootstrap.codegen import compile_program
        from bootstrap.ast import ExprMatch
        src = (
            "type Point = { x : Nat, y : Nat }\n"
            "let get_x = λ p → match p {\n"
            "  | { x = px, y = _ } → px\n"
            "}\n"
            "let main = get_x { x = 42, y = 99 }"
        )
        prog = parse(lex(src, '<test>'), '<test>')
        resolved, _ = resolve(prog, 'Test', {}, '<test>')
        compiled = compile_program(resolved, 'Test')
        # Both get_x and main should compile
        self.assertIn('Test.get_x', compiled)
        self.assertIn('Test.main', compiled)

    def test_bootstrap_record_update(self):
        """Bootstrap compiler handles record updates."""
        from bootstrap.lexer import lex
        from bootstrap.parser import parse
        from bootstrap.scope import resolve
        from bootstrap.codegen import compile_program
        from bootstrap.ast import ExprMatch
        src = (
            "type Point = { x : Nat, y : Nat }\n"
            "let update_x = λ p → p { x = 99 }\n"
            "let main = 42"
        )
        prog = parse(lex(src, '<test>'), '<test>')
        resolved, _ = resolve(prog, 'Test', {}, '<test>')
        compiled = compile_program(resolved, 'Test')
        self.assertIn('Test.update_x', compiled)
        self.assertIn('Test.main', compiled)
        result = bevaluate(compiled['Test.main'])
        self.assertEqual(result, 42)


if __name__ == '__main__':
    unittest.main()
