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
    TCon, TArr, TApp, TTup, TBound, TMeta, TRow, TComp, Scheme,
    pp_type, pp_scheme,
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
# M10.1 — Effect row types and handler checking
# ---------------------------------------------------------------------------

# -- TRow and TComp construction helpers -----------------------------------

from bootstrap.typecheck import TRow, TComp  # noqa: E402


def test_eff_decl_registers_ops():
    """DeclEff registers each operation in type_env with a TArr(A, TComp) scheme."""
    src = '''
eff Counter {
  tick : Nat → Nat
}
let dummy : Nat = 0
'''
    te = pipeline(src)
    assert 'Test.Counter.tick' in te
    op_scheme = te['Test.Counter.tick']
    # tick : Nat → {Counter | r} Nat
    assert isinstance(op_scheme.body, TArr)
    assert isinstance(op_scheme.body.dom, TCon) and op_scheme.body.dom.name == 'Nat'
    cod = op_scheme.body.cod
    assert isinstance(cod, TComp)
    assert 'Counter' in cod.row.effects
    assert isinstance(cod.ty, TCon) and cod.ty.name == 'Nat'

def test_eff_decl_nullary_op():
    """Nullary effect op registers with ⊤ as the argument type."""
    src = '''
eff Ticker {
  tick : ⊤ → ⊤
}
let dummy : Nat = 0
'''
    te = pipeline(src)
    assert 'Test.Ticker.tick' in te

def test_eff_decl_poly_param():
    """Effect with a type parameter: State s."""
    src = '''
eff State s {
  get : ⊤ → s
  put : s → ⊤
}
let dummy : Nat = 0
'''
    te = pipeline(src)
    assert 'Test.State.get' in te
    assert 'Test.State.put' in te
    get_scheme = te['Test.State.get']
    # get : ∀ s r. ⊤ → {State s | r} s
    assert 's' in get_scheme.vars
    assert 'r' in get_scheme.vars

def test_ast_to_mono_tyeffect_builds_tcomp():
    """A type annotation {IO} Nat compiles to TComp(TRow({'IO':[]}, None), Nat)."""
    src = 'let fn1 : Nat → {IO} Nat = λ nn → nn'
    # Should type-check without error (permissive: effects not enforced at call site)
    te = pipeline(src)
    assert 'Test.fn1' in te
    s = te['Test.fn1']
    assert isinstance(s.body, TArr)
    cod = s.body.cod
    assert isinstance(cod, TComp)
    assert 'IO' in cod.row.effects

def test_trow_open_unifies_with_closed():
    """{IO | r} unifies with {IO} (r constrained to empty)."""
    src = '''
let use_io : Nat → {IO} Nat = λ nn → nn
let wrapper : Nat → {IO | r} Nat = use_io
'''
    te = pipeline(src)
    assert 'Test.wrapper' in te

def test_trow_closed_mismatch_error():
    """Closed row {IO} vs closed row {State s} is a type error."""
    src = '''
eff State s { get : ⊤ → s }
let bad : Nat → {IO} Nat = λ nn → nn
let also_bad : Nat → {State Nat} Nat = bad
'''
    check_error(src, 'closed effect row missing')

def test_handle_return_arm_only():
    """A handle with only a return arm type-checks."""
    src = '''
eff Noop {
  noop : ⊤ → ⊤
}
let run_noop : Nat → Nat
  = λ nn → handle nn {
      | return xx → xx
    }
'''
    te = pipeline(src)
    s = te['Test.run_noop']
    assert isinstance(s.body, TArr)
    assert isinstance(s.body.dom, TCon) and s.body.dom.name == 'Nat'

def test_handle_op_arm_binds_continuation():
    """An op arm's continuation k is bound and its body type-checks."""
    src = '''
eff Counter {
  tick : Nat → Nat
}
let step : Nat → Nat
  = λ nn → handle nn {
      | return xx  → xx
      | tick vv kk → kk vv
    }
'''
    te = pipeline(src)
    assert 'Test.step' in te

def test_handle_return_type_mismatch():
    """If the return arm returns Bool but the op arm returns Nat, error."""
    src = '''
eff Counter {
  tick : Nat → Nat
}
let bad_handler : Nat → Nat
  = λ nn → handle nn {
      | return xx  → True
      | tick vv kk → kk vv
    }
'''
    check_error(src, 'cannot unify')

