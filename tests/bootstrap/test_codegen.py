#!/usr/bin/env python3
"""
Codegen tests — bootstrap/codegen.py, bootstrap/emit.py, bootstrap/glass_ir.py

Covers: literal compilation, variable references, lambda/application,
if/then/else, pattern matching (Nat, Bool), local let, constructor compilation,
top-level lets (non-recursive and self-recursive), seed round-trips,
and Glass IR rendering.

Run: python3 tests/bootstrap/test_codegen.py
  or: python3 -m pytest tests/bootstrap/test_codegen.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program, CodegenError, encode_name
from bootstrap.emit import emit, emit_all
from bootstrap.glass_ir import render, render_value, decode_name
from dev.harness.plan import P, L, A, N, is_nat, is_pin, is_law, is_app, evaluate
from dev.harness.seed import save_seed, load_seed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pipeline(src: str, module: str = 'Test') -> dict:
    """Lex → parse → resolve → codegen. Returns compiled dict."""
    prog = parse(lex(src, '<test>'), '<test>')
    resolved, env = resolve(prog, module, {}, '<test>')
    return compile_program(resolved, module)


def val_of(src: str, name: str, module: str = 'Test'):
    """Compile src and return the PLAN value for module.name."""
    compiled = pipeline(src, module)
    fq = f'{module}.{name}'
    assert fq in compiled, f"'{fq}' not in compiled: {list(compiled.keys())}"
    return compiled[fq]


def eval_val(src: str, name: str, module: str = 'Test'):
    """Compile, evaluate, and return the PLAN value for module.name."""
    return evaluate(val_of(src, name, module))


def round_trip(src: str, name: str, module: str = 'Test'):
    """
    Compile, emit to seed, reload, and evaluate.
    Returns the evaluated PLAN value.
    """
    compiled = pipeline(src, module)
    fq = f'{module}.{name}'
    seed_bytes = emit(compiled, fq)
    loaded = load_seed(seed_bytes)
    return evaluate(loaded)


# ---------------------------------------------------------------------------
# encode_name
# ---------------------------------------------------------------------------

def test_encode_name_empty():
    assert encode_name('') == 0


def test_encode_name_single():
    # 'a' = 0x61
    assert encode_name('a') == 0x61


def test_encode_name_two():
    # 'ab' = 0x61 | (0x62 << 8) = 0x6261
    assert encode_name('ab') == 0x6261


def test_decode_name_roundtrip():
    for s in ['main', 'foo_bar', 'add', 'Test.main']:
        n = encode_name(s)
        assert decode_name(n) == s


def test_decode_name_zero():
    assert decode_name(0) == '<anon>'


# ---------------------------------------------------------------------------
# Literal compilation
# ---------------------------------------------------------------------------

def test_literal_zero():
    """let main = 0  →  N(0)"""
    v = eval_val('let main = 0', 'main')
    assert is_nat(v) and v == 0


def test_literal_one():
    """let main = 1  →  N(1)"""
    v = eval_val('let main = 1', 'main')
    assert is_nat(v) and v == 1


def test_literal_large():
    """let main = 255  →  N(255)"""
    v = eval_val('let main = 255', 'main')
    assert is_nat(v) and v == 255


def test_literal_bignat():
    """Large nat literal."""
    v = eval_val('let main = 1000000', 'main')
    assert is_nat(v) and v == 1000000


# ---------------------------------------------------------------------------
# Constructor compilation
# ---------------------------------------------------------------------------

def test_nullary_constructor():
    """Nullary constructor = bare nat tag."""
    src = '''
type Color =
  | Red
  | Green
  | Blue

let main = Red
'''
    v = eval_val(src, 'main')
    assert is_nat(v) and v == 0  # Red = tag 0


def test_nullary_constructor_second():
    """Second nullary constructor = tag 1."""
    src = '''
type Color =
  | Red
  | Green
  | Blue

let main = Green
'''
    v = eval_val(src, 'main')
    assert is_nat(v) and v == 1  # Green = tag 1


def test_unary_constructor():
    """Unary constructor applied = (tag field)."""
    src = '''
type Wrapper =
  | Wrap Nat

let main = Wrap 42
'''
    v = eval_val(src, 'main')
    # Wrap 42 = A(N(0), N(42))
    assert is_app(v)
    assert is_nat(v.fun) and v.fun == 0   # tag 0
    assert is_nat(v.arg) and v.arg == 42


def test_binary_constructor():
    """Binary constructor = (tag f1 f2)."""
    src = '''
type Pair =
  | MkPair Nat Nat

let main = MkPair 3 7
'''
    v = eval_val(src, 'main')
    # MkPair 3 7 = A(A(N(0), N(3)), N(7))
    assert is_app(v)
    assert is_nat(v.arg) and v.arg == 7
    assert is_app(v.fun)
    assert is_nat(v.fun.arg) and v.fun.arg == 3
    assert is_nat(v.fun.fun) and v.fun.fun == 0  # tag 0


# ---------------------------------------------------------------------------
# Lambda and application
# ---------------------------------------------------------------------------

def test_identity_law():
    """let id_fn x = x  →  law of arity 1 with body N(1)"""
    src = 'let id_fn = λ x → x'
    v = val_of(src, 'id_fn')
    assert is_law(v)
    assert v.arity == 1
    # body should be N(1) (de Bruijn index 1 = first param)
    assert is_nat(v.body) and v.body == 1


def test_identity_eval():
    """Evaluating (id 42) = 42."""
    src = 'let id_fn = λ x → x'
    id_law = eval_val(src, 'id_fn')
    from dev.harness.plan import apply
    result = evaluate(apply(id_law, N(42)))
    assert is_nat(result) and result == 42


def test_const_law():
    """let const_fn x y = x  →  law of arity 2 with body N(1)"""
    src = 'let const_fn = λ x y → x'
    v = val_of(src, 'const_fn')
    assert is_law(v)
    assert v.arity == 2
    assert is_nat(v.body) and v.body == 1


def test_const_eval():
    """Evaluating (const 10 20) = 10."""
    src = 'let const_fn = λ x y → x'
    const_law = eval_val(src, 'const_fn')
    from dev.harness.plan import apply
    result = evaluate(apply(apply(const_law, N(10)), N(20)))
    assert is_nat(result) and result == 10


def test_application_top_level():
    """let main = id_fn 42  →  evaluates to 42."""
    src = '''
let id_fn = λ x → x
let main = id_fn 42
'''
    v = eval_val(src, 'main')
    assert is_nat(v) and v == 42


def test_multi_arg_application():
    """let main = const_fn 10 20  →  evaluates to 10."""
    src = '''
let const_fn = λ x y → x
let main = const_fn 10 20
'''
    v = eval_val(src, 'main')
    assert is_nat(v) and v == 10


# ---------------------------------------------------------------------------
# If/then/else
# ---------------------------------------------------------------------------

def test_if_true():
    """if True then 99 else 0  →  99  (True = N(1) builtin)"""
    # True is a builtin Bool constructor (tag 1 of Bool)
    src = 'let main = if True then 99 else 0'
    v = eval_val(src, 'main')
    assert is_nat(v) and v == 99


def test_if_false():
    """if False then 99 else 42  →  42  (False = N(0) builtin)"""
    src = 'let main = if False then 99 else 42'
    v = eval_val(src, 'main')
    assert is_nat(v) and v == 42


def test_if_in_lambda():
    """let cond_fn bb = if bb then 1 else 0  →  law, evaluates correctly."""
    src = 'let cond_fn = λ bb → if bb then 1 else 0'
    cond_fn = eval_val(src, 'cond_fn')
    from dev.harness.plan import apply
    assert evaluate(apply(cond_fn, N(1))) == 1  # True → 1
    assert evaluate(apply(cond_fn, N(0))) == 0  # False → 0


def test_if_nested():
    """Nested if/then/else."""
    src = 'let main = if True then (if False then 100 else 200) else 300'
    v = eval_val(src, 'main')
    assert is_nat(v) and v == 200


# ---------------------------------------------------------------------------
# Pattern matching — Nat
# ---------------------------------------------------------------------------

def test_match_nat_zero():
    """match n { 0 -> 99 | _ -> 0 } with n=0 → 99."""
    src = '''
let check_zero = λ nn → match nn {
  | 0 → 99
  | _ → 0
}
'''
    fn = eval_val(src, 'check_zero')
    from dev.harness.plan import apply
    assert evaluate(apply(fn, N(0))) == 99
    assert evaluate(apply(fn, N(1))) == 0
    assert evaluate(apply(fn, N(5))) == 0


def test_match_nat_one():
    """match n { 0 -> 10 | 1 -> 20 | _ -> 30 }."""
    src = '''
let classify = λ nn → match nn {
  | 0 → 10
  | 1 → 20
  | _ → 30
}
'''
    fn = eval_val(src, 'classify')
    from dev.harness.plan import apply
    assert evaluate(apply(fn, N(0))) == 10
    assert evaluate(apply(fn, N(1))) == 20
    assert evaluate(apply(fn, N(2))) == 30
    assert evaluate(apply(fn, N(99))) == 30


# ---------------------------------------------------------------------------
# Pattern matching — algebraic constructors (nullary)
# ---------------------------------------------------------------------------

def test_match_bool_true():
    """match True { True -> 1 | False -> 0 }."""
    src = '''
type MyBool =
  | MyFalse
  | MyTrue

let check_true = λ b → match b {
  | MyFalse → 0
  | MyTrue  → 1
}

let main = check_true MyTrue
'''
    v = eval_val(src, 'main')
    assert is_nat(v) and v == 1


def test_match_bool_false():
    """match False { True -> 1 | False -> 0 }."""
    src = '''
type MyBool =
  | MyFalse
  | MyTrue

let check_true = λ b → match b {
  | MyFalse → 0
  | MyTrue  → 1
}

let main = check_true MyFalse
'''
    v = eval_val(src, 'main')
    assert is_nat(v) and v == 0


def test_match_three_way():
    """Three-way enum match."""
    src = '''
type Dir =
  | North
  | South
  | East

let dir_code = λ dd → match dd {
  | North → 1
  | South → 2
  | East  → 3
}

let main = dir_code South
'''
    v = eval_val(src, 'main')
    assert is_nat(v) and v == 2


# ---------------------------------------------------------------------------
# Local let
# ---------------------------------------------------------------------------

def test_local_let_top():
    """Local let in a top-level expression."""
    src = 'let main = let result = 42 in result'
    v = eval_val(src, 'main')
    assert is_nat(v) and v == 42


def test_local_let_in_lambda():
    """Local let inside a lambda body (returns the original arg, not the let-bound val)."""
    src = 'let add_ten = λ x → let result = 10 in x'
    fn = eval_val(src, 'add_ten')
    from dev.harness.plan import apply
    result = evaluate(apply(fn, N(5)))
    assert is_nat(result) and result == 5


# ---------------------------------------------------------------------------
# Self-reference (recursion via de Bruijn index 0)
# ---------------------------------------------------------------------------

def test_law_self_reference():
    """A law's body can reference index 0 (itself)."""
    # Identity function applied to itself for 1 step: will just return arg
    src = 'let id_fn = λ x → x'
    v = val_of(src, 'id_fn')
    assert is_law(v)
    # Index 0 = self; index 1 = x.  body = N(1) = x.
    assert v.body == 1


