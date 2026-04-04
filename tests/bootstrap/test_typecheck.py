#!/usr/bin/env python3
"""
Type checker tests — bootstrap/typecheck.py

Covers: literal types, variables, lambda, application, let,
match, if/else, tuples, type annotations, constructors, operators,
forward references, top-level generalization, and error cases.

Run: python3 tests/bootstrap/test_typecheck.py
  or: python3 -m pytest tests/bootstrap/test_typecheck.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.typecheck import (
    typecheck, TypecheckError, TypeEnv,
    TCon, TArr, TApp, TTup, TBound, TMeta, Scheme,
)
from bootstrap.scope import ScopeError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pipeline(src: str, module: str = 'Test', module_env: dict | None = None):
    """Lex → parse → resolve → typecheck. Returns TypeEnv."""
    prog = parse(lex(src, '<test>'), '<test>')
    resolved, env = resolve(prog, module, module_env or {}, '<test>')
    return typecheck(resolved, env, module, '<test>')


def ty_of(src: str, name: str, module: str = 'Test') -> Scheme:
    """Return the inferred Scheme for module.name after type-checking src."""
    te = pipeline(src, module)
    fq = f"{module}.{name}"
    assert fq in te, f"'{fq}' not in TypeEnv"
    return te[fq]


def check_error(src: str, fragment: str, module: str = 'Test') -> None:
    try:
        pipeline(src, module)
        assert False, f"expected error containing {fragment!r}"
    except (TypecheckError, ScopeError) as exc:
        assert fragment in str(exc), \
            f"expected {fragment!r} in error: {str(exc)!r}"


# ---------------------------------------------------------------------------
# Literal types
# ---------------------------------------------------------------------------

def test_nat_literal():
    s = ty_of('let val = 42', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'

def test_text_literal():
    s = ty_of('let val = "hello"', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Text'

def test_bytes_literal():
    s = ty_of('let val = b"ab"', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Bytes'

def test_hexbytes_literal():
    # x"..." is the hex-encoded bytes literal syntax
    s = ty_of('let val = x"414243"', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Bytes'

def test_unit_literal():
    s = ty_of('let val = Unit', 'val')
    assert isinstance(s.body, TCon) and s.body.name == '⊤'


# ---------------------------------------------------------------------------
# Bool constructors
# ---------------------------------------------------------------------------

def test_true_constructor():
    s = ty_of('let val = True', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Bool'

def test_false_constructor():
    s = ty_of('let val = False', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Bool'


# ---------------------------------------------------------------------------
# Lambda and application
# ---------------------------------------------------------------------------

def test_identity_inferred():
    """λ n → n is polymorphic: ∀ a. a → a"""
    s = ty_of('let id_fn = λ n → n', 'id_fn')
    assert len(s.vars) == 1
    assert isinstance(s.body, TArr)
    dom = s.body.dom
    cod = s.body.cod
    assert isinstance(dom, TBound) and isinstance(cod, TBound)
    assert dom.name == cod.name

def test_const_inferred():
    """λ n m → n : ∀ a b. a → b → a"""
    s = ty_of('let const_fn = λ n m → n', 'const_fn')
    assert len(s.vars) == 2
    assert isinstance(s.body, TArr)

def test_nat_succ_inferred():
    """λ n → n + 1 : Nat → Nat"""
    s = ty_of('let succ = λ n → n + 1', 'succ')
    assert isinstance(s.body, TArr)
    assert isinstance(s.body.dom, TCon) and s.body.dom.name == 'Nat'
    assert isinstance(s.body.cod, TCon) and s.body.cod.name == 'Nat'

def test_application_nat():
    """(λ n → n + 1) 41 : Nat"""
    s = ty_of('let result = (λ n → n + 1) 41', 'result')
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'

def test_multi_arg_lambda():
    """λ aa bb → aa + bb : Nat → Nat → Nat"""
    s = ty_of('let add = λ aa bb → aa + bb', 'add')
    assert isinstance(s.body, TArr)
    inner = s.body.cod
    assert isinstance(inner, TArr)


# ---------------------------------------------------------------------------
# Type annotations
# ---------------------------------------------------------------------------

def test_annotation_nat():
    s = ty_of('let val : Nat = 0', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'

def test_annotation_arrow():
    s = ty_of('let fn1 : Nat → Nat = λ n → n + 1', 'fn1')
    assert isinstance(s.body, TArr)

def test_annotation_polymorphic():
    s = ty_of('let id_fn : a → a = λ n → n', 'id_fn')
    assert len(s.vars) == 1

def test_annotation_mismatch():
    check_error('let val : Bool = 42', "cannot unify")

def test_annotation_arrow_mismatch():
    check_error('let fn1 : Nat → Bool = λ n → n + 1', "cannot unify")


# ---------------------------------------------------------------------------
# If/then/else
# ---------------------------------------------------------------------------

def test_if_returns_nat():
    s = ty_of('let result = if True then 1 else 2', 'result')
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'

def test_if_branches_must_match():
    check_error('let result = if True then 1 else "no"', "cannot unify")

def test_if_condition_must_be_bool():
    check_error('let result = if 0 then 1 else 2', "cannot unify")

def test_if_returns_text():
    s = ty_of('let result = if True then "yes" else "no"', 'result')
    assert isinstance(s.body, TCon) and s.body.name == 'Text'


# ---------------------------------------------------------------------------
# Local let
# ---------------------------------------------------------------------------

def test_local_let_monomorphic():
    s = ty_of('let foo = let yy = 42 in yy + 1', 'foo')
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'

def test_local_let_chain():
    s = ty_of('let foo = let yy = 1 in let zz = yy + 1 in zz', 'foo')
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'

def test_local_let_annotation():
    s = ty_of('let foo = let yy : Nat = 0 in yy', 'foo')
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'


# ---------------------------------------------------------------------------
# Tuples
# ---------------------------------------------------------------------------

def test_tuple_two():
    s = ty_of('let pair = (1, 2)', 'pair')
    assert isinstance(s.body, TTup) and len(s.body.elems) == 2

def test_tuple_mixed():
    s = ty_of('let pair = (1, "hi")', 'pair')
    assert isinstance(s.body, TTup)
    assert isinstance(s.body.elems[0], TCon) and s.body.elems[0].name == 'Nat'
    assert isinstance(s.body.elems[1], TCon) and s.body.elems[1].name == 'Text'

def test_tuple_three():
    s = ty_of('let triple = (1, 2, 3)', 'triple')
    assert isinstance(s.body, TTup) and len(s.body.elems) == 3


# ---------------------------------------------------------------------------
# Algebraic types and constructors
# ---------------------------------------------------------------------------

def test_con_none_registered():
    te = pipeline('type Option a = | None | Some a')
    assert 'Test.None' in te

def test_con_some_registered():
    te = pipeline('type Option a = | None | Some a')
    assert 'Test.Some' in te

def test_con_none_type():
    s = ty_of('type Option a = | None | Some a\nlet val = None', 'val')
    inner = s.body
    assert isinstance(inner, TApp)
    assert isinstance(inner.fun, TCon) and inner.fun.name == 'Test.Option'

def test_con_some_applied():
    s = ty_of('type Option a = | None | Some a\nlet val = Some 42', 'val')
    inner = s.body
    assert isinstance(inner, TApp)
    assert isinstance(inner.fun, TCon) and inner.fun.name == 'Test.Option'
    assert isinstance(inner.arg, TCon) and inner.arg.name == 'Nat'

def test_con_some_scheme():
    te = pipeline('type Option a = | None | Some a')
    s = te['Test.Some']
    assert len(s.vars) == 1
    assert isinstance(s.body, TArr)

def test_con_two_arg():
    te = pipeline('type Pair a b = | Pair a b')
    s = te['Test.Pair']
    assert len(s.vars) == 2
    assert isinstance(s.body, TArr)

def test_user_bool_constructors():
    te = pipeline('type MyBool = | MyTrue | MyFalse')
    assert 'Test.MyTrue' in te
    assert 'Test.MyFalse' in te


# ---------------------------------------------------------------------------
# match expressions
# ---------------------------------------------------------------------------

def test_match_nat_arms():
    src = (
        'type Option a = | None | Some a\n'
        'let fn1 = λ arg → match arg { | None → 0 | Some vv → vv }'
    )
    s = ty_of(src, 'fn1')
    assert isinstance(s.body, TArr)
    result_ty = s.body.cod
    assert isinstance(result_ty, TCon) and result_ty.name == 'Nat'

def test_match_bool_arms():
    src = (
        'let describe = λ bb → match bb {\n'
        '  | True → "yes"\n'
        '  | False → "no"\n'
        '}'
    )
    s = ty_of(src, 'describe')
    assert isinstance(s.body, TArr)
    assert isinstance(s.body.dom, TCon) and s.body.dom.name == 'Bool'
    assert isinstance(s.body.cod, TCon) and s.body.cod.name == 'Text'

def test_match_arms_must_agree():
    src = (
        'type Option a = | None | Some a\n'
        'let bad = λ arg → match arg { | None → 0 | Some vv → "oops" }'
    )
    check_error(src, "cannot unify")

def test_match_wildcard():
    src = 'let fn1 = λ nn → match nn { | 0 → True | _ → False }'
    s = ty_of(src, 'fn1')
    assert isinstance(s.body, TArr)
    assert isinstance(s.body.cod, TCon) and s.body.cod.name == 'Bool'

def test_match_tuple_pattern():
    src = (
        'let fst = λ pair → match pair {\n'
        '  | (aa, _) → aa\n'
        '}'
    )
    s = ty_of(src, 'fst')
    assert isinstance(s.body, TArr)


# ---------------------------------------------------------------------------
# Forward references
# ---------------------------------------------------------------------------

def test_forward_reference():
    src = 'let foo = bar\nlet bar = 42'
    te = pipeline(src)
    assert 'Test.foo' in te and 'Test.bar' in te

def test_mutual_dependency_annotated():
    """Mutually recursive functions with annotations type-check."""
    src = (
        'let even_fn : Nat → Bool = λ nn → if nn < 1 then True else odd_fn (nn - 1)\n'
        'let odd_fn  : Nat → Bool = λ nn → if nn < 1 then False else even_fn (nn - 1)\n'
    )
    te = pipeline(src)
    assert 'Test.even_fn' in te and 'Test.odd_fn' in te


# ---------------------------------------------------------------------------
# Top-level generalization
# ---------------------------------------------------------------------------

def test_top_level_id_polymorphic():
    s = ty_of('let id_fn = λ n → n', 'id_fn')
    assert len(s.vars) >= 1

def test_top_level_const_polymorphic():
    s = ty_of('let const_fn = λ n m → n', 'const_fn')
    assert len(s.vars) >= 2

def test_annotated_arrow_preserved():
    s = ty_of('let inc : Nat → Nat = λ n → n + 1', 'inc')
    assert isinstance(s.body, TArr)
    assert isinstance(s.body.dom, TCon) and s.body.dom.name == 'Nat'

def test_multiple_uses_share_scheme():
    """Using a top-level name twice at different types — only possible if it's ∀."""
    src = (
        'let id_fn = λ n → n\n'
        'let use1 = id_fn 42\n'
        'let use2 = id_fn "hi"\n'
    )
    te = pipeline(src)
    assert 'Test.use1' in te
    assert 'Test.use2' in te


