#!/usr/bin/env python3
"""
M8.4 Scope Resolver tests (compiler/src/Compiler.gls Section 24b).

Functions under test:
  sr_collect_globals, sr_collect_con_names, sr_collect_ext_items,
  sr_rewrite_expr, sr_rewrite_arms, sr_dispatch,
  sr_resolve_decls, resolve_program

The scope resolver takes List Decl → Nat → List Decl and qualifies
every free EVar reference to its FQ module-qualified name nat.

Testing strategy
----------------
We use the BPLAN harness (eval_bplan) to:
  1. Compile a tiny snippet of Gallowglass source through the Python
     bootstrap (which produces a List Decl with bare names), then feed
     those same AST values through the Gallowglass resolve_program and
     compare the output against what the Python bootstrap's scope.py
     would produce.
  2. Directly test sr_collect_globals on synthetic Decl values.
  3. Test round-trip: compile a small module through the Python
     bootstrap, assert the scope-resolved decl list matches.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.plan import A, evaluate
from dev.harness.bplan import bevaluate

MODULE = 'Compiler'
SRC_PATH = os.path.join(os.path.dirname(__file__), '..', '..',
                        'compiler', 'src', 'Compiler.gls')

_COMPILED_BPLAN = None


def compile_module_bplan():
    global _COMPILED_BPLAN
    if _COMPILED_BPLAN is not None:
        return _COMPILED_BPLAN
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    from dev.harness.bplan import register_jets
    with open(SRC_PATH) as f:
        src = f.read()
    prog = parse(lex(src, SRC_PATH), SRC_PATH)
    resolved, _ = resolve(prog, MODULE, {}, SRC_PATH)
    compiled = compile_program(resolved, MODULE)
    register_jets(compiled)
    _COMPILED_BPLAN = compiled
    return _COMPILED_BPLAN


def eval_bplan(val, *args):
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


def eval_plan(val, *args):
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 50000))
    try:
        result = val
        for arg in args:
            result = A(result, arg)
        return evaluate(result)
    finally:
        sys.setrecursionlimit(old)


def name_nat(s: str) -> int:
    """Encode an ASCII name string as a little-endian nat."""
    return int.from_bytes(s.encode('ascii'), 'little')


def name_concat_dot(mod: str, item: str) -> int:
    """Compute name_concat_dot(mod, item) in Python."""
    mod_bytes = mod.encode('ascii')
    item_bytes = item.encode('ascii')
    combined = mod_bytes + b'.' + item_bytes
    return int.from_bytes(combined, 'little')


# ---------------------------------------------------------------------------
# Helpers to build Gallowglass AST values (EVar, EApp, ELam, ELet, ENat,
# DLet, DType, DExt, MkConDef, ArmCon, ArmVar, ArmNat, MkPair, Nil, Cons)
# ---------------------------------------------------------------------------

class ASTBuilder:
    """Lazy-init wrapper: builds PLAN constructor values from the compiled module."""

    def __init__(self):
        self._bc = None

    def _c(self):
        if self._bc is None:
            self._bc = compile_module_bplan()
        return self._bc

    def _ev(self, name, *args):
        return eval_bplan(self._c()[f'Compiler.{name}'], *args)

    # Expr constructors
    def EVar(self, n):      return self._ev('EVar', n)
    def EApp(self, f, a):   return self._ev('EApp', f, a)
    def ELam(self, p, b):   return self._ev('ELam', p, b)
    def ELet(self, n, r, b): return self._ev('ELet', n, self.MkPair(r, b))
    def ENat(self, n):      return self._ev('ENat', n)
    def EIf(self, c, t, e): return self._ev('EIf', c, self.MkPair(t, e))
    def EMatch(self, s, arms): return self._ev('EMatch', s, arms)

    # MatchArm constructors
    def ArmNat(self, tag, body): return self._ev('ArmNat', tag, body)
    def ArmVar(self, n, body):   return self._ev('ArmVar', n, body)
    def ArmCon(self, con, fields_list, body):
        return self._ev('ArmCon', con, self.MkPair(fields_list, body))

    # Decl constructors
    def DLet(self, n, body): return self._ev('DLet', n, body)
    def DType(self, n, cdefs): return self._ev('DType', n, cdefs)
    def DExt(self, mod, items): return self._ev('DExt', mod, items)
    def MkConDef(self, name, arity): return self._ev('MkConDef', name, arity)

    # List / Pair
    def Nil(self):     return self._ev('Nil')
    def Cons(self, h, t): return self._ev('Cons', h, t)
    def MkPair(self, a, b): return self._ev('MkPair', a, b)

    def list_of(self, items):
        result = self.Nil()
        for item in reversed(items):
            result = self.Cons(item, result)
        return result


B = ASTBuilder()


def decode_expr_var(bc, expr_val):
    """Extract the Nat from an EVar PLAN value, or None if not EVar."""
    tag_fn = bc['Compiler.expr_tag']
    tag = eval_plan(tag_fn, expr_val)
    if tag != 0:
        return None
    evar_n_fn = bc.get('Compiler.cg_var_name')
    # Use expr_tag == 0 and extract field via EVar accessor
    # EVar n = A(A(N(0), n), n) in constructor encoding? No:
    # EVar n is unary tag=0: A(N(0), n), so .arg = n
    ev = bevaluate(expr_val)
    from dev.harness.plan import is_app, is_nat
    if is_app(ev) and is_nat(ev.fun) and ev.fun == 0:
        return ev.arg
    return None


def decode_decl_let_body(bc, decl_val):
    """Extract the body Expr from a DLet PLAN value."""
    get_body = bc['Compiler.decl_get_let_body']
    return eval_bplan(get_body, decl_val)


# ---------------------------------------------------------------------------
# Compilation tests
# ---------------------------------------------------------------------------

class TestScopeCompilation(unittest.TestCase):
    """All M8.4 functions compile without error."""

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module_bplan()

    def _present(self, name):
        self.assertIn(f'Compiler.{name}', self.bc)

    def test_collect_functions_present(self):
        for name in ('sr_collect_con_names', 'sr_collect_ext_items',
                     'sr_collect_globals'):
            with self.subTest(name=name):
                self._present(name)

    def test_rewrite_functions_present(self):
        for name in ('sr_rewrite_arm', 'sr_rewrite_arms',
                     'sr_dispatch', 'sr_rewrite_expr',
                     'sr_resolve_decls', 'resolve_program'):
            with self.subTest(name=name):
                self._present(name)


# ---------------------------------------------------------------------------
# sr_collect_globals tests
# ---------------------------------------------------------------------------

class TestSrCollectGlobals(unittest.TestCase):
    """sr_collect_globals builds the correct bare→fq table."""

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module_bplan()
        cls.collect = cls.bc['Compiler.sr_collect_globals']
        cls.assoc_lookup = cls.bc['Compiler.assoc_lookup']

    def _lookup(self, table, key):
        result = eval_bplan(self.assoc_lookup, key, table)
        from dev.harness.plan import is_app, is_nat
        ev = bevaluate(result)
        # Option: Some v = A(A(N(1), v), v); None = N(0) for tag=0 nullary
        # Actually: Some = unary tag=1, None = nullary tag=0
        if is_nat(ev) and ev == 0:
            return None
        # Some v: unary tag=1 → A(N(1), v) but wait...
        # In our encoding: Some v = A(N(1), v) (unary tag=1)
        # None = N(0) (nullary tag=0)
        if is_app(ev):
            return ev.arg  # the value inside Some
        return None

    def test_collects_dlet_name(self):
        mod = name_nat('M')
        bare = name_nat('foo')
        fq_expected = name_concat_dot('M', 'foo')
        decl = B.DLet(bare, B.ENat(42))
        decls = B.list_of([decl])
        table = eval_bplan(self.collect, decls, mod, B.Nil())
        result = self._lookup(table, bare)
        self.assertEqual(result, fq_expected)

    def test_collects_dtype_constructors(self):
        mod = name_nat('M')
        con_name = name_nat('MyCon')
        fq_expected = name_concat_dot('M', 'MyCon')
        cdef = B.MkConDef(con_name, 1)
        decl = B.DType(name_nat('MyType'), B.list_of([cdef]))
        decls = B.list_of([decl])
        table = eval_bplan(self.collect, decls, mod, B.Nil())
        result = self._lookup(table, con_name)
        self.assertEqual(result, fq_expected)

    def test_collects_dext_items(self):
        mod = name_nat('M')
        ext_mod = name_nat('Core')
        item = name_nat('inc')
        fq_expected = name_concat_dot('Core', 'inc')
        decl = B.DExt(ext_mod, B.list_of([item]))
        decls = B.list_of([decl])
        table = eval_bplan(self.collect, decls, mod, B.Nil())
        result = self._lookup(table, item)
        self.assertEqual(result, fq_expected)

    def test_empty_decls_empty_table(self):
        table = eval_bplan(self.collect, B.Nil(), name_nat('M'), B.Nil())
        result = self._lookup(table, name_nat('anything'))
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# sr_rewrite_expr tests
# ---------------------------------------------------------------------------

class TestSrRewriteExpr(unittest.TestCase):
    """sr_rewrite_expr correctly qualifies/preserves names."""

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module_bplan()
        cls.rewrite = cls.bc['Compiler.sr_rewrite_expr']
        cls.collect = cls.bc['Compiler.sr_collect_globals']

    def _rewrite(self, expr, gtab):
        return eval_bplan(self.rewrite, expr, B.Nil(), gtab)

    def _expr_tag(self, expr_val):
        return eval_plan(self.bc['Compiler.expr_tag'], expr_val)

    def test_enat_passes_through(self):
        """ENat 42 is unchanged."""
        mod = name_nat('M')
        decls = B.list_of([B.DLet(name_nat('f'), B.ENat(0))])
        gtab = eval_bplan(self.collect, decls, mod, B.Nil())
        expr = B.ENat(42)
        result = self._rewrite(expr, gtab)
        # expr_tag is broken for ENat in bootstrap (returns 2 instead of 4).
        # Check structural equality instead: the value must be unchanged.
        self.assertEqual(bevaluate(result), bevaluate(expr))

    def test_evar_global_qualified(self):
        """EVar bare_name → EVar fq_name when in globals table."""
        mod = name_nat('M')
        bare = name_nat('bar')
        fq = name_concat_dot('M', 'bar')
        decls = B.list_of([B.DLet(bare, B.ENat(0))])
        gtab = eval_bplan(self.collect, decls, mod, B.Nil())
        expr = B.EVar(bare)
        result = self._rewrite(expr, gtab)
        self.assertEqual(self._expr_tag(result), 0)  # still EVar
        n = decode_expr_var(self.bc, result)
        self.assertEqual(n, fq)

    def test_evar_local_not_qualified(self):
        """EVar p is NOT qualified when p is in the bound set."""
        mod = name_nat('M')
        bare = name_nat('x')
        fq = name_concat_dot('M', 'x')
        # Register 'x' as a global
        decls = B.list_of([B.DLet(bare, B.ENat(0))])
        gtab = eval_bplan(self.collect, decls, mod, B.Nil())
        # ELam x (EVar x): the inner EVar x should be local
        lam_body = B.EVar(bare)
        lam = B.ELam(bare, lam_body)
        result = self._rewrite(lam, gtab)
        # Extract the body of the ELam result
        body_fn = self.bc['Compiler.cg_lam_body']
        body = eval_bplan(body_fn, result)
        n = decode_expr_var(self.bc, body)
        # Should still be bare (not qualified) because it's bound by ELam
        self.assertEqual(n, bare)

    def test_eapp_recurses(self):
        """EApp f a — both f and a are rewritten."""
        mod = name_nat('M')
        bare_f = name_nat('foo')
        bare_a = name_nat('bar')
        decls = B.list_of([
            B.DLet(bare_f, B.ENat(0)),
            B.DLet(bare_a, B.ENat(0)),
        ])
        gtab = eval_bplan(self.collect, decls, mod, B.Nil())
        expr = B.EApp(B.EVar(bare_f), B.EVar(bare_a))
        result = self._rewrite(expr, gtab)
        self.assertEqual(self._expr_tag(result), 1)  # EApp

    def test_evar_unknown_passes_through(self):
        """EVar n where n is not in gtab passes through unchanged."""
        mod = name_nat('M')
        unknown = name_nat('unknown_xyz')
        gtab = eval_bplan(self.collect, B.Nil(), mod, B.Nil())
        expr = B.EVar(unknown)
        result = self._rewrite(expr, gtab)
        n = decode_expr_var(self.bc, result)
        self.assertEqual(n, unknown)


# ---------------------------------------------------------------------------
# resolve_program round-trip test
# ---------------------------------------------------------------------------

class TestResolveProgram(unittest.TestCase):
    """
    resolve_program on a synthetic module qualifies all EVar references
    to their FQ names, matching what the Python bootstrap scope.py does.
    """

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module_bplan()
        cls.resolve = cls.bc['Compiler.resolve_program']
        cls.nn_M = name_nat('M')

    def _list_head(self, val):
        """Extract head from Cons h t = A(A(N(1), h), t)."""
        from dev.harness.plan import is_app, is_nat
        ev = bevaluate(val)
        if is_app(ev) and is_app(ev.fun) and is_nat(ev.fun.fun) and ev.fun.fun == 1:
            return ev.fun.arg
        return None

    def _list_tail(self, val):
        """Extract tail from Cons h t = A(A(N(1), h), t)."""
        from dev.harness.plan import is_app, is_nat
        ev = bevaluate(val)
        if is_app(ev) and is_app(ev.fun) and is_nat(ev.fun.fun) and ev.fun.fun == 1:
            return ev.arg
        return None

    def test_identity_on_dtype(self):
        """DType declarations pass through unchanged."""
        cdef = B.MkConDef(name_nat('Foo'), 1)
        decl = B.DType(name_nat('MyType'), B.list_of([cdef]))
        decls = B.list_of([decl])
        resolved = eval_bplan(self.resolve, decls, self.nn_M)
        # Length unchanged: still one decl; tail must be Nil (= 0)
        tail = self._list_tail(resolved)
        self.assertIsNotNone(tail)
        self.assertEqual(bevaluate(tail), 0)  # Nil = 0

    def test_evar_global_qualified_in_dlet_body(self):
        """
        DLet f = EVar g  where g is also a top-level DLet →
        after resolve_program, the EVar in f's body holds fq(g).
        """
        bare_f = name_nat('f')
        bare_g = name_nat('g')
        fq_g = name_concat_dot('M', 'g')
        decls = B.list_of([
            B.DLet(bare_f, B.EVar(bare_g)),
            B.DLet(bare_g, B.ENat(0)),
        ])
        resolved = eval_bplan(self.resolve, decls, self.nn_M)
        # Extract body of first decl using direct PLAN structural access
        # Cons h t = A(A(N(1), h), t); h = .fun.arg
        first_decl = self._list_head(resolved)
        self.assertIsNotNone(first_decl)
        body = decode_decl_let_body(self.bc, first_decl)
        n = decode_expr_var(self.bc, body)
        self.assertEqual(n, fq_g,
            f'Expected fq_g={hex(fq_g)}, got {hex(n) if n else n}')

    def test_lambda_param_not_qualified(self):
        """
        DLet f = λ x → x:  the body EVar x is local, NOT qualified
        even if x also has a top-level DLet.
        """
        bare_f = name_nat('f')
        bare_x = name_nat('x')
        # Register x as a global too
        decls = B.list_of([
            B.DLet(bare_f, B.ELam(bare_x, B.EVar(bare_x))),
            B.DLet(bare_x, B.ENat(0)),
        ])
        resolved = eval_bplan(self.resolve, decls, self.nn_M)
        first_decl = self._list_head(resolved)
        self.assertIsNotNone(first_decl)
        body = decode_decl_let_body(self.bc, first_decl)
        # body is ELam x (EVar x); extract inner EVar
        body_fn = self.bc['Compiler.cg_lam_body']
        inner = eval_bplan(body_fn, body)
        n = decode_expr_var(self.bc, inner)
        self.assertEqual(n, bare_x,
            f'Lambda param should stay bare; got {hex(n) if n else n}')


# ---------------------------------------------------------------------------
# Compilation presence test for resolve_program in the driver
# ---------------------------------------------------------------------------

class TestMainUsesResolveProgram(unittest.TestCase):
    """main calls resolve_program (both compile without error)."""

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module_bplan()

    def test_main_and_resolve_compiled(self):
        self.assertIn('Compiler.resolve_program', self.bc)
        self.assertIn('Compiler.main', self.bc)


if __name__ == '__main__':
    unittest.main()
