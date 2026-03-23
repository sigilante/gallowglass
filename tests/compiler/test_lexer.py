#!/usr/bin/env python3
"""
Compiler lexer tests (Milestone 8.2).

Tests that lexer definitions in compiler/src/Compiler.gls compile to
planvm-valid seeds.  Full end-to-end lexer evaluation is deferred until
planvm eval mode is available (bytes_at uses recursive div/mod which
is too slow for the Python harness at realistic input sizes).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.planvm.test_seed_planvm import requires_planvm, seed_loads

MODULE = 'Compiler'
SRC_PATH = os.path.join(os.path.dirname(__file__), '..', '..',
                         'compiler', 'src', 'Compiler.gls')


def compile_module():
    """Compile the full compiler module."""
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    with open(SRC_PATH) as f:
        src = f.read()
    prog = parse(lex(src, SRC_PATH), SRC_PATH)
    resolved, _ = resolve(prog, MODULE, {}, SRC_PATH)
    return compile_program(resolved, MODULE)


def make_seed(name):
    """Compile and emit a seed for a single definition."""
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    from bootstrap.emit import emit
    with open(SRC_PATH) as f:
        src = f.read()
    prog = parse(lex(src, SRC_PATH), SRC_PATH)
    resolved, _ = resolve(prog, MODULE, {}, SRC_PATH)
    compiled = compile_program(resolved, MODULE)
    return emit(compiled, f'{MODULE}.{name}')


class TestLexerCompilation(unittest.TestCase):
    """Test that lexer definitions compile without errors."""

    @classmethod
    def setUpClass(cls):
        cls.compiled = compile_module()

    def test_module_compiles(self):
        self.assertIsInstance(self.compiled, dict)
        self.assertGreater(len(self.compiled), 0)

    def test_has_byte_classifiers(self):
        expected = [
            'Compiler.is_digit', 'Compiler.is_upper', 'Compiler.is_lower',
            'Compiler.is_alpha', 'Compiler.is_alnum_or_under',
            'Compiler.is_hex_digit', 'Compiler.hex_digit_val',
        ]
        for name in expected:
            self.assertIn(name, self.compiled, f'Missing {name}')

    def test_has_lexpos_helpers(self):
        expected = [
            'Compiler.mk_lexpos',
            'Compiler.lexpos_offset', 'Compiler.lexpos_line',
            'Compiler.lexpos_col', 'Compiler.lexpos_advance',
            'Compiler.lexpos_advance_n', 'Compiler.lexpos_newline',
        ]
        for name in expected:
            self.assertIn(name, self.compiled, f'Missing {name}')

    def test_has_lex_functions(self):
        expected = [
            'Compiler.lex_skip_line', 'Compiler.lex_skip_ws',
            'Compiler.lex_scan_ident_go', 'Compiler.lex_classify_ident',
            'Compiler.lex_scan_nat_dec_go', 'Compiler.lex_scan_nat_hex_go',
            'Compiler.lex_scan_text_go',
            'Compiler.lex_one', 'Compiler.lex',
        ]
        for name in expected:
            self.assertIn(name, self.compiled, f'Missing {name}')

    def test_has_token_type(self):
        """Token constructors are compiled."""
        expected = [
            'Compiler.TkNat', 'Compiler.TkText', 'Compiler.TkIdent',
            'Compiler.TkLParen', 'Compiler.TkRParen',
            'Compiler.TkLBrace', 'Compiler.TkRBrace',
            'Compiler.TkBar', 'Compiler.TkArrow', 'Compiler.TkBackArrow',
            'Compiler.TkEqual', 'Compiler.TkColon', 'Compiler.TkDot',
            'Compiler.TkComma', 'Compiler.TkAt', 'Compiler.TkBacktick',
            'Compiler.TkLet', 'Compiler.TkType', 'Compiler.TkMatch',
            'Compiler.TkIf', 'Compiler.TkThen', 'Compiler.TkElse',
            'Compiler.TkExternal', 'Compiler.TkMod', 'Compiler.TkIn',
            'Compiler.TkLambda', 'Compiler.TkForall', 'Compiler.TkUnderscore',
            'Compiler.TkEof', 'Compiler.TkErr',
        ]
        for name in expected:
            self.assertIn(name, self.compiled, f'Missing token {name}')


class TestByteClassifierEval(unittest.TestCase):
    """Smoke-test byte classifiers via Python harness (fast — no div/mod)."""

    @classmethod
    def setUpClass(cls):
        cls.compiled = compile_module()

    def _get(self, name):
        return self.compiled[f'Compiler.{name}']

    def _eval(self, val, *args):
        from dev.harness.plan import A, evaluate
        import sys
        old = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old, 10000))
        try:
            result = val
            for arg in args:
                result = A(result, arg)
            return evaluate(result)
        finally:
            sys.setrecursionlimit(old)

    def test_is_digit(self):
        f = self._get('is_digit')
        self.assertEqual(self._eval(f, ord('0')), 1)
        self.assertEqual(self._eval(f, ord('9')), 1)
        self.assertEqual(self._eval(f, ord('a')), 0)
        self.assertEqual(self._eval(f, ord(' ')), 0)

    def test_is_alpha(self):
        f = self._get('is_alpha')
        self.assertEqual(self._eval(f, ord('a')), 1)
        self.assertEqual(self._eval(f, ord('Z')), 1)
        self.assertEqual(self._eval(f, ord('5')), 0)

    def test_is_alnum_or_under(self):
        f = self._get('is_alnum_or_under')
        self.assertEqual(self._eval(f, ord('_')), 1)
        self.assertEqual(self._eval(f, ord('a')), 1)
        self.assertEqual(self._eval(f, ord('0')), 1)
        self.assertEqual(self._eval(f, ord(' ')), 0)
        self.assertEqual(self._eval(f, ord('(')), 0)

    def test_is_hex_digit(self):
        f = self._get('is_hex_digit')
        self.assertEqual(self._eval(f, ord('0')), 1)
        self.assertEqual(self._eval(f, ord('f')), 1)
        self.assertEqual(self._eval(f, ord('F')), 1)
        self.assertEqual(self._eval(f, ord('g')), 0)

    def test_hex_digit_val(self):
        f = self._get('hex_digit_val')
        self.assertEqual(self._eval(f, ord('0')), 0)
        self.assertEqual(self._eval(f, ord('9')), 9)
        self.assertEqual(self._eval(f, ord('a')), 10)
        self.assertEqual(self._eval(f, ord('f')), 15)
        self.assertEqual(self._eval(f, ord('A')), 10)
        self.assertEqual(self._eval(f, ord('F')), 15)

    @unittest.skip(
        "lex_classify_ident calls nat_eq on keyword nats (up to 8 bytes = ~10^18); "
        "pure-recursive sub requires millions of iterations — test via planvm instead"
    )
    def test_lex_classify_ident_keywords(self):
        pass

    @unittest.skip(
        "lex_classify_ident calls nat_eq on keyword nats — too slow for Python harness"
    )
    def test_lex_classify_ident_plain(self):
        pass


class TestLexerSeedLoading(unittest.TestCase):
    """Test that lexer definitions produce planvm-valid seeds."""

    SEED_NAMES = [
        'is_digit', 'is_upper', 'is_lower', 'is_alpha',
        'is_alnum_or_under', 'is_hex_digit', 'hex_digit_val',
        'mk_lexpos', 'lexpos_offset', 'lexpos_line', 'lexpos_col',
        'lexpos_advance', 'lexpos_advance_n', 'lexpos_newline',
        'lex_skip_line', 'lex_skip_ws',
        'lex_scan_ident_go', 'lex_classify_ident',
        'lex_scan_nat_dec_go', 'lex_scan_nat_hex_go',
        'lex_scan_text_go', 'lex_one', 'lex',
    ]

    @requires_planvm
    def test_seed_loads(self):
        for name in self.SEED_NAMES:
            with self.subTest(name=name):
                seed = make_seed(name)
                self.assertTrue(seed_loads(seed),
                               f'Seed for {name} rejected by planvm')


if __name__ == '__main__':
    unittest.main()
