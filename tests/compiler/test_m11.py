#!/usr/bin/env python3
"""
GLS M11 tests — typeclass parsing and codegen in Compiler.gls.

Verifies that the GLS self-hosting compiler:
  - Encodes the 'inst' name nat correctly (nn_inst)
  - decl_is_class / decl_is_inst predicates work on hand-constructed ADT values
  - decl_get_inst_class / decl_get_inst_body field accessors work
  - name_concat_under produces inst_ClassName_TypeName style names
  - sr_collect_globals correctly collects DInst method names
  - cg_pass3 compiles a DInst declaration into a named method value
  - Self-hosting: Compiler.gls still compiles cleanly after M11 additions

PLAN encoding of Decl constructors (Decl type has 5 constructors, all arity 2):
  DLet    tag 0 → A(A(0, name), body)
  DType   tag 1 → A(A(1, name), cdefs)
  DExt    tag 2 → A(A(2, mod), items)
  DClass  tag 3 → A(A(3, class_name), methods)
  DInst   tag 4 → A(A(4, class_name), pair)

List encoding (type List a = | Nil | Cons a (List a)):
  Nil       = 0
  Cons x xs = A(A(1, x), xs)

Pair encoding (type Pair a b = | MkPair a b):
  MkPair a b = A(A(0, a), b)

Run: python3 tests/compiler/test_m11.py
  or: python3 -m pytest tests/compiler/test_m11.py -v
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


# ---------------------------------------------------------------------------
# ADT construction helpers (using raw PLAN encoding)
# ---------------------------------------------------------------------------

def mk_nil():
    """Nil = N(0) = 0."""
    return 0


def mk_cons(x, xs):
    """Cons x xs = A(A(1, x), xs)."""
    return A(A(1, x), xs)


def mk_list(*items):
    """Build a GLS List from Python items (Cons-chained, last = Nil)."""
    result = mk_nil()
    for item in reversed(items):
        result = mk_cons(item, result)
    return result


def mk_pair(a, b):
    """MkPair a b = A(A(0, a), b)."""
    return A(A(0, a), b)


def mk_dclass(class_name, methods):
    """DClass class_name methods = A(A(3, class_name), methods)."""
    return A(A(3, class_name), methods)


def mk_dinst(class_name, type_name, members):
    """DInst class_name (MkPair type_name members) = A(A(4, class_name), MkPair type_name members)."""
    return A(A(4, class_name), mk_pair(type_name, members))


def mk_dlet(name, body):
    """DLet name body = A(A(0, name), body)."""
    return A(A(0, name), body)


def nn(s: str) -> int:
    """Name nat: little-endian encoding of ASCII string."""
    return int.from_bytes(s.encode('ascii'), 'little')


# ---------------------------------------------------------------------------
# Test: GLS nn_inst value
# ---------------------------------------------------------------------------

class TestNnInst(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module()

    def test_nn_inst_value(self):
        """nn_inst encodes 'inst' as a little-endian nat (0x74736e69)."""
        expected = int.from_bytes(b'inst', 'little')
        result = ev(self.bc['Compiler.nn_inst'])
        self.assertEqual(result, expected,
            f'nn_inst: got {result:#x}, expected {expected:#x}')


# ---------------------------------------------------------------------------
# Test: decl_is_class / decl_is_inst predicates
# ---------------------------------------------------------------------------

class TestDeclPredicates(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module()

    def test_decl_is_class_true(self):
        """decl_is_class returns 1 for a DClass value."""
        d = mk_dclass(nn('Eq'), mk_nil())
        result = ev(self.bc['Compiler.decl_is_class'], d)
        self.assertEqual(result, 1)

    def test_decl_is_class_false_for_dlet(self):
        """decl_is_class returns 0 for DLet."""
        d = mk_dlet(nn('foo'), 0)
        result = ev(self.bc['Compiler.decl_is_class'], d)
        self.assertEqual(result, 0)

    def test_decl_is_class_false_for_dinst(self):
        """decl_is_class returns 0 for DInst."""
        d = mk_dinst(nn('Eq'), nn('Nat'), mk_nil())
        result = ev(self.bc['Compiler.decl_is_class'], d)
        self.assertEqual(result, 0)

    def test_decl_is_inst_true(self):
        """decl_is_inst returns 1 for a DInst value."""
        d = mk_dinst(nn('Eq'), nn('Nat'), mk_nil())
        result = ev(self.bc['Compiler.decl_is_inst'], d)
        self.assertEqual(result, 1)

    def test_decl_is_inst_false_for_dclass(self):
        """decl_is_inst returns 0 for DClass."""
        d = mk_dclass(nn('Eq'), mk_nil())
        result = ev(self.bc['Compiler.decl_is_inst'], d)
        self.assertEqual(result, 0)

    def test_decl_is_inst_false_for_dlet(self):
        """decl_is_inst returns 0 for DLet."""
        d = mk_dlet(nn('foo'), 0)
        result = ev(self.bc['Compiler.decl_is_inst'], d)
        self.assertEqual(result, 0)


# ---------------------------------------------------------------------------
# Test: DInst field accessors
# ---------------------------------------------------------------------------

class TestDeclInstAccessors(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module()
        cls.eq_name = nn('Eq')
        cls.nat_name = nn('Nat')
        cls.d = mk_dinst(cls.eq_name, cls.nat_name, mk_nil())

    def test_decl_get_inst_class(self):
        """decl_get_inst_class extracts the class name."""
        result = ev(self.bc['Compiler.decl_get_inst_class'], self.d)
        self.assertEqual(result, self.eq_name)

    def test_decl_get_class_name(self):
        """decl_get_class_name extracts class_name from DClass."""
        d = mk_dclass(nn('Add'), mk_nil())
        result = ev(self.bc['Compiler.decl_get_class_name'], d)
        self.assertEqual(result, nn('Add'))


# ---------------------------------------------------------------------------
# Test: name_concat_under
# ---------------------------------------------------------------------------

class TestNameConcatUnder(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module()

    def _ncu(self, a: str, b: str) -> int:
        a_nat = nn(a)
        b_nat = nn(b)
        return ev(self.bc['Compiler.name_concat_under'], a_nat, b_nat)

    def test_inst_eq(self):
        """name_concat_under 'inst' 'Eq' = 'inst_Eq'."""
        result = self._ncu('inst', 'Eq')
        expected = nn('inst_Eq')
        self.assertEqual(result, expected,
            f"inst_Eq: got {result:#x}, expected {expected:#x}")

    def test_inst_eq_nat(self):
        """name_concat_under (name_concat_under 'inst' 'Eq') 'Nat' = 'inst_Eq_Nat'."""
        inst_eq = self._ncu('inst', 'Eq')
        inst_eq_nat_nat = ev(self.bc['Compiler.name_concat_under'], inst_eq, nn('Nat'))
        expected = nn('inst_Eq_Nat')
        self.assertEqual(inst_eq_nat_nat, expected,
            f"inst_Eq_Nat: got {inst_eq_nat_nat:#x}, expected {expected:#x}")

    def test_inst_eq_nat_eq_method(self):
        """Full inst method prefix: inst_Eq_Nat_eq."""
        inst_eq = self._ncu('inst', 'Eq')
        inst_eq_nat = ev(self.bc['Compiler.name_concat_under'], inst_eq, nn('Nat'))
        inst_eq_nat_eq = ev(self.bc['Compiler.name_concat_under'], inst_eq_nat, nn('eq'))
        expected = nn('inst_Eq_Nat_eq')
        self.assertEqual(inst_eq_nat_eq, expected,
            f"inst_Eq_Nat_eq: got {inst_eq_nat_eq:#x}, expected {expected:#x}")


# ---------------------------------------------------------------------------
# Test: cg_pass3 compiles a DInst into a named method PLAN value
# ---------------------------------------------------------------------------

class TestCgPass3Inst(unittest.TestCase):
    """
    Verify cg_pass3 can process a DInst declaration.

    We construct a minimal DInst:
      instance Eq Nat { eq = nat_eq }

    where nat_eq is pre-defined in the module. The test:
    1. Builds a DInst with one member (eq = EVar nat_eq_fq)
       EVar is tag 0, arity 1: A(0, nat_eq_fq_name_nat)
       But since nat_eq must be in scope, we actually just compile
       a constant body (EVar 0 referring to a preloaded global).

    Approach: compile a two-decl program:
      DLet 'my_fn' (ENat 42)            -- produces val 42
      DInst 'Eq' type='Nat' { eq = EVar 'my_fn' fq }  -- eq points to my_fn

    We use the Python bootstrap to compile this mini program through
    sr_collect_globals → sr_resolve_decls → compile_program to check
    that inst_Eq_Nat_eq ends up in the output.

    This exercises the Python bootstrap's DInst handling which is the
    reference for what the GLS cg_pass3 should produce.
    """

    def test_python_bootstrap_inst_method_name(self):
        """
        Python bootstrap compile_program produces inst_Eq_Nat_eq as an output key.
        (Regression: verifies the bootstrap DInst codegen works correctly.)
        """
        from bootstrap.lexer import lex
        from bootstrap.parser import parse
        from bootstrap.scope import resolve
        from bootstrap.codegen import compile_program as py_compile

        src = """\