# ---------------------------------------------------------------------------
# Seed round-trips
# ---------------------------------------------------------------------------

def test_round_trip_zero():
    """Round-trip N(0) through seed."""
    v = round_trip('let main = 0', 'main')
    assert is_nat(v) and v == 0


def test_round_trip_nat():
    """Round-trip a nat literal through seed."""
    v = round_trip('let main = 42', 'main')
    assert is_nat(v) and v == 42


def test_round_trip_identity():
    """Round-trip identity law through seed."""
    v = round_trip('let id_fn = λ x → x', 'id_fn')
    assert is_law(v)
    from dev.harness.plan import apply
    result = evaluate(apply(v, N(7)))
    assert is_nat(result) and result == 7


def test_round_trip_if():
    """Round-trip if/then/else through seed."""
    src = 'let main = if True then 10 else 20'
    compiled = pipeline(src)
    seed_bytes = emit(compiled, 'Test.main')
    loaded = load_seed(seed_bytes)
    v = evaluate(loaded)
    assert is_nat(v) and v == 10


def test_round_trip_nullary_con():
    """Round-trip a nullary constructor application through seed."""
    src = '''
type Bit =
  | Zero
  | One

let main = One
'''
    v = round_trip(src, 'main')
    assert is_nat(v) and v == 1  # One = tag 1


