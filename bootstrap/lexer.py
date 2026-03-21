"""
Gallowglass bootstrap lexer.

Implements spec/06-surface-syntax.md §1-2.
Input:  source text (str or bytes, UTF-8)
Output: list of Token

Unicode normalization (§1.2) is applied during scanning:
    ->      →    Arrow
    fn      λ    Lambda  (only when followed by identifier char or _)
    forall  ∀    Forall
    exists  ∃    Exists
    <-      ←    Bind
    /=      ≠    NEq
    <=      ≤    LEq     (not part of <-)
    >=      ≥    GEq
    //      /    IntDiv  (longest match: // first, lone / → ÷)
    /       ÷    TrueDiv (lone /)
    =>      =>   FatArrow (kept as-is, already two ASCII chars → single token)

All subsequent passes see only canonical tokens. No ASCII alternative
ever appears past the lexer boundary.

Reference: bootstrap/src/lexer.sire (design stub)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import re


# ---------------------------------------------------------------------------
# Source location
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Loc:
    file: str
    line: int  # 1-based
    col: int   # 1-based

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.col}"


# ---------------------------------------------------------------------------
# Token kinds
# ---------------------------------------------------------------------------

# fmt: off
KEYWORDS = frozenset([
    'mod', 'let', 'type', 'eff', 'class', 'instance',
    'match', 'handle', 'return', 'once', 'fix', 'in',
    'pre', 'post', 'inv', 'law',
    'if', 'then', 'else',
    'forall', 'exists',
    'external', 'use', 'as', 'at', 'with', 'unqualified',
    'export', 'depends', 'modules', 'version', 'package',
    'import', 'from', 'macro',
    'True', 'False', 'Unit', 'Never',
    # Post-normalization canonical forms also reserved:
    'λ', '∀', '∃',
])
# fmt: on

# Multi-character punctuation scanned longest-match first
PUNCT_MULTI = [
    '@!',   # AtBang (Glass IR only)
    '::',   # Cons
    '..',   # DotDot
    '=>',   # FatArrow
]

# Operator-class multi-char sequences (must be checked before PUNCT_MULTI and SINGLE_OPS)
OP_MULTI = [
    '|>',   # Pipe
    '++',   # Concat
]

HEX_CHARS = frozenset('0123456789abcdefABCDEF')

PUNCT_SINGLE = set('=|:,.@#()[]{};?!')


@dataclass(frozen=True)
class Token:
    """A single lexed token."""
    kind: str   # see KIND_* constants below
    value: Any  # kind-specific payload
    loc: Loc

    def __repr__(self) -> str:
        return f"Token({self.kind}, {self.value!r}, {self.loc})"


# Token kind constants
KIND_SNAKE    = 'TSnake'     # snake_case identifier
KIND_PASCAL   = 'TPascal'    # PascalCase identifier
KIND_TYPEVAR  = 'TTypeVar'   # single char a-q (type variable)
KIND_ROWVAR   = 'TRowVar'    # single char r-z (row variable)
KIND_KEYWORD  = 'TKeyword'   # reserved word
KIND_NAT      = 'TNat'       # numeric literal (int)
KIND_TEXT     = 'TText'      # text literal (str, interpolation pre-split)
KIND_RAWTEXT  = 'TRawText'   # raw text literal (str)
KIND_BYTES    = 'TBytes'     # byte literal (bytes)
KIND_HEXBYTES = 'THexBytes'  # hex byte literal (bytes)
KIND_OP       = 'TOp'        # canonical Unicode operator (str)
KIND_PUNCT    = 'TPunct'     # structural punctuation (str)
KIND_PINLIT   = 'TPinLit'    # pin#hexhash (Glass IR only)
KIND_EOF      = 'TEOF'       # end of input


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

class LexError(Exception):
    def __init__(self, msg: str, loc: Loc):
        super().__init__(f"{loc}: error: {msg}")
        self.loc = loc


class Lexer:
    """
    Stateful scanner. Consumes a source string and produces Token objects.

    Usage:
        tokens = Lexer(source, filename).lex()
    """

    def __init__(self, source: str, filename: str = '<stdin>'):
        if isinstance(source, bytes):
            source = source.decode('utf-8')
        self.src = source
        self.filename = filename
        self.pos = 0        # current char index
        self.line = 1
        self.col = 1
        self._tokens: list[Token] = []

    # -- navigation --

    def _loc(self) -> Loc:
        return Loc(self.filename, self.line, self.col)

    def _at_end(self) -> bool:
        return self.pos >= len(self.src)

    def _peek(self, offset: int = 0) -> str:
        idx = self.pos + offset
        return self.src[idx] if idx < len(self.src) else ''

    def _peek_str(self, n: int) -> str:
        return self.src[self.pos:self.pos + n]

    def _advance(self) -> str:
        ch = self.src[self.pos]
        self.pos += 1
        if ch == '\n':
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def _consume(self, s: str) -> bool:
        """Consume s if it matches at current position. Return True on success."""
        if self.src[self.pos:self.pos + len(s)] == s:
            for _ in s:
                self._advance()
            return True
        return False

    def _skip_whitespace_and_comments(self):
        while not self._at_end():
            ch = self._peek()

            # Blank
            if ch in ' \t\n\r':
                self._advance()
                continue

            # Line comment: -- ... \n
            if self._peek_str(2) == '--':
                while not self._at_end() and self._peek() != '\n':
                    self._advance()
                continue

            # Block comment: {- ... -}  (non-nesting)
            if self._peek_str(2) == '{-':
                loc = self._loc()
                self._advance(); self._advance()  # consume {-
                while not self._at_end():
                    if self._peek_str(2) == '-}':
                        self._advance(); self._advance()
                        break
                    self._advance()
                else:
                    raise LexError("unterminated block comment", loc)
                continue

            break

    # -- scanning helpers --

    def _scan_nat_lit(self, loc: Loc) -> Token:
        """Scan a decimal or hex nat literal. Cursor is on first digit or '0'."""
        if self._peek_str(2).lower() == '0x':
            self._advance(); self._advance()  # 0x
            start = self.pos
            # Use frozenset: '' in 'abc' is True (substring), '' in frozenset is False
            while self._peek() in HEX_CHARS:
                self._advance()
            hex_str = self.src[start:self.pos]
            if not hex_str:
                raise LexError("empty hex literal", loc)
            return Token(KIND_NAT, int(hex_str, 16), loc)
        else:
            start = self.pos
            while self._peek().isdigit():
                self._advance()
            return Token(KIND_NAT, int(self.src[start:self.pos]), loc)

    def _scan_text_lit(self, loc: Loc) -> Token:
        """Scan a double-quoted text literal. Cursor is past the opening '"'."""
        result = []
        while not self._at_end():
            ch = self._peek()
            if ch == '"':
                self._advance()
                return Token(KIND_TEXT, ''.join(result), loc)
            if ch == '\\':
                self._advance()
                esc = self._advance()
                result.append(self._unescape(esc, loc))
                continue
            if ch == '#' and self._peek(1) == '{':
                # Interpolation: #{...} — scan raw fragment, store as marker
                # For the bootstrap, we store interpolation as a special tuple
                # ('interp', raw_inner_text) and the parser handles it.
                self._advance(); self._advance()  # #{
                depth = 1
                inner = []
                while not self._at_end() and depth > 0:
                    c = self._peek()
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            self._advance()
                            break
                    inner.append(self._advance())
                result.append(('interp', ''.join(inner)))
                continue
            result.append(self._advance())
        raise LexError("unterminated text literal", loc)

    def _scan_raw_text(self, loc: Loc) -> Token:
        """Scan r"..." raw text literal. Cursor is past 'r"'."""
        result = []
        while not self._at_end():
            ch = self._peek()
            if ch == '"':
                self._advance()
                return Token(KIND_RAWTEXT, ''.join(result), loc)
            result.append(self._advance())
        raise LexError("unterminated raw text literal", loc)

    def _scan_bytes_lit(self, loc: Loc) -> Token:
        """Scan b"..." byte literal. Cursor is past 'b"'."""
        result = []
        while not self._at_end():
            ch = self._peek()
            if ch == '"':
                self._advance()
                return Token(KIND_BYTES, bytes(result), loc)
            if ch == '\\':
                self._advance()
                esc = self._advance()
                c = self._unescape(esc, loc)
                result.append(ord(c))
                continue
            result.append(ord(self._advance()))
        raise LexError("unterminated byte literal", loc)

    def _scan_hex_bytes(self, loc: Loc) -> Token:
        """Scan x"..." hex byte literal. Cursor is past 'x"'."""
        result = []
        while not self._at_end():
            # skip whitespace within hex literal
            while self._peek() in ' \t\n\r':
                self._advance()
            ch = self._peek()
            if ch == '"':
                self._advance()
                return Token(KIND_HEXBYTES, bytes(result), loc)
            h1 = self._advance()
            h2 = self._advance()
            if h1 not in '0123456789abcdefABCDEF' or h2 not in '0123456789abcdefABCDEF':
                raise LexError(f"invalid hex byte pair {h1!r}{h2!r}", loc)
            result.append(int(h1 + h2, 16))
        raise LexError("unterminated hex byte literal", loc)

    def _unescape(self, esc: str, loc: Loc) -> str:
        table = {'n': '\n', 'r': '\r', 't': '\t', '\\': '\\', '"': '"', '0': '\0'}
        if esc in table:
            return table[esc]
        if esc == 'x':
            h1 = self._advance()
            h2 = self._advance()
            if h1 not in HEX_CHARS or h2 not in HEX_CHARS:
                raise LexError(f"invalid hex escape \\x{h1}{h2}", loc)
            return chr(int(h1 + h2, 16))
        raise LexError(f"unknown escape sequence \\{esc}", loc)

    def _scan_name(self, loc: Loc) -> Token:
        """
        Scan an identifier or keyword.
        Handles:  SnakeName, PascalName, TypeVar, RowVar, keywords.
        Also handles post-normalization: λ, ∀, ∃ as keywords.
        Caller has NOT yet consumed the first char.
        """
        start = self.pos
        ch = self._peek()

        # Scan full name
        while not self._at_end() and (self._peek().isalnum() or self._peek() == '_'):
            self._advance()
        name = self.src[start:self.pos]

        # --- Unicode normalization for word-form ASCII (§1.2) ---
        if name == 'fn':
            name = 'λ'
        elif name == 'forall':
            name = '∀'
        elif name == 'exists':
            name = '∃'

        # Classify
        if name in KEYWORDS:
            return Token(KIND_KEYWORD, name, loc)

        # Single-char type variable a-q
        if len(name) == 1 and 'a' <= name <= 'q':
            return Token(KIND_TYPEVAR, name, loc)

        # Single-char row variable r-z
        if len(name) == 1 and 'r' <= name <= 'z':
            return Token(KIND_ROWVAR, name, loc)

        # PascalCase
        if name[0].isupper():
            return Token(KIND_PASCAL, name, loc)

        # snake_case (starts with lower or _)
        return Token(KIND_SNAKE, name, loc)

    def _scan_unicode_name(self, loc: Loc) -> Token:
        """Scan a unicode identifier character (λ, ∀, ∃) as a keyword."""
        ch = self._advance()
        if ch in ('λ', '∀', '∃'):
            return Token(KIND_KEYWORD, ch, loc)
        raise LexError(f"unexpected unicode character {ch!r}", loc)

    # -- main scan loop --

    def lex(self) -> list[Token]:
        """Scan the entire source and return the token list."""
        tokens = []
        while True:
            self._skip_whitespace_and_comments()
            if self._at_end():
                tokens.append(Token(KIND_EOF, None, self._loc()))
                break
            tok = self._scan_one()
            tokens.append(tok)
        return tokens

    def _scan_one(self) -> Token:
        loc = self._loc()
        ch = self._peek()

        # --- Literals: prefix-dispatched ---

        # Nat literals
        if ch.isdigit():
            return self._scan_nat_lit(loc)

        # Text literals: "...", r"...", b"...", x"..."
        if ch == '"':
            self._advance()
            return self._scan_text_lit(loc)
        if ch == 'r' and self._peek(1) == '"':
            self._advance(); self._advance()
            return self._scan_raw_text(loc)
        if ch == 'b' and self._peek(1) == '"':
            self._advance(); self._advance()
            return self._scan_bytes_lit(loc)
        if ch == 'x' and self._peek(1) == '"':
            self._advance(); self._advance()
            return self._scan_hex_bytes(loc)

        # pin#hex literal (Glass IR only)
        if self._peek_str(4) == 'pin#':
            self._advance(); self._advance(); self._advance(); self._advance()
            start = self.pos
            while self._peek() in HEX_CHARS:
                self._advance()
            return Token(KIND_PINLIT, self.src[start:self.pos], loc)

        # --- Identifiers and keywords ---
        if ch.isalpha() or ch == '_':
            return self._scan_name(loc)

        # Unicode operators and keywords
        UNICODE_KW  = frozenset('λ∀∃')
        UNICODE_OPS = frozenset('←→·⊕⊗⊤⊥∅≠≤≥∈∉⊆÷¬')
        if ch in UNICODE_KW:
            self._advance()
            return Token(KIND_KEYWORD, ch, loc)
        if ch in UNICODE_OPS:
            self._advance()
            return Token(KIND_OP, ch, loc)

        # --- ASCII operator normalization (§1.2) ---

        # -> → Arrow
        if self._peek_str(2) == '->':
            self._advance(); self._advance()
            return Token(KIND_OP, '→', loc)

        # <- ← Bind
        if self._peek_str(2) == '<-':
            self._advance(); self._advance()
            return Token(KIND_OP, '←', loc)

        # /= ≠ NEq
        if self._peek_str(2) == '/=':
            self._advance(); self._advance()
            return Token(KIND_OP, '≠', loc)

        # <= ≤ LEq  (must check before lone < or =)
        if self._peek_str(2) == '<=' and self._peek(2) != '-':
            self._advance(); self._advance()
            return Token(KIND_OP, '≤', loc)

        # >= ≥ GEq
        if self._peek_str(2) == '>=':
            self._advance(); self._advance()
            return Token(KIND_OP, '≥', loc)

        # // → / (IntDiv)  — must check BEFORE lone /
        if self._peek_str(2) == '//':
            self._advance(); self._advance()
            return Token(KIND_OP, '/', loc)

        # / → ÷ (TrueDiv)
        if ch == '/':
            self._advance()
            return Token(KIND_OP, '÷', loc)

        # --- Multi-character operators (before punctuation, longest match) ---
        for op in OP_MULTI:
            if self._peek_str(len(op)) == op:
                for _ in op:
                    self._advance()
                return Token(KIND_OP, op, loc)

        # --- Multi-character punctuation (longest match) ---
        for punc in PUNCT_MULTI:
            if self._peek_str(len(punc)) == punc:
                for _ in punc:
                    self._advance()
                return Token(KIND_PUNCT, punc, loc)

        # --- Single-char operators ---
        SINGLE_OPS = set('+-*^<>')
        if ch in SINGLE_OPS:
            self._advance()
            return Token(KIND_OP, ch, loc)

        # --- Punctuation ---
        if ch in PUNCT_SINGLE:
            self._advance()
            return Token(KIND_PUNCT, ch, loc)

        # `  backtick (operator section delimiter)
        if ch == '`':
            self._advance()
            return Token(KIND_PUNCT, '`', loc)

        # Unrecognized
        raise LexError(f"unexpected character {ch!r} (U+{ord(ch):04X})", loc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lex(source: str | bytes, filename: str = '<stdin>') -> list[Token]:
    """
    Lex a Gallowglass source string.

    Args:
        source:   Source text (str or UTF-8 bytes).
        filename: Used in error messages and token locations.

    Returns:
        List of Token, always ending with TEOF.

    Raises:
        LexError on any lexical error.
    """
    return Lexer(source, filename).lex()
