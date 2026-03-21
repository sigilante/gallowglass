#!/usr/bin/env python3
"""
Lexer tests — bootstrap/lexer.py

Covers spec/06-surface-syntax.md §1-2 (Lexical Grammar, Token Types).

Run: python3 tests/bootstrap/test_lexer.py
  or: python3 -m pytest tests/bootstrap/test_lexer.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.lexer import (
    lex, LexError, Loc, Token,
    KIND_SNAKE, KIND_PASCAL, KIND_TYPEVAR, KIND_ROWVAR, KIND_KEYWORD,
    KIND_NAT, KIND_TEXT, KIND_RAWTEXT, KIND_BYTES, KIND_HEXBYTES,
    KIND_OP, KIND_PUNCT, KIND_PINLIT, KIND_EOF,
)


def kinds(src):
    """Return list of (kind, value) for all non-EOF tokens."""
    return [(t.kind, t.value) for t in lex(src) if t.kind != KIND_EOF]

def toks(src):
    return [t for t in lex(src) if t.kind != KIND_EOF]


# ============================================================
# Whitespace and comments
# ============================================================

def test_empty_source():
    tokens = lex('')
    assert len(tokens) == 1
    assert tokens[0].kind == KIND_EOF

def test_whitespace_only():
    tokens = lex('   \t\n  ')
    assert len(tokens) == 1
    assert tokens[0].kind == KIND_EOF

def test_line_comment_stripped():
    assert kinds('-- this is a comment\n42') == [(KIND_NAT, 42)]

def test_line_comment_no_newline():
    """Line comment at end of file (no trailing newline) is valid."""
    assert kinds('-- comment') == []

def test_block_comment_stripped():
    assert kinds('{- block -} 42') == [(KIND_NAT, 42)]

def test_block_comment_multiline():
    assert kinds('{-\nmulti\nline\n-} 99') == [(KIND_NAT, 99)]

def test_block_comment_unterminated():
    try:
        lex('{- oops')
        assert False, "should have raised"
    except LexError:
        pass


# ============================================================
# Nat literals
# ============================================================

def test_nat_zero():
    assert kinds('0') == [(KIND_NAT, 0)]

def test_nat_decimal():
    assert kinds('42') == [(KIND_NAT, 42)]

def test_nat_large():
    assert kinds('1000000') == [(KIND_NAT, 1000000)]

def test_nat_hex_lower():
    assert kinds('0xff') == [(KIND_NAT, 255)]

def test_nat_hex_upper():
    assert kinds('0xFF') == [(KIND_NAT, 255)]

def test_nat_hex_large():
    """Underscores are not part of hex literals per spec. '0x1_00' → nat 1, then snake '_00'."""
    result = kinds('0x1_00')
    assert result == [(KIND_NAT, 1), (KIND_SNAKE, '_00')]


# ============================================================
# Text literals
# ============================================================

def test_text_simple():
    assert kinds('"hello"') == [(KIND_TEXT, 'hello')]

def test_text_empty():
    assert kinds('""') == [(KIND_TEXT, '')]

def test_text_escape_newline():
    assert kinds(r'"\n"') == [(KIND_TEXT, '\n')]

def test_text_escape_tab():
    assert kinds(r'"\t"') == [(KIND_TEXT, '\t')]

def test_text_escape_backslash():
    assert kinds(r'"\\"') == [(KIND_TEXT, '\\')]

def test_text_escape_quote():
    assert kinds(r'"\""') == [(KIND_TEXT, '"')]

def test_text_escape_null():
    assert kinds(r'"\0"') == [(KIND_TEXT, '\0')]

def test_text_escape_hex():
    assert kinds(r'"\x41"') == [(KIND_TEXT, 'A')]

def test_text_unterminated():
    try:
        lex('"oops')
        assert False
    except LexError:
        pass

def test_raw_text():
    assert kinds('r"raw\\nno-escape"') == [(KIND_RAWTEXT, 'raw\\nno-escape')]

def test_bytes_lit():
    result = kinds('b"ABC"')
    assert result == [(KIND_BYTES, b'ABC')]

def test_hex_bytes():
    result = kinds('x"41 42 43"')
    assert result == [(KIND_HEXBYTES, b'ABC')]

def test_hex_bytes_no_spaces():
    result = kinds('x"414243"')
    assert result == [(KIND_HEXBYTES, b'ABC')]


# ============================================================
# Identifiers and keywords
# ============================================================

def test_snake_name():
    assert kinds('foo_bar') == [(KIND_SNAKE, 'foo_bar')]

def test_snake_underscore_prefix():
    assert kinds('_foo') == [(KIND_SNAKE, '_foo')]

def test_pascal_name():
    assert kinds('FooBar') == [(KIND_PASCAL, 'FooBar')]

def test_type_var_single():
    assert kinds('a') == [(KIND_TYPEVAR, 'a')]

def test_type_var_boundary():
    # a-q are type vars only when they are a complete token
    assert kinds('ab') == [(KIND_SNAKE, 'ab')]

def test_row_var():
    assert kinds('r') == [(KIND_ROWVAR, 'r')]

def test_row_var_z():
    assert kinds('z') == [(KIND_ROWVAR, 'z')]

def test_keyword_let():
    assert kinds('let') == [(KIND_KEYWORD, 'let')]

def test_keyword_type():
    assert kinds('type') == [(KIND_KEYWORD, 'type')]

def test_keyword_match():
    assert kinds('match') == [(KIND_KEYWORD, 'match')]

def test_keyword_true():
    assert kinds('True') == [(KIND_KEYWORD, 'True')]

def test_keyword_false():
    assert kinds('False') == [(KIND_KEYWORD, 'False')]

def test_keyword_not_prefix():
    """'letter' is a snake name, not a keyword."""
    assert kinds('letter') == [(KIND_SNAKE, 'letter')]


# ============================================================
# Unicode normalization (§1.2)
# ============================================================

def test_normalize_arrow():
    assert kinds('->') == [(KIND_OP, '→')]

def test_normalize_bind():
    assert kinds('<-') == [(KIND_OP, '←')]

def test_normalize_neq():
    assert kinds('/=') == [(KIND_OP, '≠')]

def test_normalize_leq():
    assert kinds('<=') == [(KIND_OP, '≤')]

def test_normalize_geq():
    assert kinds('>=') == [(KIND_OP, '≥')]

def test_normalize_intdiv():
    assert kinds('//') == [(KIND_OP, '/')]

def test_normalize_truediv():
    assert kinds('/') == [(KIND_OP, '÷')]

def test_normalize_fn_to_lambda():
    """'fn' followed by identifier char normalizes to λ keyword."""
    result = kinds('fn x')
    assert result[0] == (KIND_KEYWORD, 'λ')

def test_normalize_fn_not_in_identifier():
    """'fn_helper' is a snake name, not normalized."""
    assert kinds('fn_helper') == [(KIND_SNAKE, 'fn_helper')]

def test_normalize_forall():
    assert kinds('forall') == [(KIND_KEYWORD, '∀')]

def test_normalize_exists():
    assert kinds('exists') == [(KIND_KEYWORD, '∃')]

def test_canonical_unicode_ops_passthrough():
    """Already-canonical Unicode operators pass through unchanged."""
    assert kinds('→') == [(KIND_OP, '→')]
    assert kinds('←') == [(KIND_OP, '←')]
    assert kinds('≠') == [(KIND_OP, '≠')]
    assert kinds('≤') == [(KIND_OP, '≤')]
    assert kinds('≥') == [(KIND_OP, '≥')]
    assert kinds('÷') == [(KIND_OP, '÷')]


# ============================================================
# Operators
# ============================================================

def test_op_plus():
    assert kinds('+') == [(KIND_OP, '+')]

def test_op_minus():
    assert kinds('-') == [(KIND_OP, '-')]

def test_op_star():
    assert kinds('*') == [(KIND_OP, '*')]

def test_op_concat():
    assert kinds('++') == [(KIND_OP, '++')]

def test_op_pipe():
    assert kinds('|>') == [(KIND_OP, '|>')]

def test_op_lt():
    assert kinds('<') == [(KIND_OP, '<')]

def test_op_gt():
    assert kinds('>') == [(KIND_OP, '>')]


# ============================================================
# Punctuation
# ============================================================

def test_punct_eq():
    assert kinds('=') == [(KIND_PUNCT, '=')]

def test_punct_bar():
    assert kinds('|') == [(KIND_PUNCT, '|')]

def test_punct_colon():
    assert kinds(':') == [(KIND_PUNCT, ':')]

def test_punct_cons():
    assert kinds('::') == [(KIND_PUNCT, '::')]

def test_punct_dot():
    assert kinds('.') == [(KIND_PUNCT, '.')]

def test_punct_dotdot():
    assert kinds('..') == [(KIND_PUNCT, '..')]

def test_punct_at():
    assert kinds('@') == [(KIND_PUNCT, '@')]

def test_punct_at_bang():
    assert kinds('@!') == [(KIND_PUNCT, '@!')]

def test_punct_fat_arrow():
    assert kinds('=>') == [(KIND_PUNCT, '=>')]

def test_punct_lparen():
    assert kinds('(') == [(KIND_PUNCT, '(')]

def test_punct_rparen():
    assert kinds(')') == [(KIND_PUNCT, ')')]

def test_punct_lbrace():
    assert kinds('{') == [(KIND_PUNCT, '{')]

def test_punct_rbrace():
    assert kinds('}') == [(KIND_PUNCT, '}')]

def test_punct_lbrack():
    assert kinds('[') == [(KIND_PUNCT, '[')]

def test_punct_rbrack():
    assert kinds(']') == [(KIND_PUNCT, ']')]

def test_punct_comma():
    assert kinds(',') == [(KIND_PUNCT, ',')]

def test_punct_hash():
    assert kinds('#') == [(KIND_PUNCT, '#')]


# ============================================================
# Source locations
# ============================================================

def test_location_first_token():
    tokens = lex('foo', 'test.gls')
    assert tokens[0].loc == Loc('test.gls', 1, 1)

def test_location_after_newline():
    tokens = lex('foo\nbar', 'test.gls')
    bar = next(t for t in tokens if t.kind == KIND_SNAKE and t.value == 'bar')
    assert bar.loc.line == 2
    assert bar.loc.col == 1

def test_location_column():
    tokens = lex('  foo', 'test.gls')
    foo = next(t for t in tokens if t.kind == KIND_SNAKE)
    assert foo.loc.col == 3

def test_eof_location():
    tokens = lex('', 'test.gls')
    assert tokens[-1].kind == KIND_EOF


# ============================================================
# Multi-token sequences
# ============================================================

def test_let_binding():
    # 'n' is a type variable (a-q); use a multi-char name for a snake identifier
    result = kinds('let foo = 42')
    assert result == [
        (KIND_KEYWORD, 'let'),
        (KIND_SNAKE, 'foo'),
        (KIND_PUNCT, '='),
        (KIND_NAT, 42),
    ]

def test_let_binding_typevar():
    # Single chars a-q are type variables per spec §1.3
    result = kinds('let n = 0')
    assert result == [
        (KIND_KEYWORD, 'let'),
        (KIND_TYPEVAR, 'n'),
        (KIND_PUNCT, '='),
        (KIND_NAT, 0),
    ]

def test_let_binding_rowvar():
    # Single chars r-z are row variables per spec §1.3
    result = kinds('let x = 0')
    assert result == [
        (KIND_KEYWORD, 'let'),
        (KIND_ROWVAR, 'x'),
        (KIND_PUNCT, '='),
        (KIND_NAT, 0),
    ]

def test_type_annotation():
    # 'f' is a type variable (a-q); use 'foo' for a function name
    result = kinds('foo : a -> b')
    assert result == [
        (KIND_SNAKE, 'foo'),
        (KIND_PUNCT, ':'),
        (KIND_TYPEVAR, 'a'),
        (KIND_OP, '→'),
        (KIND_TYPEVAR, 'b'),
    ]

def test_single_char_f_is_typevar():
    """Single char 'f' (a-q) is a type variable, not a snake name."""
    assert kinds('f') == [(KIND_TYPEVAR, 'f')]

def test_qualified_name():
    """Core.List is Pascal then . then Pascal — the parser handles qualification."""
    result = kinds('Core.List')
    assert result == [
        (KIND_PASCAL, 'Core'),
        (KIND_PUNCT, '.'),
        (KIND_PASCAL, 'List'),
    ]

def test_match_arm():
    # 'x' is a row variable (r-z) per spec §1.3
    result = kinds('| Ok x ->')
    assert result == [
        (KIND_PUNCT, '|'),
        (KIND_PASCAL, 'Ok'),
        (KIND_ROWVAR, 'x'),
        (KIND_OP, '→'),
    ]

def test_lambda_expression():
    # 'x' is a row variable (r-z); 'fn' normalizes to λ keyword
    result = kinds('fn x -> x')
    assert result == [
        (KIND_KEYWORD, 'λ'),
        (KIND_ROWVAR, 'x'),
        (KIND_OP, '→'),
        (KIND_ROWVAR, 'x'),
    ]

def test_unicode_lambda_direct():
    # λ scanned directly is a keyword; x is a row variable
    result = kinds('λ x → x')
    assert result == [
        (KIND_KEYWORD, 'λ'),
        (KIND_ROWVAR, 'x'),
        (KIND_OP, '→'),
        (KIND_ROWVAR, 'x'),
    ]


# ============================================================
# Additional keyword coverage
# ============================================================

def test_keyword_in():
    assert kinds('in') == [(KIND_KEYWORD, 'in')]

def test_keyword_eff():
    assert kinds('eff') == [(KIND_KEYWORD, 'eff')]

def test_keyword_class():
    assert kinds('class') == [(KIND_KEYWORD, 'class')]

def test_keyword_instance():
    assert kinds('instance') == [(KIND_KEYWORD, 'instance')]

def test_keyword_handle():
    assert kinds('handle') == [(KIND_KEYWORD, 'handle')]

def test_keyword_then():
    assert kinds('then') == [(KIND_KEYWORD, 'then')]

def test_keyword_else():
    assert kinds('else') == [(KIND_KEYWORD, 'else')]

def test_keyword_as():
    assert kinds('as') == [(KIND_KEYWORD, 'as')]

def test_keyword_with():
    assert kinds('with') == [(KIND_KEYWORD, 'with')]

def test_keyword_use():
    assert kinds('use') == [(KIND_KEYWORD, 'use')]

def test_keyword_unit():
    assert kinds('Unit') == [(KIND_KEYWORD, 'Unit')]

def test_keyword_never():
    assert kinds('Never') == [(KIND_KEYWORD, 'Never')]

def test_keyword_not_prefix_class():
    """'classify' is a snake name, not 'class'."""
    assert kinds('classify') == [(KIND_SNAKE, 'classify')]

def test_keyword_not_prefix_in():
    """'info' is a snake name, not 'in'."""
    assert kinds('info') == [(KIND_SNAKE, 'info')]


# ============================================================
# Additional Unicode operator coverage
# ============================================================

def test_op_sum():
    assert kinds('⊕') == [(KIND_OP, '⊕')]

def test_op_product():
    assert kinds('⊗') == [(KIND_OP, '⊗')]

def test_op_top():
    assert kinds('⊤') == [(KIND_OP, '⊤')]

def test_op_bottom():
    assert kinds('⊥') == [(KIND_OP, '⊥')]

def test_op_empty_set():
    assert kinds('∅') == [(KIND_OP, '∅')]

def test_op_element_of():
    assert kinds('∈') == [(KIND_OP, '∈')]

def test_op_not_element_of():
    assert kinds('∉') == [(KIND_OP, '∉')]

def test_op_subset():
    assert kinds('⊆') == [(KIND_OP, '⊆')]

def test_op_compose():
    assert kinds('·') == [(KIND_OP, '·')]


# ============================================================
# Escape sequence edge cases
# ============================================================

def test_text_escape_carriage_return():
    assert kinds(r'"\r"') == [(KIND_TEXT, '\r')]

def test_text_escape_invalid():
    """Unknown escape sequence should raise LexError."""
    try:
        lex(r'"\q"')
        assert False, "should have raised"
    except LexError:
        pass

def test_text_escape_hex_invalid():
    """Non-hex digits after \\x should raise LexError."""
    try:
        lex(r'"\xZZ"')
        assert False, "should have raised"
    except LexError:
        pass

def test_text_escape_hex_incomplete():
    """Only one hex digit after \\x should raise LexError."""
    try:
        lex(r'"\x4"')
        assert False, "should have raised"
    except LexError:
        pass

def test_hex_bytes_invalid_digit():
    """Non-hex digit inside x"..." should raise LexError."""
    try:
        lex('x"ZZ"')
        assert False, "should have raised"
    except LexError:
        pass


# ============================================================
# Multi-char operator/punctuation boundary cases
# ============================================================

def test_triple_colon_is_cons_then_colon():
    """':::' lexes as '::' (cons) then ':' (colon)."""
    result = kinds(':::')
    assert result == [(KIND_PUNCT, '::'), (KIND_PUNCT, ':')]

def test_triple_dot_is_dotdot_then_dot():
    """'...' lexes as '..' then '.'."""
    result = kinds('...')
    assert result == [(KIND_PUNCT, '..'), (KIND_PUNCT, '.')]

def test_fat_arrow_not_eq_gt():
    """'=>' is a single token, not '=' then '>'."""
    assert kinds('=>') == [(KIND_PUNCT, '=>')]

def test_pipe_gt_is_pipe_op():
    """|> is KIND_OP, not bar then gt."""
    assert kinds('|>') == [(KIND_OP, '|>')]

def test_plus_plus_is_concat():
    """'++' is KIND_OP concat, not two '+'."""
    assert kinds('++') == [(KIND_OP, '++')]


# ============================================================
# Run as script
# ============================================================

if __name__ == '__main__':
    tests = [(name, obj) for name, obj in globals().items()
             if name.startswith('test_') and callable(obj)]
    passed = failed = 0
    for name, fn in sorted(tests):
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except (AssertionError, Exception) as e:
            print(f"  FAIL  {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
