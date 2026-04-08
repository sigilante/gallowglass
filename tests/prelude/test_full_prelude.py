#!/usr/bin/env python3
"""
Full prelude integration test — compile all 8 Core modules together.

Validates that the entire prelude compiles as a single dependency graph
via build_modules, with correct topological ordering and cross-module
resolution.

Run: python3 -m pytest tests/prelude/test_full_prelude.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.bplan import bevaluate, register_prelude_jets, _bapply
from dev.harness.plan import A, N, is_app

CORE_DIR = os.path.join(os.path.dirname(__file__), '..', '..',
                        'prelude', 'src', 'Core')

MODULES = [
    'Core.Combinators',
    'Core.Nat',
    'Core.Bool',
    'Core.Text',
    'Core.Pair',
    'Core.Option',
    'Core.List',
    'Core.Result',
]

_COMPILED = None


def _build_full_prelude():
    global _COMPILED
    if _COMPILED is not None:
        return _COMPILED
    from bootstrap.build import build_modules
    sources = []
    for mod in MODULES:
        short = mod.split('.')[-1]
        path = os.path.join(CORE_DIR, f'{short}.gls')
        with open(path) as f:
            sources.append((mod, f.read()))
    _COMPILED = build_modules(sources)
    register_prelude_jets(_COMPILED)
    return _COMPILED


def mk_text(s: str):
    b = s.encode('utf-8')
    content = int.from_bytes(b, 'little') if b else 0
    return A(N(len(b)), N(content))


def check_text(v, expected: str) -> bool:
    b = expected.encode('utf-8')
    content = int.from_bytes(b, 'little') if b else 0
    return is_app(v) and v.fun == len(b) and v.arg == content


def mk_nil():
    return N(0)


def mk_list(*elems):
    result = mk_nil()
    for e in reversed(elems):
        result = A(A(N(1), N(e)), result)
    return result


class TestFullPrelude(unittest.TestCase):
    """Compile all 8 Core modules together and verify cross-module functions."""

    @classmethod
    def setUpClass(cls):
        cls.c = _build_full_prelude()

    def fn(self, fq):
        self.assertIn(fq, self.c, f"'{fq}' not compiled")
        return self.c[fq]

    # --- Compilation ---

    def test_all_modules_present(self):
        """Every module contributes at least one binding."""
        for mod in MODULES:
            found = any(k.startswith(mod + '.') for k in self.c)
            self.assertTrue(found, f"no bindings from {mod}")

    def test_binding_count(self):
        """Full prelude has >100 compiled bindings."""
        self.assertGreater(len(self.c), 100)

    # --- Cross-module: Combinators ---

    def test_id(self):
        result = bevaluate(_bapply(self.fn('Core.Combinators.id'), N(42)))
        self.assertEqual(result, 42)

    def test_pipe(self):
        inc = self.fn('Core.PLAN.inc')
        pipe = self.fn('Core.Combinators.pipe')
        result = bevaluate(_bapply(_bapply(pipe, N(5)), inc))
        self.assertEqual(result, 6)

    # --- Cross-module: Eq ---

    def test_nat_eq(self):
        eq = self.fn('Core.Nat.inst_Eq_Nat_eq')
        self.assertEqual(bevaluate(_bapply(_bapply(eq, N(3)), N(3))), 1)
        self.assertEqual(bevaluate(_bapply(_bapply(eq, N(3)), N(4))), 0)

    def test_bool_eq(self):
        eq = self.fn('Core.Bool.inst_Eq_Bool_eq')
        self.assertEqual(bevaluate(_bapply(_bapply(eq, N(1)), N(1))), 1)
        self.assertEqual(bevaluate(_bapply(_bapply(eq, N(1)), N(0))), 0)

    # --- Cross-module: Show ---

    def test_show_nat(self):
        show = self.fn('Core.Text.inst_Show_Nat_show')
        result = bevaluate(_bapply(show, N(42)))
        self.assertTrue(check_text(result, '42'))

    def test_show_bool_true(self):
        show = self.fn('Core.Text.inst_Show_Bool_show')
        result = bevaluate(_bapply(show, N(1)))
        self.assertTrue(check_text(result, 'True'))

    def test_show_list(self):
        show_list = self.fn('Core.List.inst_Show_List_show')
        show_nat = self.fn('Core.Text.inst_Show_Nat_show')
        result = bevaluate(_bapply(_bapply(show_list, show_nat), mk_list(1, 2, 3)))
        self.assertTrue(check_text(result, '[1,2,3]'))

    def test_show_option_some(self):
        show_opt = self.fn('Core.Option.inst_Show_Option_show')
        show_nat = self.fn('Core.Text.inst_Show_Nat_show')
        some_5 = A(N(1), N(5))  # Some 5
        result = bevaluate(_bapply(_bapply(show_opt, show_nat), some_5))
        self.assertTrue(check_text(result, 'Some(5)'))

    # --- Cross-module: Pair ---

    def test_fst_snd(self):
        fst = self.fn('Core.Pair.fst')
        snd = self.fn('Core.Pair.snd')
        pair = A(A(N(0), N(10)), N(20))  # MkPair 10 20
        self.assertEqual(bevaluate(_bapply(fst, pair)), 10)
        self.assertEqual(bevaluate(_bapply(snd, pair)), 20)

    # --- Cross-module: Result ---

    def test_is_ok(self):
        is_ok = self.fn('Core.Result.is_ok')
        ok_v = A(N(0), N(42))   # Ok 42
        err_v = A(N(1), N(99))  # Err 99
        self.assertEqual(bevaluate(_bapply(is_ok, ok_v)), 1)
        self.assertEqual(bevaluate(_bapply(is_ok, err_v)), 0)

    # --- Cross-module: List operations ---

    def test_map(self):
        map_fn = self.fn('Core.List.map')
        inc = self.fn('Core.PLAN.inc')
        result = bevaluate(_bapply(_bapply(map_fn, inc), mk_list(1, 2, 3)))
        expected = mk_list(2, 3, 4)
        # Compare via Eq List
        eq_list = self.fn('Core.List.inst_Eq_List_eq')
        nat_eq = self.fn('Core.Nat.inst_Eq_Nat_eq')
        nat_neq = self.fn('Core.Nat.inst_Eq_Nat_neq')
        eq_result = bevaluate(
            _bapply(_bapply(_bapply(_bapply(eq_list, nat_eq), nat_neq), result), expected)
        )
        self.assertEqual(eq_result, 1)

    def test_foldl(self):
        foldl = self.fn('Core.List.foldl')
        add = self.fn('Core.Nat.add')
        result = bevaluate(
            _bapply(_bapply(_bapply(foldl, add), N(0)), mk_list(1, 2, 3, 4))
        )
        self.assertEqual(result, 10)


if __name__ == '__main__':
    unittest.main()