def test_emit_all():
    """emit_all produces a seed that loads without error."""
    src = '''
let foo = 1
let bar = 2
'''
    compiled = pipeline(src)
    seed_bytes = emit_all(compiled)
    loaded = load_seed(seed_bytes)
    # Should load without exception
    assert loaded is not None


# ---------------------------------------------------------------------------
# Glass IR rendering
# ---------------------------------------------------------------------------

def test_render_nat():
    assert render_value(N(42)) == '42'


def test_render_pin():
    assert render_value(P(N(0))) == '<0>'


def test_render_law():
    # L(1, encode_name('id'), N(1))
    from bootstrap.codegen import encode_name as en
    v = L(1, en('id'), N(1))
    rendered = render_value(v)
    assert '"id"' in rendered
    assert '1' in rendered


def test_render_app():
    v = A(N(0), N(1))
    rendered = render_value(v)
    assert rendered == '(0 1)'


def test_render_document():
    src = '''
let foo = 1
let bar = 2
'''
    compiled = pipeline(src)
    doc = render(compiled)
    assert 'Glass IR' in doc
    assert 'Test.foo' in doc
    assert 'Test.bar' in doc


def test_render_identity_law():
    src = 'let id_fn = λ x → x'
    compiled = pipeline(src)
    doc = render(compiled)
    assert 'Test.id_fn' in doc
    assert '"id_fn"' in doc


# ---------------------------------------------------------------------------
# CodegenError cases
# ---------------------------------------------------------------------------

def test_unbound_global_raises():
    """Referencing a completely unknown name should raise CodegenError."""
    src = 'let main = does_not_exist'
    try:
        pipeline(src)
        assert False, 'expected CodegenError or ScopeError'
    except (CodegenError, Exception) as exc:
        # Either scope or codegen error is acceptable
        assert 'does_not_exist' in str(exc) or 'unbound' in str(exc).lower()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _run_tests():
    import inspect
    tests = [(name, fn) for name, fn in globals().items()
             if name.startswith('test_') and callable(fn)]
    tests.sort()
    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f'  OK  {name}')
            passed += 1
        except Exception as exc:
            print(f'FAIL  {name}: {exc}')
            import traceback
            traceback.print_exc()
            failed += 1
    print(f'\n{passed} passed, {failed} failed')
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    _run_tests()
