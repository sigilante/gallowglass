#!/usr/bin/env python3
"""
GLS M12 effects tests — eff/handle/do/pure/run in Compiler.gls.

Verifies that the GLS self-hosting compiler:
  - Encodes effect keywords correctly (kw_eff, kw_handle, kw_pure, kw_run)
  - decl_is_eff predicate works on DEff values
  - CPS effect constants are present in compiled output
  - Can compile programs containing eff/handle/pure/run (via bootstrap)
  - Self-hosting invariant preserved after additions

Run: python3 -m pytest tests/compiler/test_m12_effects.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.plan import A, P, L, N, evaluate, is_nat, is_app, is_pin, is_law
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


def ev(val, *args):
    """Evaluate a GLS PLAN value applied to args using BPLAN jets."""
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 50000))
    try:
        result = val
        for a in args:
            result = A(result, a)
        return bevaluate(result)
    finally:
        sys.setrecursionlimit(old)


def nn(s: str) -> int:
    """Name nat: little-endian encoding of ASCII string."""
    return int.from_bytes(s.encode('ascii'), 'little')


def mk_nil():
    return 0

def mk_cons(x, xs):
    return A(A(1, x), xs)

def mk_list(*items):
    result = mk_nil()
    for item in reversed(items):
        result = mk_cons(item, result)
    return result

def mk_pair(a, b):
    return A(A(0, a), b)


# DEff tag = 5 (6th constructor: DLet=0, DType=1, DExt=2, DClass=3, DInst=4, DEff=5)
def mk_deff(eff_name, ops):
    return A(A(5, eff_name), ops)

def mk_dlet(name, body):
    return A(A(0, name), body)


# ---------------------------------------------------------------------------
# Test: keyword nats
# ---------------------------------------------------------------------------

class TestEffKeywords(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module()

    def test_kw_eff(self):
        expected = nn('eff')
        result = ev(self.bc['Compiler.kw_eff'])
        self.assertEqual(result, expected,
            f'kw_eff: got {result:#x}, expected {expected:#x}')

    def test_kw_handle(self):
        expected = nn('handle')
        result = ev(self.bc['Compiler.kw_handle'])
        self.assertEqual(result, expected)

    def test_kw_pure(self):
        expected = nn('pure')
        result = ev(self.bc['Compiler.kw_pure'])
        self.assertEqual(result, expected)

    def test_kw_run(self):
        expected = nn('run')
        result = ev(self.bc['Compiler.kw_run'])
        self.assertEqual(result, expected)


# ---------------------------------------------------------------------------
# Test: decl_is_eff predicate
# ---------------------------------------------------------------------------

class TestDeclIsEff(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module()

    def test_decl_is_eff_true(self):
        """decl_is_eff returns 1 for a DEff value."""
        d = mk_deff(nn('State'), mk_list(nn('get'), nn('put')))
        result = ev(self.bc['Compiler.decl_is_eff'], d)
        self.assertEqual(result, 1)

    def test_decl_is_eff_false_for_dlet(self):
        d = mk_dlet(nn('foo'), 0)
        result = ev(self.bc['Compiler.decl_is_eff'], d)
        self.assertEqual(result, 0)

    def test_decl_is_eff_false_for_other(self):
        """decl_is_eff returns 0 for DClass (tag 3)."""
        d = A(A(3, nn('Eq')), mk_nil())  # DClass
        result = ev(self.bc['Compiler.decl_is_eff'], d)
        self.assertEqual(result, 0)

    def test_decl_get_eff_name(self):
        d = mk_deff(nn('State'), mk_list(nn('get')))
        result = ev(self.bc['Compiler.decl_get_eff_name'], d)
        self.assertEqual(result, nn('State'))

    def test_decl_get_eff_ops(self):
        ops = mk_list(nn('get'), nn('put'))
        d = mk_deff(nn('State'), ops)
        result = ev(self.bc['Compiler.decl_get_eff_ops'], d)
        # Should be Cons(nn('get'), Cons(nn('put'), Nil))
        self.assertTrue(is_app(result))  # Cons


# ---------------------------------------------------------------------------
# Test: CPS constants present
# ---------------------------------------------------------------------------

class TestCPSConstants(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module()

    def test_cps_id_law_present(self):
        """cps_id_law is in compiled output."""
        self.assertIn('Compiler.cps_id_law', self.bc)

    def test_cps_null_dispatch_present(self):
        self.assertIn('Compiler.cps_null_dispatch', self.bc)

    def test_cps_compose_present(self):
        self.assertIn('Compiler.cps_compose', self.bc)

    def test_cps_pure_law_present(self):
        self.assertIn('Compiler.cps_pure_law', self.bc)

    def test_cps_run_law_present(self):
        self.assertIn('Compiler.cps_run_law', self.bc)


# ---------------------------------------------------------------------------
# Test: Bootstrap compiler compiles programs with effects
# ---------------------------------------------------------------------------

def compile_via_bootstrap(source: str, mod_name: str = 'Test') -> dict:
    """Compile source through the Python bootstrap and return compiled dict."""
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    prog = parse(lex(source, '<test>'), '<test>')
    resolved, _ = resolve(prog, mod_name, {}, '<test>')
    return compile_program(resolved, mod_name)


class TestEffectCompilation(unittest.TestCase):
    """Test that the bootstrap compiler can compile effect programs."""

    def test_eff_decl_pure_run(self):
        """Compile: eff + handle + pure + run — run (pure 42) = 42."""
        src = """