def test_row_var_generalized():
    """A function with an open row variable in its type gets it generalized."""
    src = 'let passthru : Nat → {IO | r} Nat = λ nn → nn'
    te = pipeline(src)
    s = te['Test.passthru']
    assert isinstance(s.body, TArr)
    cod = s.body.cod
    assert isinstance(cod, TComp)
    assert cod.row.tail is not None   # open row — tail not None


# ---------------------------------------------------------------------------
# pp_type / pp_scheme — standalone type pretty-printing
# ---------------------------------------------------------------------------

def test_pp_type_tcon():
    assert pp_type(TCon('Nat')) == 'Nat'
    assert pp_type(TCon('Bool')) == 'Bool'
    assert pp_type(TCon('Text')) == 'Text'


def test_pp_type_tarr():
    # Nat → Nat
    assert pp_type(TArr(TCon('Nat'), TCon('Nat'))) == 'Nat → Nat'
    # (Nat → Nat) → Nat — left-arg needs parens
    assert pp_type(TArr(TArr(TCon('Nat'), TCon('Nat')), TCon('Nat'))) == '(Nat → Nat) → Nat'
    # Nat → Nat → Nat — right-associative, no parens
    assert pp_type(TArr(TCon('Nat'), TArr(TCon('Nat'), TCon('Nat')))) == 'Nat → Nat → Nat'


def test_pp_type_tapp():
    # List Nat
    assert pp_type(TApp(TCon('List'), TCon('Nat'))) == 'List Nat'
    # Result Text Nat
    assert pp_type(TApp(TApp(TCon('Result'), TCon('Text')), TCon('Nat'))) == 'Result Text Nat'
    # List (List Nat) — nested app needs parens
    assert pp_type(TApp(TCon('List'), TApp(TCon('List'), TCon('Nat')))) == 'List (List Nat)'


def test_pp_type_tvar():
    assert pp_type(TBound('a')) == 'a'
    assert pp_type(TBound('b')) == 'b'


def test_pp_type_ttup():
    assert pp_type(TTup([TCon('Nat'), TCon('Bool')])) == '(Nat, Bool)'
    assert pp_type(TTup([TCon('Nat'), TCon('Bool'), TCon('Text')])) == '(Nat, Bool, Text)'


def test_pp_type_effect_row():
    # {IO} Nat
    row = TRow({'IO': []}, None)
    assert pp_type(TComp(row, TCon('Nat'))) == '{IO} Nat'
    # {IO | r} — open row with tail
    tail = TBound('r')
    row_open = TRow({'IO': []}, tail)
    assert pp_type(TComp(row_open, TCon('Nat'))) == '{IO | r} Nat'
    # {Exn Text, IO} — multiple effects, sorted
    row_multi = TRow({'IO': [], 'Exn': [TCon('Text')]}, None)
    assert pp_type(TComp(row_multi, TCon('Nat'))) == '{Exn Text, IO} Nat'


def test_pp_scheme_no_vars():
    s = Scheme([], TArr(TCon('Nat'), TCon('Nat')))
    assert pp_scheme(s) == 'Nat → Nat'


def test_pp_scheme_with_vars():
    s = Scheme(['a'], TArr(TBound('a'), TBound('a')))
    assert pp_scheme(s) == '∀ a. a → a'


def test_pp_scheme_multi_vars():
    s = Scheme(['a', 'b'], TArr(TBound('a'), TArr(TBound('b'),
        TApp(TApp(TCon('Pair'), TBound('a')), TBound('b')))))
    assert pp_scheme(s) == '∀ a b. a → b → Pair a b'


def test_pp_type_arrow_in_app_arg():
    """List (Nat → Nat) — arrow type as app arg needs parens."""
    assert pp_type(TApp(TCon('List'), TArr(TCon('Nat'), TCon('Nat')))) == 'List (Nat → Nat)'


# ---------------------------------------------------------------------------
# pp_scheme with constraints
# ---------------------------------------------------------------------------

def test_pp_scheme_single_constraint():
    s = Scheme(['a'], TArr(TBound('a'), TArr(TBound('a'), TCon('Bool'))),
               constraints=[('Eq', [TBound('a')])])
    assert pp_scheme(s) == '∀ a. Eq a ⇒ a → a → Bool'


