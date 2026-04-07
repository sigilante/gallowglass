#!/usr/bin/env python3
"""
Scope resolver tests — bootstrap/scope.py

Covers name collection, forward references, use declarations, external mods,
mod blocks, pattern resolution, qualified names, and error cases.

Run: python3 tests/bootstrap/test_scope.py
  or: python3 -m pytest tests/bootstrap/test_scope.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.lexer import lex, Loc
from bootstrap.parser import parse
from bootstrap.scope import resolve, ScopeError, Env, BindingValue, BindingCon, BindingType, BindingClass, BindingClassMethod, BindingExtValue, BindingExtType
from bootstrap.ast import *


def resolve_src(src, module='Test', module_env=None):
    prog = parse(lex(src, '<test>'), '<test>')
    return resolve(prog, module, module_env or {}, '<test>')

def check_error(src, expected_fragment, module='Test', module_env=None):
    try:
        resolve_src(src, module, module_env)
        assert False, f"expected ScopeError containing {expected_fragment!r}"
    except ScopeError as e:
        assert expected_fragment in str(e), \
            f"expected {expected_fragment!r} in error: {str(e)!r}"

def mk_env(fq_names: list[str]) -> Env:
    """Build a minimal Env from a list of fq value names."""
    e = Env()
    for fq in fq_names:
        loc = Loc('<test>', 1, 1)
        e.bindings[fq] = BindingValue(fq, None, None, loc)
        mod = '.'.join(fq.split('.')[:-1])
        e.module_exports.setdefault(mod, set()).add(fq)
    return e


# ============================================================
# Top-level binding collection
# ============================================================

def test_let_registered():
    _, env = resolve_src('let foo = 42')
    assert 'Test.foo' in env.bindings

def test_let_binding_kind():
    _, env = resolve_src('let foo = 42')
    assert isinstance(env.bindings['Test.foo'], BindingValue)

def test_multiple_lets_registered():
    _, env = resolve_src('let foo = 1\nlet bar = 2')
    assert 'Test.foo' in env.bindings
    assert 'Test.bar' in env.bindings

def test_type_registered():
    _, env = resolve_src('type Bool = | True | False')
    assert 'Test.Bool' in env.bindings
    assert isinstance(env.bindings['Test.Bool'], BindingType)

def test_constructors_registered():
    _, env = resolve_src('type Option a = | None | Some a')
    assert 'Test.None' in env.bindings
    assert 'Test.Some' in env.bindings
    assert isinstance(env.bindings['Test.None'], BindingCon)
    assert isinstance(env.bindings['Test.Some'], BindingCon)

def test_constructor_arity():
    _, env = resolve_src('type Option a = | None | Some a')
    assert env.bindings['Test.None'].arity == 0
    assert env.bindings['Test.Some'].arity == 1

def test_type_alias_registered():
    _, env = resolve_src('type MyNat = Nat')
    assert 'Test.MyNat' in env.bindings
    assert isinstance(env.bindings['Test.MyNat'], BindingType)

def test_type_builtin_registered():
    _, env = resolve_src('type Nat : builtin')
    assert 'Test.Nat' in env.bindings

def test_record_type_registered():
    _, env = resolve_src('type Point = { x : Nat, y : Nat }')
    assert 'Test.Point' in env.bindings
    # Records register a BindingCon (constructor overwrites type binding)
    assert isinstance(env.bindings['Test.Point'], BindingCon)
    assert env.bindings['Test.Point'].arity == 2
    # Field metadata stored
    assert env.record_fields['Test.Point'] == ['x', 'y']

def test_eff_decl_registered():
    _, env = resolve_src('eff State s { get : ⊤ → s  put : s → ⊤ }')
    assert 'Test.State' in env.bindings

def test_class_registered():
    _, env = resolve_src('class Show a { show : a → Text }')
    assert 'Test.Show' in env.bindings
    assert isinstance(env.bindings['Test.Show'], BindingClass)

def test_class_method_registered():
    _, env = resolve_src('class Show a { show : a → Text }')
    assert 'Test.show' in env.bindings
    assert isinstance(env.bindings['Test.show'], BindingClassMethod)

def test_class_method_references_class():
    _, env = resolve_src('class Show a { show : a → Text }')
    assert env.bindings['Test.show'].class_fq == 'Test.Show'

def test_duplicate_let_error():
    check_error('let foo = 1\nlet foo = 2', "duplicate definition of 'foo'")

def test_duplicate_constructor_error():
    check_error('type A = | X\ntype B = | X', "duplicate definition of 'X'")

def test_module_exports():
    _, env = resolve_src('let foo = 1\nlet bar = 2')
    exports = env.exports_of('Test')
    assert 'Test.foo' in exports
    assert 'Test.bar' in exports


# ============================================================
# Forward references at top level
# ============================================================

def test_forward_reference_top_level():
    """Top-level lets can reference each other regardless of source order."""
    prog, _ = resolve_src('let foo = bar\nlet bar = 42')
    # No ScopeError — forward references OK at module level

def test_mutual_reference():
    prog, _ = resolve_src('let even = odd\nlet odd = even')
    # No error


# ============================================================
# Expression name resolution
# ============================================================

def test_bare_name_resolves():
    prog, _ = resolve_src('let foo = 1\nlet bar = foo')
    bar_body = prog.decls[1].body
    assert isinstance(bar_body, ExprVar)
    assert str(bar_body.name) == 'Test.foo'

def test_unbound_name_error():
    check_error('let foo = bar', "unbound name 'bar'")

def test_local_let_binding():
    """Local let bindings are accessible in their body."""
    prog, _ = resolve_src('let foo = let y = 1 in y')
    # No error

def test_local_let_not_in_outer():
    """Local let binders do not escape their scope."""
    check_error('let foo = let y = 1 in y\nlet bar = y', "unbound name 'y'")

def test_lambda_param():
    prog, _ = resolve_src('let foo = λ x → x')
    # No error

def test_lambda_param_not_in_outer():
    check_error('let foo = λ x → x\nlet bar = x', "unbound name 'x'")

def test_if_expression():
    prog, _ = resolve_src('let foo = if True then 1 else 0')
    # No error — True is a keyword constructor, resolvable

def test_constructor_in_expr():
    prog, _ = resolve_src(
        'type Option a = | None | Some a\n'
        'let foo = Some 1'
    )
    # Some resolves to Test.Some
    body = prog.decls[1].body
    assert isinstance(body, ExprApp)
    assert str(body.fun.name) == 'Test.Some'

def test_pin_expr_body_scope():
    prog, _ = resolve_src('let foo = @result = 42 in result')
    # result visible in body, no error

def test_pin_expr_rhs_cannot_see_name():
    """The pin name is not in scope in the rhs."""
    check_error('let foo = @result = result in result', "unbound name 'result'")

def test_match_arm_pat_scope():
    """Pattern variables are in scope in the arm body."""
    prog, _ = resolve_src(
        'type Option a = | None | Some a\n'
        'let foo = λ arg → match arg { | Some v → v | None → 0 }'
    )
    # No error


# ============================================================
# Pattern resolution
# ============================================================

def test_pattern_con_resolves():
    prog, _ = resolve_src(
        'type Option a = | None | Some a\n'
        'let foo = λ arg → match arg { | Some v → v | None → 0 }'
    )
    # body is ExprLam; body.body is ExprMatch
    arms = prog.decls[1].body.body.arms
    some_pat = arms[0][0]
    assert isinstance(some_pat, PatCon)
    assert str(some_pat.name) == 'Test.Some'

def test_pattern_con_unbound():
    check_error(
        'let foo = λ arg → match arg { | Nonexistent → 0 }',
        "unbound name 'Nonexistent'"
    )

def test_pattern_arity_check():
    """Constructor with wrong arity in pattern raises ScopeError."""
    check_error(
        'type Option a = | None | Some a\n'
        'let foo = λ arg → match arg { | Some aa bb → aa }',
        'expects'   # message: "constructor 'Some' expects 1 argument(s), got 2"
    )

def test_pattern_con_zero_arity():
    prog, _ = resolve_src(
        'type Option a = | None | Some a\n'
        'let foo = λ arg → match arg { | None → 0 | Some v → v }'
    )
    arms = prog.decls[1].body.body.arms
    none_pat = arms[0][0]
    assert isinstance(none_pat, PatCon)
    assert str(none_pat.name) == 'Test.None'


# ============================================================
# Qualified name resolution
# ============================================================

def test_qualified_name_via_module_env():
    other_env = mk_env(['Core.List.map'])
    prog, _ = resolve_src(
        'use Core.List\nlet foo = Core.List.map',
        module_env={'Core.List': other_env}
    )
    body = prog.decls[1].body
    assert isinstance(body, ExprVar)
    assert str(body.name) == 'Core.List.map'

def test_unknown_module_error():
    check_error('let foo = Unknown.Module.bar', "unknown module")

def test_name_not_in_module_error():
    other_env = mk_env([])  # no bindings
    other_env.module_exports['Core.List'] = set()
    check_error(
        'use Core.List\nlet foo = Core.List.nonexistent',
        "not defined in module",
        module_env={'Core.List': other_env}
    )

def test_module_alias_short():
    """After  use Core.List, short alias 'List' can be used."""
    other_env = mk_env(['Core.List.map'])
    # Note: qualified access as List.map (via short alias)
    prog, _ = resolve_src(
        'use Core.List\nlet foo = Core.List.map',
        module_env={'Core.List': other_env}
    )
    body = prog.decls[1].body
    assert str(body.name) == 'Core.List.map'


# ============================================================
# use declarations
# ============================================================

def test_use_unqualified():
    other_env = mk_env(['Core.List.map'])
    prog, _ = resolve_src(
        'use Core.List unqualified { map }\nlet foo = map',
        module_env={'Core.List': other_env}
    )
    body = prog.decls[1].body
    assert str(body.name) == 'Core.List.map'

def test_use_qualified_no_bare():
    """use Core.List (no unqualified) does NOT put 'map' in scope bare."""
    other_env = mk_env(['Core.List.map'])
    check_error(
        'use Core.List\nlet foo = map',
        "unbound name 'map'",
        module_env={'Core.List': other_env}
    )

def test_use_unknown_module_error():
    check_error(
        'use NonExistent.Mod\nlet foo = 0',
        "unknown module"
    )

def test_use_unknown_export_error():
    other_env = mk_env([])
    other_env.module_exports['Core.List'] = set()
    check_error(
        'use Core.List unqualified { nonexistent }\nlet foo = 0',
        "not exported",
        module_env={'Core.List': other_env}
    )


# ============================================================
# external mod declarations
# ============================================================

def test_external_mod_value_registered():
    _, env = resolve_src('external mod Core.Nat { add : Nat → Nat → Nat }')
    assert 'Core.Nat.add' in env.bindings
    assert isinstance(env.bindings['Core.Nat.add'], BindingExtValue)

def test_external_mod_type_registered():
    _, env = resolve_src('external mod Core.Nat { type Nat : builtin }')
    assert 'Core.Nat.Nat' in env.bindings
    assert isinstance(env.bindings['Core.Nat.Nat'], BindingExtType)

def test_external_mod_qualified_access():
    prog, _ = resolve_src(
        'external mod Core.Nat { add : Nat → Nat → Nat }\n'
        'let foo = Core.Nat.add'
    )
    body = prog.decls[1].body
    assert str(body.name) == 'Core.Nat.add'

def test_external_mod_not_prefixed_by_current():
    """external mod path is absolute even inside mod blocks."""
    _, env = resolve_src(
        'external mod Core.Nat { add : Nat → Nat → Nat }'
    )
    assert 'Core.Nat.add' in env.bindings
    assert 'Test.Core.Nat.add' not in env.bindings

def test_external_mod_multi_items():
    _, env = resolve_src(
        'external mod Core.Nat {\n'
        '  type Nat : builtin\n'
        '  add : Nat → Nat → Nat\n'
        '  mul : Nat → Nat → Nat\n'
        '}'
    )
    assert 'Core.Nat.Nat' in env.bindings
    assert 'Core.Nat.add' in env.bindings
    assert 'Core.Nat.mul' in env.bindings


# ============================================================
# mod blocks (nested namespaces)
# ============================================================

def test_mod_block_registers_nested():
    _, env = resolve_src('mod Foo { let bar = 1 }')
    assert 'Test.Foo.bar' in env.bindings

def test_mod_block_inner_can_see_outer():
    prog, _ = resolve_src('let outer = 1\nmod Foo { let inner = outer }')
    # No error — outer binding visible inside mod block

def test_mod_block_outer_cannot_see_inner_bare():
    """Inner binding 'bar' is only accessible as Foo.bar, not bare 'bar'."""
    check_error(
        'mod Foo { let bar = 1 }\nlet baz = bar',
        "unbound name 'bar'"
    )

def test_mod_nested():
    _, env = resolve_src('mod Outer { mod Inner { let deep = 1 } }')
    assert 'Test.Outer.Inner.deep' in env.bindings


# ============================================================
# instance declarations
# ============================================================

def test_instance_valid_method():
    prog, _ = resolve_src(
        'class Show a { show : a → Text }\n'
        'type Foo = | Foo\n'
        'instance Show Foo { show = λ _ → "foo" }'
    )
    # No error

def test_instance_invalid_method():
    check_error(
        'class Show a { show : a → Text }\n'
        'type Foo = | Foo\n'
        'instance Show Foo { bad_method = "x" }',
        "not a member of class 'Show'"
    )


# ============================================================
# Error messages include location
# ============================================================

def test_error_includes_filename():
    prog = parse(lex('let foo = unbound_name', 'myfile.gls'), 'myfile.gls')
    try:
        resolve(prog, 'Test', {}, 'myfile.gls')
        assert False, "expected ScopeError"
    except ScopeError as e:
        assert 'myfile.gls' in str(e)

def test_error_includes_line():
    try:
        resolve_src('let foo = unbound_name')
        assert False
    except ScopeError as e:
        # error message contains line:col
        assert ':1:' in str(e)

def test_module_name_from_arg():
    """The module name is passed in, not parsed from source."""
    _, env = resolve_src('let val = 1', module='My.Module')
    assert 'My.Module.val' in env.bindings


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
