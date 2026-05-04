#!/usr/bin/env python3
"""
M8.6 Plan Assembler emitter tests (compiler/src/Compiler.gls Section 25).

Functions under test:
  nat_to_decimal_go, nat_to_decimal,
  emit_debruijn_ref,
  emit_asm_app, emit_asm_let, emit_asm_pin,
  emit_law_sig_go, emit_law_sig,
  emit_body_val,
  emit_plaw, emit_pval,
  emit_bind, emit_program

Evaluation strategy
-------------------
Bytes in Gallowglass is Pair Nat Nat = MkPair(length, content_nat) where content
is a little-endian nat.  bytes_concat computes:

    new_content = bit_or(a_content, shift_left(b_content, a_len * 8))

Under the pure PLAN harness, bit_or and shift_left use O(n) recursive arithmetic.
All tests that produce multi-byte output previously exceeded the Python recursion
limit and were skipped.

The BPLAN harness (dev/harness/bplan.py) registers native Python jets for all
O(n) arithmetic laws (add, mul, bit_or, shift_left, …).  With jets active,
bytes_concat is O(1), eliminating the recursion-depth limit for all emitter tests.

Tests that only check compilation presence (TestEmitterCompilation) use the pure
harness — they don't evaluate, so the BPLAN harness is not needed.  All evaluation
tests use the BPLAN harness via compile_module_bplan() / eval_bplan().
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.plan import A, evaluate

MODULE = 'Compiler'
SRC_PATH = os.path.join(os.path.dirname(__file__), '..', '..',
                        'compiler', 'src', 'Compiler.gls')

# Module-level caches — Compiler.gls is ~3800 lines; compile once per process.
_COMPILED = None
_COMPILED_BPLAN = None


def compile_module():
    global _COMPILED
    if _COMPILED is not None:
        return _COMPILED
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    with open(SRC_PATH) as f:
        src = f.read()
    prog = parse(lex(src, SRC_PATH), SRC_PATH)
    resolved, _ = resolve(prog, MODULE, {}, SRC_PATH)
    _COMPILED = compile_program(resolved, MODULE)
    return _COMPILED


def compile_module_bplan():
    """Return the compiled module with BPLAN jet registry active."""
    global _COMPILED_BPLAN
    if _COMPILED_BPLAN is not None:
        return _COMPILED_BPLAN
    from dev.harness.bplan import register_jets
    compiled = compile_module()  # jets work by object identity; reuse same dict
    register_jets(compiled)
    _COMPILED_BPLAN = compiled
    return _COMPILED_BPLAN


def make_seed(name):
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    from bootstrap.emit_seed import emit
    with open(SRC_PATH) as f:
        src = f.read()
    prog = parse(lex(src, SRC_PATH), SRC_PATH)
    resolved, _ = resolve(prog, MODULE, {}, SRC_PATH)
    compiled = compile_program(resolved, MODULE)
    return emit(compiled, f'{MODULE}.{name}')


def eval_plan(val, *args):
    """Evaluate using pure PLAN harness (no jets)."""
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 50000))
    try:
        result = val
        for arg in args:
            result = A(result, arg)
        return evaluate(result)
    finally:
        sys.setrecursionlimit(old)


def eval_bplan(val, *args):
    """Evaluate using BPLAN harness (arithmetic jets active)."""
    from dev.harness.bplan import bevaluate
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 100000))
    try:
        result = val
        for arg in args:
            result = A(result, arg)
        return bevaluate(result)
    finally:
        sys.setrecursionlimit(old)


def check_bytes(compiled, result, expected_str):
    """
    Assert that a PLAN Bytes value (MkPair len content) encodes expected_str.
    Uses bytes_length and bytes_content — both O(1) field extractions.
    """
    expected = expected_str.encode('utf-8')
    length = eval_plan(compiled['Compiler.bytes_length'], result)
    content = eval_plan(compiled['Compiler.bytes_content'], result)
    assert length == len(expected), \
        f'Length mismatch: got {length}, expected {len(expected)} for {expected_str!r}'
    if expected:
        expected_nat = int.from_bytes(expected, 'little')
        assert content == expected_nat, \
            f'Content mismatch for {expected_str!r}: got {hex(content)}, expected {hex(expected_nat)}'


# ---------------------------------------------------------------------------
# Compilation tests (pure harness — no evaluation needed)
# ---------------------------------------------------------------------------

class TestEmitterCompilation(unittest.TestCase):
    """All M8.6 functions compile without errors."""

    @classmethod
    def setUpClass(cls):
        cls.c = compile_module()

    def test_nat_to_decimal(self):
        self.assertIn('Compiler.nat_to_decimal_go', self.c)
        self.assertIn('Compiler.nat_to_decimal', self.c)

    def test_emit_helpers(self):
        for name in ('emit_debruijn_ref', 'emit_asm_app', 'emit_asm_let',
                     'emit_asm_pin', 'emit_law_sig_go', 'emit_law_sig'):
            with self.subTest(name=name):
                self.assertIn(f'Compiler.{name}', self.c)

    def test_emit_body_val(self):
        self.assertIn('Compiler.emit_body_val', self.c)

    def test_emit_plaw(self):
        self.assertIn('Compiler.emit_plaw', self.c)

    def test_emit_pval(self):
        self.assertIn('Compiler.emit_pval', self.c)

    def test_emit_bind_and_program(self):
        self.assertIn('Compiler.emit_bind', self.c)
        self.assertIn('Compiler.emit_program', self.c)


# ---------------------------------------------------------------------------
# nat_to_decimal (BPLAN harness)
# ---------------------------------------------------------------------------

class TestNatToDecimal(unittest.TestCase):
    """nat_to_decimal correctness — all cases, with BPLAN jets."""

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module_bplan()

    def _check(self, n, expected):
        result = eval_bplan(self.bc['Compiler.nat_to_decimal'], n)
        check_bytes(self.bc, result, expected)

    def test_zero(self):
        self._check(0, '0')

    def test_one(self):
        self._check(1, '1')

    def test_five(self):
        self._check(5, '5')

    def test_nine(self):
        self._check(9, '9')

    def test_ten(self):
        self._check(10, '10')

    def test_42(self):
        self._check(42, '42')

    def test_99(self):
        self._check(99, '99')

    def test_three_digit(self):
        self._check(123, '123')


# ---------------------------------------------------------------------------
# emit_debruijn_ref (BPLAN harness)
# ---------------------------------------------------------------------------

class TestEmitDebruijnRef(unittest.TestCase):
    """
    emit_debruijn_ref i → "_i"

    Output is always ≥ 2 bytes ("_" + decimal(i)).
    Requires BPLAN jets for bytes_concat arithmetic.
    """

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module_bplan()

    def _check(self, i, expected):
        result = eval_bplan(self.bc['Compiler.emit_debruijn_ref'], i)
        check_bytes(self.bc, result, expected)

    def test_ref_0(self):
        self._check(0, '_0')

    def test_ref_1(self):
        self._check(1, '_1')

    def test_ref_3(self):
        self._check(3, '_3')

    def test_ref_9(self):
        self._check(9, '_9')

    def test_ref_10_plus(self):
        self._check(42, '_42')


# ---------------------------------------------------------------------------
# emit_body_val (BPLAN harness)
# ---------------------------------------------------------------------------

class TestEmitBodyVal(unittest.TestCase):
    """
    emit_body_val dispatch — pattern table:

      PNat i                     → "_i"          (de Bruijn ref)
      PApp(PNat 0)(PNat k)       → "k"           (cg_quote_nat constant)
      PApp(PApp(PNat 0) x2) x    → "(x2 x)"      (cg_bapp application)
      PApp(PApp(PNat 1) rhs) body→ "_d(rhs)\n  body"  (let-chain, d=depth+1)
      PPin v                     → "(#pin ep(v))" (embedded pin, ep=emit_pval)

    All cases require BPLAN jets (multi-byte output).
    """

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module_bplan()

    def _pnat(self, n):
        return eval_bplan(self.bc['Compiler.PNat'], n)

    def _papp(self, f, x):
        return eval_bplan(self.bc['Compiler.PApp'], f, x)

    def _ppin(self, v):
        return eval_bplan(self.bc['Compiler.PPin'], v)

    def _check(self, pval, depth, expected):
        ep = self.bc['Compiler.emit_pval']
        result = eval_bplan(self.bc['Compiler.emit_body_val'], ep, pval, depth)
        check_bytes(self.bc, result, expected)

    # PNat i → "_i" (de Bruijn reference; depth is irrelevant)

    def test_pnat_self_ref(self):
        """PNat 0 → "_0" (law self-reference slot)"""
        self._check(self._pnat(0), 3, '_0')

    def test_pnat_arg_ref(self):
        """PNat 1 → "_1" (first argument slot)"""
        self._check(self._pnat(1), 3, '_1')

    def test_pnat_let_slot(self):
        """PNat 5 → "_5" (let-binding slot)"""
        self._check(self._pnat(5), 3, '_5')

    def test_pnat_depth_irrelevant(self):
        """PNat 2 → "_2" regardless of depth parameter"""
        self._check(self._pnat(2), 10, '_2')

    # PApp(PNat 0)(PNat k) → "k"  [cg_quote_nat: quoted constant]
    # These previously failed because emit_bval_dispatch uses if-then-else
    # (eager Case_ dispatch) — the else branches evaluated bytes_concat on
    # 2-byte output even when the true branch returned "k" (1 byte).
    # With arithmetic jets the else branch is O(1) and no longer hangs.

    def test_quote_nat_single_digit(self):
        """PApp(PNat 0)(PNat 5) → "5" (quoted constant, 1 byte)"""
        pval = self._papp(self._pnat(0), self._pnat(5))
        self._check(pval, 1, '5')

    def test_quote_nat_zero(self):
        """PApp(PNat 0)(PNat 0) → "0" (quoted zero, 1 byte)"""
        pval = self._papp(self._pnat(0), self._pnat(0))
        self._check(pval, 1, '0')

    # PApp(PApp(PNat 0) x2) x → "(x2 x)"  [cg_bapp: application]

    def test_cg_bapp(self):
        """PApp(PApp(PNat 0)(PNat 1))(PNat 2) → "(_1 _2)" (application)"""
        # f = PApp(PNat 0)(PNat 1), x = PNat 2
        f = self._papp(self._pnat(0), self._pnat(1))
        pval = self._papp(f, self._pnat(2))
        self._check(pval, 3, '(_1 _2)')

    # PApp(PApp(PNat 1) rhs) body → "_slot(rhs)\n  body"  [let-chain]

    def test_let_chain(self):
        """PApp(PApp(PNat 1)(PNat 3))(PNat 4) at depth=2 → "_3(_3)\n  _4" """
        # opcode=1, x2=PNat 3, x=PNat 4, depth=2 → slot=add 2 1=3
        # emit_asm_let 3 (ebv (PNat 3) 2) (ebv (PNat 4) 3)
        # = emit_debruijn_ref 3 + "(" + "_3" + ")" + "\n" + "  " + "_4"
        # = "_3(_3)\n  _4"
        f = self._papp(self._pnat(1), self._pnat(3))
        pval = self._papp(f, self._pnat(4))
        self._check(pval, 2, '_3(_3)\n  _4')

    # PPin v → "(#pin ep(v))"  [embedded pin, emit_pval handles v]

    def test_ppin_in_body(self):
        """PPin(PNat 5) → "(#pin 5)" via emit_pval"""
        pval = self._ppin(self._pnat(5))
        self._check(pval, 1, '(#pin 5)')


# ---------------------------------------------------------------------------
# emit_pval (BPLAN harness)
# ---------------------------------------------------------------------------

class TestEmitPval(unittest.TestCase):
    """
    emit_pval top-level emitter.

      PNat n    → nat_to_decimal n
      PApp f x  → "(ep(f) ep(x))"
      PLaw n ap → "(#law \"n\" sig\n  body)"
      PPin v    → "(#pin ep(v))"

    All cases require BPLAN jets.
    """

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module_bplan()

    def _pnat(self, n):
        return eval_bplan(self.bc['Compiler.PNat'], n)

    def _papp(self, f, x):
        return eval_bplan(self.bc['Compiler.PApp'], f, x)

    def _plaw(self, name_nat, arity, body_pval):
        # PLaw name_nat (MkPair arity body_pval)
        pair = eval_bplan(self.bc['Compiler.MkPair'], arity, body_pval)
        return eval_bplan(self.bc['Compiler.PLaw'], name_nat, pair)

    def _ppin(self, v):
        return eval_bplan(self.bc['Compiler.PPin'], v)

    def _check(self, pval, expected):
        result = eval_bplan(self.bc['Compiler.emit_pval'], pval)
        check_bytes(self.bc, result, expected)

    def test_pnat_zero(self):
        """emit_pval (PNat 0) → '0'"""
        self._check(self._pnat(0), '0')

    def test_pnat_five(self):
        """emit_pval (PNat 5) → '5'"""
        self._check(self._pnat(5), '5')

    def test_pnat_nine(self):
        """emit_pval (PNat 9) → '9'"""
        self._check(self._pnat(9), '9')

    def test_plaw(self):
        """emit_pval (PLaw 0 (MkPair 1 (PNat 1))) → '(#law \"0\" (_0 _1)\n  _1)'

        Identity law: name=0, arity=1, body=PNat(1) (first argument).
        sig = emit_law_sig 1 = "(_0 _1)"
        body_asm = emit_body_val ep (PNat 1) 1 = emit_debruijn_ref 1 = "_1"
        Result: '(#law "0" (_0 _1)\n  _1)'
        """
        pval = self._plaw(0, 1, self._pnat(1))
        self._check(pval, '(#law "0" (_0 _1)\n  _1)')

    def test_papp(self):
        """emit_pval (PApp (PNat 0) (PNat 5)) → '(0 5)'"""
        pval = self._papp(self._pnat(0), self._pnat(5))
        self._check(pval, '(0 5)')

    def test_ppin(self):
        """emit_pval (PPin (PNat 3)) → '(#pin 3)'"""
        pval = self._ppin(self._pnat(3))
        self._check(pval, '(#pin 3)')


# ---------------------------------------------------------------------------
# emit_bind, emit_program (BPLAN harness)
# ---------------------------------------------------------------------------

class TestEmitBindAndProgram(unittest.TestCase):
    """
    emit_bind and emit_program — correctness tests with BPLAN harness.

    emit_bind name pval  → '(#bind "name_decimal" expr_asm)\\n'
    emit_program defs    → concatenation of one bind form per definition
    """

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module_bplan()

    def _pnat(self, n):
        return eval_bplan(self.bc['Compiler.PNat'], n)

    def _mk_pair(self, a, b):
        return eval_bplan(self.bc['Compiler.MkPair'], a, b)

    def _cons(self, x, xs):
        return eval_bplan(self.bc['Compiler.Cons'], x, xs)

    def _nil(self):
        return eval_bplan(self.bc['Compiler.Nil'])

    def test_emit_bind_compiles(self):
        self.assertIn('Compiler.emit_bind', self.bc)

    def test_emit_program_compiles(self):
        self.assertIn('Compiler.emit_program', self.bc)

    def test_emit_bind_correctness(self):
        """emit_bind 0 (PNat 5) → '(#bind \"0\" 5)\\n'"""
        pval = self._pnat(5)
        result = eval_bplan(self.bc['Compiler.emit_bind'], 0, pval)
        check_bytes(self.bc, result, '(#bind "0" 5)\n')

    def test_emit_program_correctness(self):
        """emit_program [(0, PNat 5)] → '(#bind \"0\" 5)\\n'"""
        pval = self._pnat(5)
        pair = self._mk_pair(0, pval)
        defs = self._cons(pair, self._nil())
        result = eval_bplan(self.bc['Compiler.emit_program'], defs)
        check_bytes(self.bc, result, '(#bind "0" 5)\n')



if __name__ == '__main__':
    unittest.main()