# ---------------------------------------------------------------------------
# Unary operators
# ---------------------------------------------------------------------------

def test_unary_negate():
    s = ty_of('let val = -1', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'

def test_unary_not():
    s = ty_of('let val = ¬True', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Bool'

def test_unary_not_wrong_type():
    check_error('let val = ¬42', "cannot unify")


# ---------------------------------------------------------------------------
# Binary operators
# ---------------------------------------------------------------------------

def test_plus_nat():
    s = ty_of('let val = 1 + 2', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'

def test_minus_nat():
    s = ty_of('let val = 3 - 1', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'

def test_mul_nat():
    s = ty_of('let val = 3 * 4', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'

def test_lt_returns_bool():
    s = ty_of('let val = 1 < 2', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Bool'

def test_gt_returns_bool():
    s = ty_of('let val = 2 > 1', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Bool'

def test_neq_operator():
    s = ty_of('let val = 1 ≠ 2', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Bool'

def test_leq_returns_bool():
    s = ty_of('let val = 1 ≤ 2', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Bool'

def test_pipe_forward():
    s = ty_of('let val = 42 |> (λ n → n + 1)', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_unbound_variable():
    # Unbound names are caught by scope resolver (ScopeError)
    check_error('let val = no_such_name', "unbound name")

def test_type_mismatch_in_app():
    check_error('let val = 42 1', "cannot unify")

def test_type_mismatch_branch():
    src = (
        'let fn1 : Nat → Nat = λ nn → match nn {\n'
        '  | 0 → 0\n'
        '  | _ → "wrong"\n'
        '}'
    )
    check_error(src, "cannot unify")

def test_if_wrong_condition_type():
    check_error('let val = if 1 then 0 else 1', "cannot unify")


# ---------------------------------------------------------------------------
# External mod
# ---------------------------------------------------------------------------

def test_ext_value_registered():
    src = 'external mod Core.Nat { add : Nat → Nat → Nat }'
    te = pipeline(src)
    assert 'Core.Nat.add' in te

def test_ext_value_scheme():
    src = 'external mod Core.Nat { add : Nat → Nat → Nat }'
    te = pipeline(src)
    s = te['Core.Nat.add']
    assert isinstance(s.body, TArr)

def test_ext_value_used():
    src = (
        'external mod Core.Nat { add : Nat → Nat → Nat }\n'
        'let result = Core.Nat.add 1 2'
    )
    s = ty_of(src, 'result')
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'


# ---------------------------------------------------------------------------
# Class / instance basics
# ---------------------------------------------------------------------------

def test_class_method_registered():
    src = 'class Show a { show : a → Text }'
    te = pipeline(src)
    assert 'Test.show' in te

def test_class_method_scheme():
    src = 'class Show a { show : a → Text }'
    te = pipeline(src)
    s = te['Test.show']
    assert isinstance(s.body, TArr)

def test_instance_valid():
    src = (
        'class Show a { show : a → Text }\n'
        'type Foo = | Foo\n'
        'instance Show Foo { show = λ _ → "foo" }'
    )
    te = pipeline(src)
    assert 'Test.show' in te


# ---------------------------------------------------------------------------
# Pin expressions
# ---------------------------------------------------------------------------

def test_pin_expr_type():
    s = ty_of('let val = @res = 42 in res + 1', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'

def test_pin_expr_with_annotation():
    s = ty_of('let val = @res : Nat = 0 in res', 'val')
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'


# ---------------------------------------------------------------------------
# mod blocks
# ---------------------------------------------------------------------------

def test_mod_block_let_registered():
    te = pipeline('mod Inner { let value = 42 }')
    assert 'Test.Inner.value' in te

def test_mod_block_let_inferred():
    s = pipeline('mod Inner { let value = 42 }')['Test.Inner.value']
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'

def test_mod_block_type_registered():
    te = pipeline('mod Inner { type Pair = | MkPair Nat Nat }')
    assert 'Test.Inner.MkPair' in te


# ---------------------------------------------------------------------------
# M9.4 — fix expressions
# ---------------------------------------------------------------------------

def test_fix_infers_recursive_nat():
    """fix λ self n → ... : Nat → Nat"""
    src = '''
external mod Core.PLAN { inc : Nat → Nat }
let countdown : Nat → Nat
  = fix λ self n → match n { | 0 → 0 | k → self k }
'''
    s = ty_of(src, 'countdown')
    assert isinstance(s.body, TArr)
    assert isinstance(s.body.dom, TCon) and s.body.dom.name == 'Nat'
    assert isinstance(s.body.cod, TCon) and s.body.cod.name == 'Nat'

def test_fix_annotated_matches():
    """Type annotation on a fix expression is accepted."""
    src = '''
let repeat : Nat → Nat → Nat
  = fix λ self n m → match n { | 0 → 0 | k → m + self k m }
'''
    s = ty_of(src, 'repeat')
    assert isinstance(s.body, TArr)
    inner = s.body.cod
    assert isinstance(inner, TArr)
    assert isinstance(inner.dom, TCon) and inner.dom.name == 'Nat'
    assert isinstance(inner.cod, TCon) and inner.cod.name == 'Nat'

def test_fix_self_ref_type_unified():
    """The self-reference type is unified with the fix result type, not the lambda type."""
    src = 'let fn_id = fix λ self nn → nn'
    s = ty_of(src, 'fn_id')
    # fix λ self n → n : a → a  (polymorphic identity via recursion)
    # The lambda type is (a→a)→(a→a); fix of that is a→a.
    assert isinstance(s.body, TArr)
    dom = s.body.dom
    cod = s.body.cod
    assert isinstance(dom, TBound) and isinstance(cod, TBound)
    assert dom.name == cod.name

def test_fix_wrong_self_usage():
    """Applying self to a Bool when the recursive type is Nat→Nat is a type error."""
    src = '''
let bad_fn : Nat → Nat
  = fix λ self nn → self True
'''
    check_error(src, 'cannot unify')


# ---------------------------------------------------------------------------
# M9.4 — mutual recursion SCC ordering
# ---------------------------------------------------------------------------

def test_mutual_annotated():
    """Mutually recursive is_even / is_odd with explicit annotations type-check."""
    src = '''
type Bool2 = | Even | Odd
let is_even : Nat → Bool
  = λ n → match n { | 0 → True | k → is_odd k }
let is_odd : Nat → Bool
  = λ n → match n { | 0 → False | k → is_even k }
'''
    te = pipeline(src)
    for name in ('Test.is_even', 'Test.is_odd'):
        s = te[name]
        assert isinstance(s.body, TArr)
        assert isinstance(s.body.dom, TCon) and s.body.dom.name == 'Nat'
        assert isinstance(s.body.cod, TCon) and s.body.cod.name == 'Bool'

def test_mutual_unannotated_inferred():
    """Mutually recursive add/zero without annotations are inferred as Nat→Nat."""
    src = '''
let my_add = λ n m → match n { | 0 → m | k → Core.PLAN.inc (my_add k m) }
external mod Core.PLAN { inc : Nat → Nat }
'''
    te = pipeline(src)
    s = te['Test.my_add']
    assert isinstance(s.body, TArr)
    assert isinstance(s.body.dom, TCon) and s.body.dom.name == 'Nat'

def test_mutual_forward_ref():
    """A let that references a later let is resolved via the pre-pass."""
    src = '''
let use_val : Nat = base_val + 1
let base_val : Nat = 42
'''
    te = pipeline(src)
    s = te['Test.use_val']
    assert isinstance(s.body, TCon) and s.body.name == 'Nat'

def test_mutual_type_error_propagates():
    """A type error in one SCC member still raises TypecheckError."""
    src = '''
let fn1 : Bool → Nat = λ nn → fn2 nn
let fn2 : Nat → Nat = λ nn → nn + 1
'''
    check_error(src, 'cannot unify')


# ---------------------------------------------------------------------------
# Run as script
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    tests = [(name, obj) for name, obj in globals().items()
             if name.startswith('test_') and callable(obj)]
    passed = failed = 0
    for name, fn in sorted(tests):
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except (AssertionError, Exception) as exc:
            print(f"  FAIL  {name}: {exc}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