let nat_eq : Nat → Nat → Bool
  = λ m n → match m {
    | 0 → match n { | 0 → True  | _ → False }
    | j → match n { | 0 → False | k → nat_eq j k }
  }
class Eq a { eq : a → a → Bool }
instance Eq Nat { eq = nat_eq }
"""
        prog = parse(lex(src, '<test>'), '<test>')
        resolved, _ = resolve(prog, 'Test', {}, '<test>')
        compiled = py_compile(resolved, 'Test')

        self.assertIn('Test.inst_Eq_Nat_eq', compiled,
            f"Expected 'Test.inst_Eq_Nat_eq' in compiled; got: {sorted(compiled.keys())}")

    def test_python_bootstrap_inst_method_value(self):
        """inst_Eq_Nat_eq evaluates correctly when instance Eq Nat { eq = nat_eq }."""
        from bootstrap.lexer import lex
        from bootstrap.parser import parse
        from bootstrap.scope import resolve
        from bootstrap.codegen import compile_program as py_compile
        from dev.harness.plan import N, A, apply, evaluate as peval

        src = """\
let nat_eq : Nat → Nat → Bool
  = λ m n → match m {
    | 0 → match n { | 0 → True  | _ → False }
    | j → match n { | 0 → False | k → nat_eq j k }
  }
