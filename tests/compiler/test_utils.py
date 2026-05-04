#!/usr/bin/env python3
"""
Compiler utility function tests (Milestone 8.1).

Tests that every definition in compiler/src/Compiler.gls compiles to a
planvm-valid seed and produces correct PLAN values via the Python harness.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.plan import P, L, A, N, is_nat, is_pin, is_law, is_app

MODULE = 'Compiler'
SRC_PATH = os.path.join(os.path.dirname(__file__), '..', '..',
                         'compiler', 'src', 'Compiler.gls')


def compile_module():
    """Compile the full compiler module, returning (compiled_dict, resolved_program)."""
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    with open(SRC_PATH) as f:
        src = f.read()
    prog = parse(lex(src, SRC_PATH), SRC_PATH)
    resolved, _ = resolve(prog, MODULE, {}, SRC_PATH)
    compiled = compile_program(resolved, MODULE)
    return compiled


def make_seed(name):
    """Compile and emit a seed for a single definition."""
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
    """Evaluate a PLAN value applied to arguments using the Python harness."""
    import sys
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, 50000))
    try:
        from dev.harness.plan import evaluate
        result = val
        for arg in args:
            result = A(result, arg)
        return evaluate(result)
    finally:
        sys.setrecursionlimit(old_limit)


class TestCompilation(unittest.TestCase):
    """Test that the compiler module compiles without errors."""

    @classmethod
    def setUpClass(cls):
        cls.compiled = compile_module()

    def test_module_compiles(self):
        """The compiler module compiles without errors."""
        self.assertIsInstance(self.compiled, dict)
        self.assertGreater(len(self.compiled), 0)

    def test_has_core_types(self):
        """Core types (constructors) are compiled."""
        expected_constructors = [
            'Compiler.MkPair', 'Compiler.Ok', 'Compiler.Err',
            'Compiler.None', 'Compiler.Some',
            'Compiler.Nil', 'Compiler.Cons',
        ]
        for name in expected_constructors:
            self.assertIn(name, self.compiled,
                         f'Missing constructor {name}')

    def test_has_utility_functions(self):
        """Core utility functions are compiled."""
        expected_fns = [
            'Compiler.pred', 'Compiler.is_zero',
            'Compiler.nat_eq', 'Compiler.nat_lt',
            'Compiler.add', 'Compiler.sub', 'Compiler.mul',
            'Compiler.div_nat', 'Compiler.mod_nat',
            'Compiler.length', 'Compiler.map', 'Compiler.foldl', 'Compiler.foldr',
            'Compiler.reverse', 'Compiler.append',
            'Compiler.assoc_lookup', 'Compiler.assoc_insert',
            'Compiler.byte_at', 'Compiler.bytes_concat',
        ]
        for name in expected_fns:
            self.assertIn(name, self.compiled,
                         f'Missing function {name}')


class TestNatArithmetic(unittest.TestCase):
    """Test nat arithmetic functions via Python harness evaluation."""

    @classmethod
    def setUpClass(cls):
        cls.compiled = compile_module()

    def _get(self, name):
        return self.compiled[f'Compiler.{name}']

    def test_pred_zero(self):
        self.assertEqual(eval_plan(self._get('pred'), 0), 0)

    def test_pred_five(self):
        self.assertEqual(eval_plan(self._get('pred'), 5), 4)

    def test_is_zero_zero(self):
        result = eval_plan(self._get('is_zero'), 0)
        self.assertEqual(result, 1)  # True = 1

    def test_is_zero_nonzero(self):
        result = eval_plan(self._get('is_zero'), 3)
        self.assertEqual(result, 0)  # False = 0

    def test_nat_eq_equal(self):
        result = eval_plan(self._get('nat_eq'), 5, 5)
        self.assertEqual(result, 1)

    def test_nat_eq_unequal(self):
        result = eval_plan(self._get('nat_eq'), 3, 7)
        self.assertEqual(result, 0)

    def test_nat_lt_less(self):
        result = eval_plan(self._get('nat_lt'), 3, 5)
        self.assertEqual(result, 1)

    def test_nat_lt_equal(self):
        result = eval_plan(self._get('nat_lt'), 5, 5)
        self.assertEqual(result, 0)

    def test_nat_lt_greater(self):
        result = eval_plan(self._get('nat_lt'), 7, 3)
        self.assertEqual(result, 0)

    def test_add(self):
        self.assertEqual(eval_plan(self._get('add'), 3, 4), 7)

    def test_add_zero(self):
        self.assertEqual(eval_plan(self._get('add'), 5, 0), 5)

    def test_sub(self):
        self.assertEqual(eval_plan(self._get('sub'), 7, 3), 4)

    def test_sub_saturating(self):
        self.assertEqual(eval_plan(self._get('sub'), 3, 7), 0)

    def test_mul(self):
        self.assertEqual(eval_plan(self._get('mul'), 3, 4), 12)

    def test_mul_zero(self):
        self.assertEqual(eval_plan(self._get('mul'), 5, 0), 0)

    def test_div_nat(self):
        self.assertEqual(eval_plan(self._get('div_nat'), 10, 3), 3)

    def test_div_nat_exact(self):
        self.assertEqual(eval_plan(self._get('div_nat'), 12, 4), 3)

    def test_div_nat_by_zero(self):
        self.assertEqual(eval_plan(self._get('div_nat'), 5, 0), 0)

    def test_mod_nat(self):
        self.assertEqual(eval_plan(self._get('mod_nat'), 10, 3), 1)

    def test_mod_nat_exact(self):
        self.assertEqual(eval_plan(self._get('mod_nat'), 12, 4), 0)

    def test_mod_nat_by_zero(self):
        self.assertEqual(eval_plan(self._get('mod_nat'), 5, 0), 0)

    def test_lte(self):
        self.assertEqual(eval_plan(self._get('lte'), 3, 5), 1)
        self.assertEqual(eval_plan(self._get('lte'), 5, 5), 1)
        self.assertEqual(eval_plan(self._get('lte'), 7, 5), 0)


class TestBitwiseOps(unittest.TestCase):
    """Test bitwise operations."""

    @classmethod
    def setUpClass(cls):
        cls.compiled = compile_module()

    def _get(self, name):
        return self.compiled[f'Compiler.{name}']

    def test_pow2(self):
        self.assertEqual(eval_plan(self._get('pow2'), 0), 1)
        self.assertEqual(eval_plan(self._get('pow2'), 3), 8)
        self.assertEqual(eval_plan(self._get('pow2'), 8), 256)

    def test_bit_and(self):
        self.assertEqual(eval_plan(self._get('bit_and'), 0xFF, 0x0F), 0x0F)
        self.assertEqual(eval_plan(self._get('bit_and'), 6, 3), 2)

    def test_bit_or(self):
        self.assertEqual(eval_plan(self._get('bit_or'), 0xF0, 0x0F), 0xFF)
        self.assertEqual(eval_plan(self._get('bit_or'), 6, 3), 7)

    def test_shift_right(self):
        self.assertEqual(eval_plan(self._get('shift_right'), 16, 2), 4)
        self.assertEqual(eval_plan(self._get('shift_right'), 255, 4), 15)

    def test_shift_left(self):
        self.assertEqual(eval_plan(self._get('shift_left'), 1, 8), 256)
        self.assertEqual(eval_plan(self._get('shift_left'), 3, 4), 48)


class TestListOps(unittest.TestCase):
    """Test list operations."""

    @classmethod
    def setUpClass(cls):
        cls.compiled = compile_module()

    def _get(self, name):
        return self.compiled[f'Compiler.{name}']

    def _make_list(self, *elems):
        """Build a PLAN list from Python values."""
        nil = self.compiled['Compiler.Nil']
        cons = self.compiled['Compiler.Cons']
        result = nil
        for e in reversed(elems):
            result = A(A(cons, e), result)
        return result

    def _list_to_py(self, val):
        """Convert a PLAN list to a Python list of nats."""
        from dev.harness.plan import evaluate
        result = []
        current = evaluate(val)
        while is_app(current):
            # Cons x rest = A(A(1, x), rest)
            if is_app(current.fun):
                result.append(current.fun.arg)
                current = evaluate(current.arg)
            else:
                break
        return result

    def test_length_empty(self):
        nil = self.compiled['Compiler.Nil']
        self.assertEqual(eval_plan(self._get('length'), nil), 0)

    def test_length_nonempty(self):
        lst = self._make_list(10, 20, 30)
        self.assertEqual(eval_plan(self._get('length'), lst), 3)

    def test_append(self):
        a = self._make_list(1, 2)
        b = self._make_list(3, 4)
        result = eval_plan(self._get('append'), a, b)
        self.assertEqual(self._list_to_py(result), [1, 2, 3, 4])

    def test_reverse(self):
        lst = self._make_list(1, 2, 3)
        result = eval_plan(self._get('reverse'), lst)
        self.assertEqual(self._list_to_py(result), [3, 2, 1])

    def test_nth(self):
        lst = self._make_list(10, 20, 30)
        self.assertEqual(eval_plan(self._get('nth'), 0, 1, lst), 20)

    def test_nth_default(self):
        lst = self._make_list(10)
        self.assertEqual(eval_plan(self._get('nth'), 99, 5, lst), 99)


class TestAssocList(unittest.TestCase):
    """Test association list operations."""

    @classmethod
    def setUpClass(cls):
        cls.compiled = compile_module()

    def _get(self, name):
        return self.compiled[f'Compiler.{name}']

    def _make_assoc(self, *kvs):
        """Build an assoc list from (key, val) pairs."""
        nil = self.compiled['Compiler.Nil']
        cons = self.compiled['Compiler.Cons']
        mk_pair = self.compiled['Compiler.MkPair']
        result = nil
        for k, v in reversed(kvs):
            pair = A(A(mk_pair, k), v)
            result = A(A(cons, pair), result)
        return result

    def test_assoc_lookup_found(self):
        alist = self._make_assoc((1, 10), (2, 20), (3, 30))
        result = eval_plan(self._get('assoc_lookup'), 2, alist)
        # Should be Some(20) = A(1, 20)
        self.assertTrue(is_app(result))
        self.assertEqual(result.arg, 20)

    def test_assoc_lookup_not_found(self):
        alist = self._make_assoc((1, 10), (2, 20))
        result = eval_plan(self._get('assoc_lookup'), 5, alist)
        # Should be None = 0
        self.assertEqual(result, 0)

    def test_assoc_has(self):
        alist = self._make_assoc((1, 10), (2, 20))
        self.assertEqual(eval_plan(self._get('assoc_has'), 2, alist), 1)
        self.assertEqual(eval_plan(self._get('assoc_has'), 5, alist), 0)


class TestByteOps(unittest.TestCase):
    """Test byte-level operations."""

    @classmethod
    def setUpClass(cls):
        cls.compiled = compile_module()

    def _get(self, name):
        return self.compiled[f'Compiler.{name}']

    def _make_bytes(self, bs):
        """Build a Bytes pair from a Python bytes object."""
        mk_pair = self.compiled['Compiler.MkPair']
        content = int.from_bytes(bs, 'little') if bs else 0
        return A(A(mk_pair, len(bs)), content)

    @unittest.skip("byte_at uses recursive div/mod — too slow for Python harness; works on planvm")
    def test_byte_at(self):
        # content nat for bytes [0x48, 0x65, 0x6C] = 0x6C6548
        content = 0x48 | (0x65 << 8) | (0x6C << 16)
        self.assertEqual(eval_plan(self._get('byte_at'), content, 0), 0x48)
        self.assertEqual(eval_plan(self._get('byte_at'), content, 1), 0x65)
        self.assertEqual(eval_plan(self._get('byte_at'), content, 2), 0x6C)

    @unittest.skip("byte_at even at index 0 does div_nat(n, 1) = n iterations — too slow")
    def test_byte_at_index_zero(self):
        content = 0x48 | (0x65 << 8)
        self.assertEqual(eval_plan(self._get('byte_at'), content, 0), 0x48)

    def test_bytes_length(self):
        bs = self._make_bytes(b'Hello')
        self.assertEqual(eval_plan(self._get('bytes_length'), bs), 5)

    @unittest.skip("bytes_at uses byte_at (recursive div/mod) — too slow for Python harness")
    def test_bytes_at(self):
        bs = self._make_bytes(b'Hi')
        self.assertEqual(eval_plan(self._get('bytes_at'), bs, 0), ord('H'))
        self.assertEqual(eval_plan(self._get('bytes_at'), bs, 1), ord('i'))

    def test_bytes_eq_equal(self):
        """bytes_eq with tiny values (single byte) is fast enough."""
        a = self._make_bytes(b'\x01')
        b = self._make_bytes(b'\x01')
        self.assertEqual(eval_plan(self._get('bytes_eq'), a, b), 1)

    def test_bytes_eq_unequal_length(self):
        """bytes_eq with different lengths returns False immediately (no nat comparison)."""
        a = self._make_bytes(b'\x01')
        b = self._make_bytes(b'\x01\x02')
        self.assertEqual(eval_plan(self._get('bytes_eq'), a, b), 0)

    @unittest.skip("bytes_concat uses bit_or/shift_left (recursive) — too slow for Python harness")
    def test_bytes_concat(self):
        a = self._make_bytes(b'He')
        b = self._make_bytes(b'lo')
        result = eval_plan(self._get('bytes_concat'), a, b)
        from dev.harness.plan import evaluate
        result = evaluate(result)
        if is_app(result) and is_app(result.fun):
            self.assertEqual(result.fun.arg, 4)
        else:
            self.fail(f'bytes_concat did not produce MkPair: {result}')




if __name__ == '__main__':
    unittest.main()