def test_pp_scheme_multiple_constraints():
    s = Scheme(['a'], TArr(TBound('a'), TCon('Text')),
               constraints=[('Eq', [TBound('a')]), ('Show', [TBound('a')])])
    assert pp_scheme(s) == '∀ a. (Eq a, Show a) ⇒ a → Text'


def test_pp_scheme_no_constraints():
    """pp_scheme with empty constraints = same as before."""
    s = Scheme(['a'], TArr(TBound('a'), TBound('a')), constraints=[])
    assert pp_scheme(s) == '∀ a. a → a'


def test_scheme_preserves_constraints():
    """Scheme dataclass preserves constraint field."""
    s = Scheme(['a'], TBound('a'), [('Eq', [TBound('a')])])
    assert len(s.constraints) == 1
    assert s.constraints[0][0] == 'Eq'


def test_pp_scheme_constraint_no_vars():
    """Monomorphic constrained type."""
    s = Scheme([], TArr(TCon('Nat'), TCon('Bool')),
               constraints=[('Eq', [TCon('Nat')])])
    assert pp_scheme(s) == 'Eq Nat ⇒ Nat → Bool'


# ---------------------------------------------------------------------------
# AUDIT.md B5: documented-but-not-enforced effect-system invariants
#
# CLAUDE.md states two invariants the type checker should eventually
# enforce:
#
#   1. `Abort` is NOT in any effect row.  It is unhandleable and propagates
#      to the VM's virtualization supervisor.
#   2. `External` marks VM boundary crossings — any function calling a
#      `Core.PLAN.*` op (or other `external mod` op) must carry `External`
#      in its effect row.  `spec/05-type-system.md` defines E0011 for the
#      missing-External diagnostic.
#
# Neither is enforced today; `bootstrap/typecheck.py` is permissive about
# effects in non-handle positions.  These two tests are `xfail(strict=True)`
# regression gates: each test body asserts the *correct-future* behaviour
# (`TypecheckError` with the relevant fragment in the message).  Today
# they correctly xfail because the type checker silently accepts the
# program, so `_expect_typecheck_error` raises `AssertionError`.  When
# enforcement lands, both tests will start raising `TypecheckError` as
# asserted, the `strict=True` xfail will turn into an `XPASS` failure,
# and someone removes the `xfail` marker — at which point B5 is fully
# closed.
#
# If you implement enforcement, drop the markers and adjust the assertion
# fragments.  Do NOT delete the tests — they are the spec gate.
# ---------------------------------------------------------------------------

import pytest


def _expect_typecheck_error(src: str, fragment: str = '', module: str = 'M'):
    """Compile & typecheck `src`; assert TypecheckError whose message
    contains `fragment`.  Used by the B5 xfail gates below."""
    prog = parse(lex(src, '<b5>'), '<b5>')
    resolved, env = resolve(prog, module, {}, '<b5>')
    try:
        typecheck(resolved, env, module, '<b5>')
    except TypecheckError as e:
        if fragment:
            assert fragment in str(e), \
                f"expected {fragment!r} in error: {str(e)!r}"
        return
    raise AssertionError(
        f"expected TypecheckError; the type checker silently accepted:\n{src}"
    )


@pytest.mark.xfail(
    strict=True,
    reason='B5: Abort-in-effect-row enforcement not yet implemented '
           '(see CLAUDE.md effect-system bullet, AUDIT.md B5)',
)
def test_b5_abort_in_effect_row_is_rejected():
    """`Abort` must never appear in an effect row.  CLAUDE.md gospel."""
    src = '''
eff Bomb { boom : Nat → Nat }
let bomb : {Bomb, Abort | r} Nat = 42
let main : Nat = bomb
'''
    _expect_typecheck_error(src, 'Abort')


@pytest.mark.xfail(
    strict=True,
    reason='B5: missing-External enforcement (E0011) not yet implemented '
           '(see spec/05-type-system.md E0011, AUDIT.md B5)',
)
def test_b5_missing_external_is_rejected():
    """A function calling a `Core.PLAN.*` op must carry `External` in its
    effect row.  `spec/05-type-system.md` defines this as error E0011."""
    src = '''
external mod Core.PLAN { inc : Nat → Nat }
let wrap : Nat → Nat = λ n → inc n
let main : Nat = wrap 5
'''
    _expect_typecheck_error(src, 'External')


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