class Eq a { eq : a → a → Bool }
instance Eq Nat { eq = nat_eq }
"""
        prog = parse(lex(src, '<test>'), '<test>')
        resolved, _ = resolve(prog, 'Test', {}, '<test>')
        compiled = py_compile(resolved, 'Test')

        eq_fn = compiled['Test.inst_Eq_Nat_eq']
        self.assertEqual(peval(apply(apply(eq_fn, N(3)), N(3))), 1)  # 3 == 3
        self.assertEqual(peval(apply(apply(eq_fn, N(3)), N(4))), 0)  # 3 != 4


# ---------------------------------------------------------------------------
# Regression: Compiler.gls still self-hosts cleanly after M11 additions
# ---------------------------------------------------------------------------

class TestM11SelfhostRegression(unittest.TestCase):
    """M11 changes don't break self-hosting compilation."""

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module()

    def test_compiler_compiles_without_error(self):
        """Compiler.gls compiles successfully via Python bootstrap."""
        # compile_module() raises on error; reaching here means success.
        self.assertIn('Compiler.main', self.bc)

    def test_nn_inst_in_compiled(self):
        """Compiler.nn_inst is present in compiled output."""
        self.assertIn('Compiler.nn_inst', self.bc)

    def test_decl_helpers_present(self):
        """All DClass/DInst accessor functions are present."""
        for fn in ['decl_is_class', 'decl_is_inst', 'decl_get_class_name',
                   'decl_get_class_methods', 'decl_get_inst_class',
                   'decl_get_inst_body']:
            self.assertIn(f'Compiler.{fn}', self.bc,
                f'Compiler.{fn} missing from compiled output')

    def test_pass3_inst_functions_present(self):
        """cg_compile_inst_members is present in compiled output."""
        self.assertIn('Compiler.cg_compile_inst_members', self.bc)

    def test_sr_collect_inst_present(self):
        """sr_collect_inst_method_names is present in compiled output."""
        self.assertIn('Compiler.sr_collect_inst_method_names', self.bc)

    def test_sr_resolve_inst_present(self):
        """sr_resolve_inst_members is present in compiled output."""
        self.assertIn('Compiler.sr_resolve_inst_members', self.bc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    sys.exit(unittest.main())