external mod Core.PLAN {
  inc : Nat → Nat
}

eff Ask {
  ask : Nat → Nat
}

let main : Nat
  = run (pure 42)
"""
        compiled = compile_via_bootstrap(src)
        result = bevaluate(compiled['Test.main'])
        self.assertEqual(result, 42)

    def test_handle_return(self):
        """handle (pure 10) { | return x → x } = 10."""
        src = """
external mod Core.PLAN {
  inc : Nat → Nat
}

eff Ask {
  ask : Nat → Nat
}

let main : Nat
  = run (handle (pure 10) {
      | return x → x
      | ask aa kk → kk 0
    })
"""
        compiled = compile_via_bootstrap(src)
        result = bevaluate(compiled['Test.main'])
        self.assertEqual(result, 10)

    def test_handle_effect_op(self):
        """handle (ask 5) { return x → x | ask a k → 99 } = 99."""
        src = """
external mod Core.PLAN {
  inc : Nat → Nat
}

eff Ask {
  ask : Nat → Nat
}

let main : Nat
  = run (handle (ask 5) {
      | return x → x
      | ask aa kk → 99
    })
"""
        compiled = compile_via_bootstrap(src)
        result = bevaluate(compiled['Test.main'])
        self.assertEqual(result, 99)

    def test_handle_resume(self):
        """handle (ask 5) { return x → x | ask a k → k (inc a) } = 6."""
        src = """
external mod Core.PLAN {
  inc : Nat → Nat
}

eff Ask {
  ask : Nat → Nat
}

let main : Nat
  = run (handle (ask 5) {
      | return x → x
      | ask aa kk → kk (Core.PLAN.inc aa)
    })
"""
        compiled = compile_via_bootstrap(src)
        result = bevaluate(compiled['Test.main'])
        self.assertEqual(result, 6)

    def test_do_bind(self):
        """x ← pure 10 in pure x = 10."""
        src = """
external mod Core.PLAN {
  inc : Nat → Nat
}

eff Ask {
  ask : Nat → Nat
}

let main : Nat
  = run (handle (xx ← pure 10 in pure xx) {
      | return x → x
      | ask aa kk → kk 0
    })
"""
        compiled = compile_via_bootstrap(src)
        result = bevaluate(compiled['Test.main'])
        self.assertEqual(result, 10)


# ---------------------------------------------------------------------------
# Test: GLS compiler self-hosting regression
# ---------------------------------------------------------------------------

class TestM12SelfhostRegression(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module()

    def test_compiler_compiles_without_error(self):
        """Compiler.gls still compiles cleanly after M12 effects additions."""
        self.assertIsNotNone(self.bc)
        self.assertGreater(len(self.bc), 100)

    def test_effect_functions_present(self):
        """Key effect functions are in the compiled output."""
        for name in ['cps_id_law', 'cps_null_dispatch', 'cps_compose',
                      'cps_pure_law', 'cps_run_law',
                      'cg_compile_handle', 'cg_compile_do',
                      'cg_compile_dispatch_fn', 'cg_compile_return_fn',
                      'cg_build_handle_dispatch', 'cg_apply_range',
                      'cg_register_eff_ops', 'cg_register_effs',
                      'kw_eff', 'kw_handle', 'kw_pure', 'kw_run',
                      'decl_is_eff', 'decl_get_eff_name', 'decl_get_eff_ops',
                      'parse_eff_decl', 'parse_handle_expr',
                      'sr_collect_eff_op_names', 'sr_rewrite_handle_arms',
                      'cg_cf_handle_arms']:
            fq = f'Compiler.{name}'
            self.assertIn(fq, self.bc, f'{fq} not found in compiled output')

    def test_use_functions_present(self):
        """DeclUse-related functions are in compiled output."""
        for name in ['kw_use', 'decl_is_use', 'parse_use_decl', 'parse_use_names']:
            fq = f'Compiler.{name}'
            self.assertIn(fq, self.bc, f'{fq} not found in compiled output')

    def test_kw_use(self):
        expected = nn('use')
        result = ev(self.bc['Compiler.kw_use'])
        self.assertEqual(result, expected)

    def test_decl_is_use_true(self):
        """decl_is_use returns 1 for a DUse value."""
        # DUse tag = 6 (7th constructor)
        d = A(A(6, nn('Core.Nat')), mk_nil())
        result = ev(self.bc['Compiler.decl_is_use'], d)
        self.assertEqual(result, 1)

    def test_decl_is_use_false_for_dlet(self):
        d = mk_dlet(nn('foo'), 0)
        result = ev(self.bc['Compiler.decl_is_use'], d)
        self.assertEqual(result, 0)


if __name__ == '__main__':
    unittest.main()
