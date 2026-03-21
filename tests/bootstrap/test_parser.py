#!/usr/bin/env python3
"""
Parser tests — bootstrap/parser.py

Covers spec/06-surface-syntax.md §3-9 for the restricted dialect.

Run: python3 tests/bootstrap/test_parser.py
  or: python3 -m pytest tests/bootstrap/test_parser.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.lexer import lex
from bootstrap.parser import parse, ParseError
from bootstrap.ast import *


def p(src: str) -> Program:
    return parse(lex(src, '<test>'), '<test>')

def decl(src: str):
    """Parse src as a single top-level declaration."""
    return p(src).decls[0]

def expr(src: str):
    """Parse src as an expression inside a let declaration body."""
    prog = p(f'let foo = {src}')
    return prog.decls[0].body

def ty(src: str):
    """Parse src as a type inside a let signature."""
    prog = p(f'let foo : {src} = 0')
    return prog.decls[0].type_ann

def pat(src: str):
    """Parse src as a pattern inside a match."""
    prog = p(f'let foo = match x {{ | {src} → 0 }}')
    return prog.decls[0].body.arms[0][0]


# ============================================================
# Program structure
# ============================================================

def test_empty_program():
    assert p('').decls == []

def test_single_let_decl():
    prog = p('let foo = 42')
    assert len(prog.decls) == 1
    assert isinstance(prog.decls[0], DeclLet)

def test_multiple_decls():
    prog = p('let foo = 1\nlet bar = 2')
    assert len(prog.decls) == 2


# ============================================================
# Let declarations
# ============================================================

def test_let_decl_name():
    d = decl('let foo = 42')
    assert isinstance(d, DeclLet)
    assert d.name == 'foo'

def test_let_decl_nat_body():
    d = decl('let foo = 42')
    assert isinstance(d.body, ExprNat)
    assert d.body.value == 42

def test_let_decl_with_type():
    d = decl('let foo : Nat = 42')
    assert d.type_ann is not None
    assert isinstance(d.type_ann, TyCon)

def test_let_decl_no_type():
    d = decl('let foo = 42')
    assert d.type_ann is None

def test_let_decl_contracts_ignored():
    d = decl('let foo : Nat\n  | pre Proven (0 = 0)\n  = 42')
    assert isinstance(d, DeclLet)
    assert len(d.contracts) == 1
    assert d.contracts[0].kind == 'pre'
    assert d.contracts[0].status == 'Proven'


# ============================================================
# Type declarations
# ============================================================

def test_type_decl_sum():
    d = decl('type Bool = | True | False')
    assert isinstance(d, DeclType)
    assert d.name == 'Bool'
    assert len(d.constructors) == 2
    assert d.constructors[0].name == 'True'
    assert d.constructors[1].name == 'False'

def test_type_decl_with_param():
    d = decl('type Maybe a = | Nothing | Just a')
    assert isinstance(d, DeclType)
    assert d.params == ['a']
    assert d.constructors[1].name == 'Just'
    assert len(d.constructors[1].arg_types) == 1

def test_type_decl_builtin():
    d = decl('type Nat : builtin')
    assert isinstance(d, DeclTypeBuiltin)
    assert d.name == 'Nat'

def test_type_decl_alias():
    d = decl('type MyNat = Nat')
    assert isinstance(d, DeclTypeAlias)
    assert d.name == 'MyNat'

def test_type_decl_record():
    d = decl('type Point = { x : Nat, y : Nat }')
    assert isinstance(d, DeclRecord)
    assert d.name == 'Point'
    assert len(d.fields) == 2
    assert d.fields[0][0] == 'x'
    assert d.fields[1][0] == 'y'

def test_type_decl_multi_arg_constructor():
    d = decl('type Pair a b = | MkPair a b')
    assert isinstance(d, DeclType)
    assert len(d.constructors[0].arg_types) == 2


# ============================================================
# Types
# ============================================================

def test_type_con():
    t = ty('Nat')
    assert isinstance(t, TyCon)
    assert str(t.name) == 'Nat'

def test_type_var():
    t = ty('a')
    assert isinstance(t, TyVar)
    assert t.name == 'a'

def test_type_arrow():
    t = ty('Nat → Nat')
    assert isinstance(t, TyArr)
    assert isinstance(t.from_, TyCon)
    assert isinstance(t.to_, TyCon)

def test_type_arrow_right_assoc():
    t = ty('Nat → Nat → Bool')
    assert isinstance(t, TyArr)
    assert isinstance(t.to_, TyArr)

def test_type_app():
    t = ty('List Nat')
    assert isinstance(t, TyApp)
    assert isinstance(t.fun, TyCon)
    assert isinstance(t.arg, TyCon)

def test_type_app_multi():
    t = ty('Map Text Nat')
    assert isinstance(t, TyApp)
    assert isinstance(t.fun, TyApp)

def test_type_forall():
    t = ty('∀ a. List a')
    assert isinstance(t, TyForall)
    assert t.vars == ['a']

def test_type_forall_multi():
    t = ty('∀ a b. Map a b')
    assert isinstance(t, TyForall)
    assert t.vars == ['a', 'b']

def test_type_tuple():
    t = ty('(Nat, Bool)')
    assert isinstance(t, TyTuple)
    assert len(t.elems) == 2

def test_type_unit():
    t = ty('⊤')
    assert isinstance(t, TyUnit)

def test_type_effect_annotated():
    t = ty('{IO} Nat')
    assert isinstance(t, TyEffect)
    assert isinstance(t.ty, TyCon)

def test_type_effect_empty_row():
    t = ty('{} Nat')
    assert isinstance(t, TyEffect)
    assert t.row.entries == []
    assert t.row.row_var is None

def test_type_constrained():
    t = ty('Eq a => List a')
    assert isinstance(t, TyConstrained)
    assert len(t.constraints) == 1
    assert t.constraints[0][0] == 'Eq'

def test_type_paren():
    t = ty('(Nat)')
    assert isinstance(t, TyCon)
    assert str(t.name) == 'Nat'

def test_type_qualified():
    t = ty('Core.List.List')
    assert isinstance(t, TyCon)
    assert str(t.name) == 'Core.List.List'


# ============================================================
# Expressions
# ============================================================

def test_expr_nat():
    e = expr('42')
    assert isinstance(e, ExprNat)
    assert e.value == 42

def test_expr_var():
    e = expr('foo')
    assert isinstance(e, ExprVar)
    assert str(e.name) == 'foo'

def test_expr_constructor():
    e = expr('True')
    assert isinstance(e, ExprVar)
    assert str(e.name) == 'True'

def test_expr_app():
    e = expr('foo 42')
    assert isinstance(e, ExprApp)
    assert isinstance(e.fun, ExprVar)
    assert isinstance(e.arg, ExprNat)

def test_expr_app_multi():
    e = expr('foo 1 2')
    assert isinstance(e, ExprApp)
    assert isinstance(e.fun, ExprApp)

def test_expr_lambda():
    e = expr('λ foo → foo')
    assert isinstance(e, ExprLam)
    assert len(e.params) == 1
    assert isinstance(e.body, ExprVar)

def test_expr_lambda_multi_arg():
    e = expr('λ foo bar → foo')
    assert isinstance(e, ExprLam)
    assert len(e.params) == 2

def test_expr_let():
    # Bootstrap restriction: requires 'in' to separate rhs from body.
    e = expr('let foo = 1 in foo')
    assert isinstance(e, ExprLet)
    assert isinstance(e.rhs, ExprNat)
    assert isinstance(e.body, ExprVar)

def test_expr_let_with_type():
    e = expr('let foo : Nat = 1 in foo')
    assert isinstance(e, ExprLet)
    assert isinstance(e.type_ann, TyCon)

def test_expr_if():
    e = expr('if True then 1 else 0')
    assert isinstance(e, ExprIf)
    assert isinstance(e.cond, ExprVar)
    assert isinstance(e.then_, ExprNat)
    assert isinstance(e.else_, ExprNat)

def test_expr_match():
    e = expr('match foo { | True → 1 | False → 0 }')
    assert isinstance(e, ExprMatch)
    assert len(e.arms) == 2

def test_expr_match_scrutinee():
    e = expr('match foo { | True → 1 | False → 0 }')
    assert isinstance(e.scrutinee, ExprVar)
    assert str(e.scrutinee.name) == 'foo'

def test_expr_tuple():
    e = expr('(1, 2)')
    assert isinstance(e, ExprTuple)
    assert len(e.elems) == 2

def test_expr_tuple_3():
    e = expr('(1, 2, 3)')
    assert isinstance(e, ExprTuple)
    assert len(e.elems) == 3

def test_expr_unit():
    e = expr('()')
    assert isinstance(e, ExprUnit)

def test_expr_list_empty():
    e = expr('[]')
    assert isinstance(e, ExprList)
    assert e.elems == []

def test_expr_list():
    e = expr('[1, 2, 3]')
    assert isinstance(e, ExprList)
    assert len(e.elems) == 3

def test_expr_text():
    e = expr('"hello"')
    assert isinstance(e, ExprText)
    assert e.value == 'hello'

def test_expr_pin():
    # Bootstrap restriction: requires 'in' to separate rhs from body.
    e = expr('@result = 42 in result')
    assert isinstance(e, ExprPin)
    assert e.name == 'result'
    assert isinstance(e.rhs, ExprNat)

def test_expr_op_add():
    e = expr('1 + 2')
    assert isinstance(e, ExprOp)
    assert e.op == '+'

def test_expr_op_cons():
    e = expr('1 :: []')
    assert isinstance(e, ExprOp)
    assert e.op == '::'

def test_expr_op_concat():
    e = expr('"a" ++ "b"')
    assert isinstance(e, ExprOp)
    assert e.op == '++'

def test_expr_op_pipe():
    e = expr('foo |> bar')
    assert isinstance(e, ExprOp)
    assert e.op == '|>'

def test_expr_ann():
    e = expr('42 : Nat')
    assert isinstance(e, ExprAnn)
    assert isinstance(e.expr, ExprNat)
    assert isinstance(e.type_, TyCon)

def test_expr_paren():
    e = expr('(42)')
    assert isinstance(e, ExprNat)

def test_expr_qualified():
    e = expr('Core.Nat.add')
    assert isinstance(e, ExprVar)
    assert str(e.name) == 'Core.Nat.add'

def test_expr_fix():
    e = expr('fix λ self → self')
    assert isinstance(e, ExprFix)
    assert isinstance(e.lam, ExprLam)

def test_expr_handle():
    e = expr('handle comp { | return foo → foo }')
    assert isinstance(e, ExprHandle)
    assert len(e.arms) == 1

def test_expr_match_guard():
    e = expr('match foo { | bar if bar → 1 | baz → 0 }')
    assert isinstance(e, ExprMatch)
    assert e.arms[0][1] is not None   # guard present
    assert e.arms[1][1] is None        # no guard

def test_expr_record():
    e = expr('{ foo = 1, bar = 2 }')
    assert isinstance(e, ExprRecord)
    assert len(e.fields) == 2


# ============================================================
# Patterns
# ============================================================

def test_pat_wild():
    p_ = pat('_')
    assert isinstance(p_, PatWild)

def test_pat_var():
    p_ = pat('foo')
    assert isinstance(p_, PatVar)
    assert p_.name == 'foo'

def test_pat_nat():
    p_ = pat('42')
    assert isinstance(p_, PatNat)
    assert p_.value == 42

def test_pat_constructor_noarg():
    p_ = pat('True')
    assert isinstance(p_, PatCon)
    assert str(p_.name) == 'True'
    assert p_.args == []

def test_pat_constructor_arg():
    p_ = pat('Just foo')
    assert isinstance(p_, PatCon)
    assert p_.name.parts == ['Just']
    assert len(p_.args) == 1
    assert isinstance(p_.args[0], PatVar)

def test_pat_constructor_multi_arg():
    p_ = pat('Pair foo bar')
    assert isinstance(p_, PatCon)
    assert len(p_.args) == 2

def test_pat_tuple():
    p_ = pat('(foo, bar)')
    assert isinstance(p_, PatTuple)
    assert len(p_.pats) == 2

def test_pat_list_empty():
    p_ = pat('[]')
    assert isinstance(p_, PatList)
    assert p_.pats == []

def test_pat_list():
    p_ = pat('[foo, bar]')
    assert isinstance(p_, PatList)
    assert len(p_.pats) == 2

def test_pat_as():
    p_ = pat('foo as bar')
    assert isinstance(p_, PatAs)
    assert p_.name == 'bar'

def test_pat_cons():
    p_ = pat('foo :: bar')
    assert isinstance(p_, PatCons)
    assert isinstance(p_.head, PatVar)
    assert isinstance(p_.tail, PatVar)

def test_pat_or():
    p_ = pat('True | False')
    assert isinstance(p_, PatOr)
    assert len(p_.pats) == 2

def test_pat_nested():
    p_ = pat('Just (foo, bar)')
    assert isinstance(p_, PatCon)
    assert len(p_.args) == 1
    assert isinstance(p_.args[0], PatTuple)


# ============================================================
# Module and use declarations
# ============================================================

def test_mod_decl():
    d = decl('mod Foo { let bar = 42 }')
    assert isinstance(d, DeclMod)
    assert d.name == ['Foo']
    assert len(d.body) == 1

def test_mod_qualified():
    d = decl('mod Foo.Bar { }')
    assert isinstance(d, DeclMod)
    assert d.name == ['Foo', 'Bar']

def test_use_decl_bare():
    d = decl('use Core.List')
    assert isinstance(d, DeclUse)
    assert d.module_path == ['Core', 'List']
    assert d.spec is None

def test_use_decl_specific():
    d = decl('use Core.List { map, filter }')
    assert isinstance(d, DeclUse)
    assert d.spec is not None
    assert not d.spec.unqualified

def test_use_decl_unqualified():
    d = decl('use Core.List unqualified { map }')
    assert isinstance(d, DeclUse)
    assert d.spec.unqualified


# ============================================================
# External mod
# ============================================================

def test_external_mod():
    d = decl('external mod Core.Nat { add : Nat → Nat → Nat }')
    assert isinstance(d, DeclExt)
    assert d.module_path == ['Core', 'Nat']
    assert len(d.items) == 1
    assert d.items[0].name == 'add'
    assert not d.items[0].is_type

def test_external_mod_type():
    d = decl('external mod Core.Nat { type Nat : builtin }')
    assert isinstance(d, DeclExt)
    assert d.items[0].is_type
    assert d.items[0].name == 'Nat'

def test_external_mod_multi():
    src = 'external mod Core.Nat { type Nat : builtin  add : Nat → Nat → Nat }'
    d = decl(src)
    assert len(d.items) == 2


# ============================================================
# Class / instance
# ============================================================

def test_class_decl():
    d = decl('class Eq a { eq : a → a → Bool }')
    assert isinstance(d, DeclClass)
    assert d.name == 'Eq'
    assert d.params == ['a']
    assert len(d.members) == 1

def test_class_member_default():
    d = decl('class Eq a { ne : a → a → Bool = λ foo bar → False }')
    # False is a keyword-literal expression
    assert isinstance(d, DeclClass)
    assert d.members[0].default is not None

def test_instance_decl():
    d = decl('instance Eq Nat { eq = λ foo bar → True }')
    assert isinstance(d, DeclInst)
    assert d.class_name == 'Eq'
    assert len(d.members) == 1
    assert d.members[0].name == 'eq'

def test_instance_decl_constrained():
    d = decl('instance Eq a => Eq (Maybe a) { eq = λ foo bar → True }')
    assert isinstance(d, DeclInst)
    assert len(d.constraints) == 1


# ============================================================
# Eff declarations
# ============================================================

def test_eff_decl():
    d = decl('eff State s { get : ⊤ → s  put : s → ⊤ }')
    assert isinstance(d, DeclEff)
    assert d.name == 'State'
    assert d.params == ['s']
    assert len(d.ops) == 2
    assert d.ops[0].name == 'get'


# ============================================================
# Error cases
# ============================================================

def test_parse_error_unexpected_token():
    try:
        p('42')   # nat literal at top level is not a valid decl
        assert False, "should have raised"
    except ParseError:
        pass

def test_parse_error_missing_eq():
    try:
        p('let foo 42')
        assert False
    except ParseError:
        pass

def test_parse_error_unclosed_brace():
    try:
        p('mod Foo {')
        assert False
    except ParseError:
        pass


# ============================================================
# Source locations
# ============================================================

def test_location_on_decl():
    prog = parse(lex('let foo = 42', 'test.gls'), 'test.gls')
    assert prog.decls[0].loc.file == 'test.gls'
    assert prog.decls[0].loc.line == 1

def test_location_on_expr():
    prog = parse(lex('let foo = 42', 'test.gls'), 'test.gls')
    body = prog.decls[0].body
    assert isinstance(body, ExprNat)
    assert body.loc.line == 1


# ============================================================
# Operator precedence
# ============================================================

def test_op_prec_add_mul():
    """1 + 2 * 3 parses as 1 + (2 * 3)."""
    e = expr('1 + 2 * 3')
    assert isinstance(e, ExprOp)
    assert e.op == '+'
    assert isinstance(e.rhs, ExprOp)
    assert e.rhs.op == '*'

def test_op_prec_app_binds_tightest():
    """foo 1 + 2 parses as (foo 1) + 2."""
    e = expr('foo 1 + 2')
    assert isinstance(e, ExprOp)
    assert e.op == '+'
    assert isinstance(e.lhs, ExprApp)

def test_op_pipe_lowest():
    """1 + 2 |> foo parses as (1 + 2) |> foo."""
    e = expr('1 + 2 |> foo')
    assert isinstance(e, ExprOp)
    assert e.op == '|>'
    assert isinstance(e.lhs, ExprOp)
    assert e.lhs.op == '+'

def test_cons_right_assoc():
    """1 :: 2 :: [] parses as 1 :: (2 :: [])."""
    e = expr('1 :: 2 :: []')
    assert isinstance(e, ExprOp)
    assert e.op == '::'
    assert isinstance(e.rhs, ExprOp)
    assert e.rhs.op == '::'


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
