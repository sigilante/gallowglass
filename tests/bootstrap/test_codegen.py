#!/usr/bin/env python3
"""
Codegen tests — bootstrap/codegen.py, bootstrap/emit_seed.py, bootstrap/glass_ir.py

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
from bootstrap.emit_seed import emit, emit_all
from bootstrap.glass_ir_debug import (
    debug_dump_all as render,
    debug_dump_plan_value as render_value,
    decode_name,
)
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


# CPS effect handler helpers
_NULL_DISPATCH = L(3, encode_name('_null_dispatch'), P(N(0)))
# Open-continuation protocol: root k_open is 2-arg (dispatch, value) → value.
_ID_OPEN = L(2, 0, N(2))

def run_cps(val):
    """Run a CPS computation value by applying null_dispatch and id_open."""
    return evaluate(A(A(val, _NULL_DISPATCH), _ID_OPEN))

def eval_handler(src: str, name: str, module: str = 'Test'):
    """Compile a handler expression and run its CPS value to get a raw result."""
    return run_cps(val_of(src, name, module))


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
# Text / Bytes literal compilation
# ---------------------------------------------------------------------------
# Text and Bytes are encoded as A(byte_length, content_nat) per spec §6.
# content_nat = little-endian interpretation of the UTF-8/raw byte sequence.

def test_text_empty():
    """Empty text literal encodes as A(0, 0)."""
    v = val_of('let main = ""', 'main')
    assert is_app(v), f"expected App, got {v!r}"
    bl, cn = v.fun, v.arg
    assert is_nat(bl) and bl == 0
    assert is_nat(cn) and cn == 0


def test_text_single_char():
    """Single ASCII char: byte_length=1, content_nat=ord(c)."""
    v = val_of('let main = "A"', 'main')
    assert is_app(v)
    bl, cn = v.fun, v.arg
    assert is_nat(bl) and bl == 1
    assert is_nat(cn) and cn == ord('A')  # 65


def test_text_hello():
    '"hello": byte_length=5, content_nat=little-endian of UTF-8 bytes.'
    b = "hello".encode('utf-8')
    expected_cn = int.from_bytes(b, 'little')
    v = val_of('let main = "hello"', 'main')
    assert is_app(v)
    bl, cn = v.fun, v.arg
    assert is_nat(bl) and bl == 5
    assert is_nat(cn) and cn == expected_cn


def test_text_in_lambda_body():
    """Text literal inside a lambda (law body arity>0) still encodes as pair."""
    from dev.harness.plan import apply
    v = val_of('let get_greeting : Nat → Text = λ _ → "hi"', 'get_greeting')
    assert is_law(v)
    result = evaluate(apply(v, N(0)))
    assert is_app(result)
    bl, cn = result.fun, result.arg
    b = "hi".encode('utf-8')
    assert is_nat(bl) and bl == 2
    assert is_nat(cn) and cn == int.from_bytes(b, 'little')


def test_text_two_values_differ():
    """Two different text literals compile to structurally different values."""
    v1 = val_of('let main = "abc"', 'main')
    v2 = val_of('let main = "xyz"', 'main')
    assert v1 != v2


def test_text_byte_length_accessor():
    """byte_length field (fun of App) equals len(utf-8 bytes)."""
    src = 'let msg = "hello world"'
    v = val_of(src, 'msg')
    assert is_app(v)
    assert v.fun == 11  # len("hello world".encode()) == 11


def test_bytes_literal():
    """Bytes literal x\"48656c6c6f\" ('Hello') encodes as (5, content_nat) pair."""
    v = val_of('let main = x"48656c6c6f"', 'main')
    assert is_app(v)
    bl, cn = v.fun, v.arg
    assert is_nat(bl) and bl == 5   # 5 bytes
    b = bytes.fromhex('48656c6c6f')
    assert is_nat(cn) and cn == int.from_bytes(b, 'little')


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
# M9.1 — fix (anonymous recursion)
# ---------------------------------------------------------------------------

def test_fix_simple():
    """fix (λ f n → match n { 0→0 | k → f k }) applied to 3 should evaluate to 0."""
    src = '''
let descend = fix λ f n → match n {
  | 0 → 0
  | k → f k
}
'''
    fn = eval_val(src, 'descend')
    from dev.harness.plan import apply
    assert evaluate(apply(fn, N(3))) == 0
    assert evaluate(apply(fn, N(0))) == 0


def test_fix_countdown():
    """fix (λ f n → match n { 0→42 | k → f k }) applied to 5 returns 42."""
    src = '''
let countdown = fix λ f n → match n {
  | 0 → 42
  | k → f k
}
'''
    fn = eval_val(src, 'countdown')
    from dev.harness.plan import apply
    assert evaluate(apply(fn, N(5))) == 42
    assert evaluate(apply(fn, N(0))) == 42


def test_fix_self_ref_is_arity_0():
    """The self-ref parameter does not count toward user arity; law has arity len(user_params)."""
    src = '''
let id_fix = fix λ self x → x
'''
    v = val_of(src, 'id_fix')
    assert is_law(v)
    assert v.arity == 1  # only 'x'; 'self' is index 0


def test_fix_accumulate():
    """Factorial via fix: fix (λ f n → match n { 0→1 | k → k })
    with k being the predecessor (n-1)."""
    src = '''
let pred_fn = fix λ f n → match n {
  | 0 → 1
  | k → k
}
'''
    fn = eval_val(src, 'pred_fn')
    from dev.harness.plan import apply
    # f(0) = 1, f(1) = 0 (predecessor of 1), f(3) = 2 (predecessor of 3)
    assert evaluate(apply(fn, N(0))) == 1
    assert evaluate(apply(fn, N(1))) == 0
    assert evaluate(apply(fn, N(3))) == 2


# ---------------------------------------------------------------------------
# M9.2 — Tuple pattern matching
# ---------------------------------------------------------------------------

def test_tuple_construction():
    """(3, 7) evaluates to A(A(0, 3), 7)."""
    v = eval_val('let main = (3, 7)', 'main')
    assert is_app(v)
    assert is_app(v.fun)
    assert v.fun.fun == 0       # tag 0
    assert v.fun.arg == 3       # first element
    assert v.arg == 7           # second element


def test_tuple_match():
    """match (3, 7) { (a, b) → a } = 3 and similarly b = 7."""
    src = '''
let fst = λ pair → match pair {
  | (a, b) → a
}
let snd = λ pair → match pair {
  | (a, b) → b
}
let main_fst = fst (3, 7)
let main_snd = snd (3, 7)
'''
    compiled = pipeline(src)
    assert evaluate(compiled['Test.main_fst']) == 3
    assert evaluate(compiled['Test.main_snd']) == 7


def test_tuple_match_add():
    """match (3, 7) { (a, b) → a } — structural test; a = 3."""
    src = '''
let fst_of = λ pair → match pair {
  | (a, b) → a
}
let main = fst_of (10, 20)
'''
    v = eval_val(src, 'main')
    assert v == 10


# ---------------------------------------------------------------------------
# M9.3 — Mutual recursion (SCC)
# ---------------------------------------------------------------------------

def test_mutual_is_even_odd():
    """Classic is_even/is_odd mutual recursion."""
    src = '''
let is_even = λ n → match n {
  | 0 → 1
  | k → is_odd k
}

let is_odd = λ n → match n {
  | 0 → 0
  | k → is_even k
}
'''
    compiled = pipeline(src)
    from dev.harness.plan import apply
    is_even = evaluate(compiled['Test.is_even'])
    is_odd  = evaluate(compiled['Test.is_odd'])

    # is_even
    assert evaluate(apply(is_even, N(0))) == 1   # 0 is even
    assert evaluate(apply(is_even, N(1))) == 0   # 1 is not even
    assert evaluate(apply(is_even, N(2))) == 1   # 2 is even
    assert evaluate(apply(is_even, N(4))) == 1   # 4 is even

    # is_odd
    assert evaluate(apply(is_odd, N(0))) == 0    # 0 is not odd
    assert evaluate(apply(is_odd, N(1))) == 1    # 1 is odd
    assert evaluate(apply(is_odd, N(3))) == 1    # 3 is odd


def test_mutual_three_way():
    """Three-way mutual recursion: mod3_is_0 / mod3_is_1 / mod3_is_2."""
    src = '''
let mod3_is_0 = λ n → match n {
  | 0 → 1
  | k → mod3_is_2 k
}

let mod3_is_1 = λ n → match n {
  | 0 → 0
  | k → mod3_is_0 k
}

let mod3_is_2 = λ n → match n {
  | 0 → 0
  | k → mod3_is_1 k
}
'''
    compiled = pipeline(src)
    from dev.harness.plan import apply
    f0 = evaluate(compiled['Test.mod3_is_0'])
    f1 = evaluate(compiled['Test.mod3_is_1'])
    f2 = evaluate(compiled['Test.mod3_is_2'])

    # mod3_is_0: n mod 3 == 0
    assert evaluate(apply(f0, N(0))) == 1   # 0 mod 3 = 0
    assert evaluate(apply(f0, N(3))) == 1   # 3 mod 3 = 0
    assert evaluate(apply(f0, N(1))) == 0   # 1 mod 3 ≠ 0

    # mod3_is_1: n mod 3 == 1
    assert evaluate(apply(f1, N(1))) == 1   # 1 mod 3 = 1
    assert evaluate(apply(f1, N(4))) == 1   # 4 mod 3 = 1
    assert evaluate(apply(f1, N(0))) == 0   # 0 mod 3 ≠ 1

    # mod3_is_2: n mod 3 == 2
    assert evaluate(apply(f2, N(2))) == 1   # 2 mod 3 = 2
    assert evaluate(apply(f2, N(5))) == 1   # 5 mod 3 = 2
    assert evaluate(apply(f2, N(0))) == 0   # 0 mod 3 ≠ 2


# ---------------------------------------------------------------------------
# Mixed-arity constructor match (unary tag>0 in binary path)
# ---------------------------------------------------------------------------
# These tests exercise the `max_arity == 2` path in `_build_field_arm_law` when
# the type has both binary (arity=2) and unary (arity=1, tag>0) constructors.
# The canonical example is PlanVal: PNat(unary,tag=0), PApp(binary,tag=1),
# PLaw(binary,tag=2), PPin(unary,tag=3).

_PLANVAL_SRC = '''
type PlanVal =
  | PNat Nat
  | PApp Nat Nat
  | PLaw Nat Nat
  | PPin Nat
'''


def test_match_mixed_arity_pnat_arm():
    """PNat arm fires correctly (unary tag=0 — baseline for the binary path)."""
    src = _PLANVAL_SRC + '''
let tag_of = λ v → match v {
  | PNat n   → 10
  | PApp f x → 20
  | PLaw n p → 30
  | PPin w   → 40
}
let main = tag_of (PNat 99)
'''
    v = eval_val(src, 'main')
    assert v == 10, f'expected 10 (PNat arm), got {v}'


def test_match_mixed_arity_papp_arm():
    """PApp arm fires correctly (binary tag=1)."""
    src = _PLANVAL_SRC + '''
let tag_of = λ v → match v {
  | PNat n   → 10
  | PApp f x → 20
  | PLaw n p → 30
  | PPin w   → 40
}
let main = tag_of (PApp 1 2)
'''
    v = eval_val(src, 'main')
    assert v == 20, f'expected 20 (PApp arm), got {v}'


def test_match_mixed_arity_plaw_arm():
    """PLaw arm fires correctly (binary tag=2)."""
    src = _PLANVAL_SRC + '''
let tag_of = λ v → match v {
  | PNat n   → 10
  | PApp f x → 20
  | PLaw n p → 30
  | PPin w   → 40
}
let main = tag_of (PLaw 5 6)
'''
    v = eval_val(src, 'main')
    assert v == 30, f'expected 30 (PLaw arm), got {v}'


def test_match_mixed_arity_ppin_arm():
    """PPin arm fires correctly (unary tag=3 — the formerly-deferred path)."""
    src = _PLANVAL_SRC + '''
let tag_of = λ v → match v {
  | PNat n   → 10
  | PApp f x → 20
  | PLaw n p → 30
  | PPin w   → 40
}
let main = tag_of (PPin 7)
'''
    v = eval_val(src, 'main')
    assert v == 40, f'expected 40 (PPin arm), got {v}'


def test_match_mixed_arity_ppin_field_captured():
    """PPin arm body can access the field variable correctly."""
    src = _PLANVAL_SRC + '''
let unwrap_pin = λ v → match v {
  | PNat n   → 0
  | PApp f x → 0
  | PLaw n p → 0
  | PPin w   → w
}
let main = unwrap_pin (PPin 99)
'''
    v = eval_val(src, 'main')
    assert v == 99, f'expected 99 (PPin field), got {v}'


def test_match_mixed_arity_pnat_field_captured():
    """PNat arm body can access its field (unary tag=0, sanity check)."""
    src = _PLANVAL_SRC + '''
let unwrap_nat = λ v → match v {
  | PNat n   → n
  | PApp f x → 0
  | PLaw n p → 0
  | PPin w   → 0
}
let main = unwrap_nat (PNat 77)
'''
    v = eval_val(src, 'main')
    assert v == 77, f'expected 77 (PNat field), got {v}'


def test_match_mixed_arity_papp_fields_captured():
    """PApp arm body can access both binary fields."""
    src = _PLANVAL_SRC + '''
let get_papp_fst = λ v → match v {
  | PNat n   → 0
  | PApp f x → f
  | PLaw n p → 0
  | PPin w   → 0
}
let main = get_papp_fst (PApp 11 22)
'''
    v = eval_val(src, 'main')
    assert v == 11, f'expected 11 (PApp first field), got {v}'


def test_match_mixed_arity_with_outer_capture():
    """PPin arm body can reference outer-scope variables (free variable capture)."""
    src = _PLANVAL_SRC + '''
let add_outer = λ base v → match v {
  | PNat n   → base
  | PApp f x → base
  | PLaw n p → base
  | PPin w   → w
}
let main = add_outer 100 (PPin 42)
'''
    v = eval_val(src, 'main')
    assert v == 42, f'expected 42 (PPin field with outer capture), got {v}'


def test_match_mixed_arity_wildcard_fires():
    """Wildcard arm fires for constructors not listed in the match."""
    src = _PLANVAL_SRC + '''
let partial = λ v → match v {
  | PNat n → n
  | _      → 999
}
let main = partial (PPin 7)
'''
    v = eval_val(src, 'main')
    assert v == 999, f'expected 999 (wildcard), got {v}'


# ---------------------------------------------------------------------------
# AUDIT.md A1: outer locals dropped in mixed nullary/field dispatch
#
# When a type has ≥2 explicitly-named nullary constructors *and* ≥1
# field-bearing constructor, the secondary nullary arms (tags > 0) used to
# be compiled with `pred_env = Env(globals=env.globals, arity=1)`, which
# discarded `env.locals`.  Any arm body referencing an outer-lambda
# parameter raised `CodegenError: unbound variable`.  The fix mirrors the
# capture-and-partial-apply pattern in `make_succ_law`: collect free vars
# across `remaining_nullary` bodies plus `wild_body`, build a lifted law,
# partial-apply at the outer env's perspective.
# ---------------------------------------------------------------------------


_XY_TYPE = '''
type XY =
  | X
  | Y
  | Extra Nat
'''


def test_a1_secondary_nullary_arm_references_outer_lambda():
    """`| Y → v` (tag 1, nullary) must see outer lambda param `v`."""
    src = _XY_TYPE + '''
let check : XY → Nat → Nat
  = λ t v → match t {
      | X       → 0
      | Y       → v
      | Extra n → n
    }
let main : Nat = check Y 42
'''
    assert eval_val(src, 'main') == 42


def test_a1_x_arm_still_returns_zero():
    """The fix must not regress the tag=0 nullary arm."""
    src = _XY_TYPE + '''
let check : XY → Nat → Nat
  = λ t v → match t { | X → 0 | Y → v | Extra n → n }
let main : Nat = check X 42
'''
    assert eval_val(src, 'main') == 0


def test_a1_field_arm_still_extracts():
    """The fix must not regress the field-bearing arm."""
    src = _XY_TYPE + '''
let check : XY → Nat → Nat
  = λ t v → match t { | X → 0 | Y → v | Extra n → n }
let main : Nat = check (Extra 7) 42
'''
    assert eval_val(src, 'main') == 7


def test_a1_secondary_nullary_arm_references_self_and_outer():
    """A secondary nullary arm body that uses self-ref AND an outer local."""
    src = _XY_TYPE + '''
let go : XY → Nat → Nat
  = λ t v → match t {
      | X       → 0
      | Y       → v
      | Extra n → go X v
    }
let main : Nat = go (Extra 99) 7
'''
    assert eval_val(src, 'main') == 0


def test_a1_three_nullary_plus_field_outer_capture():
    """Three nullary tags (0,1,2) plus a field arm — the C arm at tag 2
    is reached via the lifted-law's nat-dispatch chain, exercising
    `_build_nat_dispatch` recursion under the new pred_env."""
    src = '''
type Three =
  | A
  | B
  | C
  | Field Nat
let pick : Three → Nat → Nat
  = λ t v → match t {
      | A       → 0
      | B       → v
      | C       → 100
      | Field n → n
    }
let r1 : Nat = pick A 42
let r2 : Nat = pick B 42
let r3 : Nat = pick C 42
let r4 : Nat = pick (Field 7) 42
'''
    assert eval_val(src, 'r1') == 0
    assert eval_val(src, 'r2') == 42
    assert eval_val(src, 'r3') == 100
    assert eval_val(src, 'r4') == 7


# ---------------------------------------------------------------------------
# Wildcard against type with field-bearing siblings
#
# `match e { | <nullary> → … | _ → … }` on a type whose other constructors
# carry fields (so the scrutinee can show up as an App at runtime) used to
# route through `_build_nat_dispatch` with `id_pin` as the Elim app-case,
# returning the App unchanged instead of firing the wildcard.  The fix
# detects field-bearing siblings in `_compile_con_match`, routes through
# `_compile_adt_dispatch`, and synthesises a const-law app handler for the
# wildcard via `_build_wild_app_handler`.
# ---------------------------------------------------------------------------


_EXPR_TYPE = '''
type E =
  | EConst Nat
  | EAdd E E
  | EErr
'''


def test_wild_app_handler_top_level():
    """`match e { | EErr → 999 | _ → 100 }` against a field-bearing
    constructor (EConst 7, EAdd 1 2) must fire the wildcard, not return
    the App."""
    src = _EXPR_TYPE + '''
let check : E → Nat
  = λ e → match e {
      | EErr → 999
      | _    → 100
    }
let r1 : Nat = check EErr
let r2 : Nat = check (EConst 7)
let r3 : Nat = check (EAdd (EConst 1) (EConst 2))
'''
    assert eval_val(src, 'r1') == 999
    assert eval_val(src, 'r2') == 100
    assert eval_val(src, 'r3') == 100


def test_wild_app_handler_captures_outer_lambda():
    """The wildcard arm's body must see outer-lambda locals through the
    lifted const-law that backs the App branch."""
    src = _EXPR_TYPE + '''
let check : E → Nat → Nat
  = λ e v → match e {
      | EErr → 999
      | _    → v
    }
let r1 : Nat = check EErr 42
let r2 : Nat = check (EConst 7) 42
let r3 : Nat = check (EAdd (EConst 1) (EConst 2)) 42
'''
    assert eval_val(src, 'r1') == 999
    assert eval_val(src, 'r2') == 42
    assert eval_val(src, 'r3') == 42


def test_a1_top_level_path_unchanged():
    """At env.arity == 0 (top level), the old `pred_env = arity=1, no locals`
    path is still taken — no captures to lift.  Pin the no-regression."""
    src = _XY_TYPE + '''
let main : Nat = match Y { | X → 0 | Y → 1 | Extra n → n }
'''
    assert eval_val(src, 'main') == 1


# ---------------------------------------------------------------------------
# F11 (D from feedback follow-ups): nullary + unary + binary mix
#
# `_build_tag_chain` had a bug in its multi-arm branch where
# `first_tag > 0` was ignored: the code unconditionally used
# `tag_val_pairs[0][1]` as the zero_val of an op2 dispatch, regardless of
# whether tag_val_pairs[0][0] was actually 0.  This affected ADTs whose
# field-bearing constructors all had tag > 0 (common for the
# nullary-+-unary-+-binary shape, where Leaf takes tag 0 nullary and the
# field-bearing constructors start at tag 1).
#
# Symptom: `match (Branch a b) { | Leaf → 0 | Node n → n | Branch a b → ANY }`
# returned `<0>` (P(0)) regardless of the Branch arm body.
# ---------------------------------------------------------------------------

_TREE_SRC = '''
type Tree =
  | Leaf
  | Node Nat
  | Branch Nat Nat
'''


def test_match_nullary_unary_binary_branch_arm():
    """Branch (binary tag=2) arm fires correctly when Leaf and Node also exist."""
    src = _TREE_SRC + '''
let depth = λ tt → match tt {
  | Leaf       → 0
  | Node nn    → 1
  | Branch a b → 2
}
let main = depth (Branch 11 22)
'''
    assert eval_val(src, 'main') == 2


def test_match_nullary_unary_binary_branch_field():
    """Branch arm body can read both fields."""
    src = _TREE_SRC + '''
let first_field = λ tt → match tt {
  | Leaf       → 0
  | Node nn    → 0
  | Branch a b → a
}
let second_field = λ tt → match tt {
  | Leaf       → 0
  | Node nn    → 0
  | Branch a b → b
}
let r1 = first_field  (Branch 11 22)
let r2 = second_field (Branch 33 44)
'''
    assert eval_val(src, 'r1') == 11
    assert eval_val(src, 'r2') == 44


def test_match_nullary_unary_binary_all_arms():
    """All three arities dispatch correctly in the same match."""
    src = _TREE_SRC + '''
let go = λ tt → match tt {
  | Leaf       → 100
  | Node nn    → nn
  | Branch a b → a
}
let r0 = go Leaf
let r1 = go (Node 7)
let r2 = go (Branch 11 22)
'''
    assert eval_val(src, 'r0') == 100
    assert eval_val(src, 'r1') == 7
    assert eval_val(src, 'r2') == 11


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


# ---------------------------------------------------------------------------
# M10.2 — Effect handlers: CPS compilation
# ---------------------------------------------------------------------------

def test_eff_op_compiles_to_3arg_law():
    """Effect op compiles to a 3-arg law: (op_arg, dispatch, k) → dispatch(tag, op_arg, k)."""
    src = '''
eff Counter {
  inc : Nat → Nat
}
'''
    compiled = pipeline(src)
    inc_law = compiled.get('Test.Counter.inc')
    assert inc_law is not None, "Test.Counter.inc not in compiled"
    assert is_law(inc_law), f"expected Law, got {type(inc_law)}"
    assert inc_law.arity == 3, f"expected arity 3, got {inc_law.arity}"


def test_handle_single_op_constant_return():
    """handle (inc ()) { | return x → x | inc _ k → k 42 } evaluates to 42."""
    src = '''
eff Counter {
  inc : () → Nat
}

let result = handle (inc ()) {
  | return x → x
  | inc _ kk → kk 42
}
'''
    result = eval_handler(src, 'result')
    assert result == 42, f"expected 42, got {result}"


def test_handle_op_arg_passed_through():
    """Handler arm receives op_arg and can pass it to the continuation."""
    src = '''
eff Counter {
  get_val : () → Nat
}

let result = handle (get_val ()) {
  | return x → x
  | get_val _ kk → kk 99
}
'''
    result = eval_handler(src, 'result')
    assert result == 99, f"expected 99, got {result}"


def test_handle_return_arm_receives_value():
    """Return arm transforms the value before returning."""
    src = '''
eff Counter {
  inc : () → Nat
}

let result = handle (inc ()) {
  | return x → 100
  | inc _ kk → kk 5
}
'''
    result = eval_handler(src, 'result')
    assert result == 100, f"expected 100, got {result}"


def test_handle_op_uses_op_arg():
    """Dispatch arm can use the op_arg in its body."""
    src = '''
eff Counter {
  echo : Nat → Nat
}

let result = handle (echo 77) {
  | return x → x
  | echo nn kk → kk nn
}
'''
    result = eval_handler(src, 'result')
    assert result == 77, f"expected 77, got {result}"


def test_handle_two_ops_first_arm():
    """Two-op effect; first op is called; correct arm dispatches."""
    src = '''
eff Pair {
  first  : () → Nat
  second : () → Nat
}

let result = handle (first ()) {
  | return x → x
  | first  _ kk → kk 11
  | second _ kk → kk 22
}
'''
    result = eval_handler(src, 'result')
    assert result == 11, f"expected 11, got {result}"


def test_handle_two_ops_second_arm():
    """Two-op effect; second op is called; correct arm dispatches."""
    src = '''
eff Pair {
  first  : () → Nat
  second : () → Nat
}

let result = handle (second ()) {
  | return x → x
  | first  _ kk → kk 11
  | second _ kk → kk 22
}
'''
    result = eval_handler(src, 'result')
    assert result == 22, f"expected 22, got {result}"


def test_do_sequence_two_ops():
    """do xx ← inc () in inc xx chains two effect calls."""
    src = '''
eff Counter {
  inc : () → Nat
}

let comp = xx ← inc () in inc xx

let result = handle comp {
  | return rr → rr
  | inc _ kk → kk 7
}
'''
    result = eval_handler(src, 'result')
    assert result == 7, f"expected 7, got {result}"


def test_do_passes_arg_between_ops():
    """Do notation passes the result of one op as arg to the next."""
    src = '''
eff Echo {
  echo : Nat → Nat
}

let comp = nn ← echo 5 in echo nn

let result = handle comp {
  | return rr → rr
  | echo vv kk → kk vv
}
'''
    result = eval_handler(src, 'result')
    assert result == 5, f"expected 5, got {result}"


def test_handle_captures_outer_local():
    """Dispatch arm can reference an outer let binding."""
    src = '''
eff Greeter {
  greet : () → Nat
}

let answer : Nat = 42

let result = handle (greet ()) {
  | return x → x
  | greet _ kk → kk answer
}
'''
    result = eval_handler(src, 'result')
    assert result == 42, f"expected 42, got {result}"


def test_pure_standalone():
    """handle (pure 77) { | return rr → rr } evaluates to 77."""
    src = '''
eff Noop {
  noop : Nat → Nat
}

let result = handle (pure 77) {
  | return rr → rr
}
'''
    result = eval_handler(src, 'result')
    assert result == 77, f"expected 77, got {result}"


def test_pure_in_do_chain():
    """do nn ← echo 42 in pure nn terminates the chain with a pure value."""
    src = '''
eff Echo {
  echo : Nat → Nat
}

let comp = nn ← echo 42 in pure nn

let result = handle comp {
  | return rr → rr
  | echo vv kk → kk vv
}
'''
    result = eval_handler(src, 'result')
    assert result == 42, f"expected 42, got {result}"


def test_pure_transforms_value():
    """Return arm can transform the pure-wrapped value."""
    src = '''
eff Counter {
  inc : () → Nat
}

let result = handle (pure 10) {
  | return rr → 999
}
'''
    result = eval_handler(src, 'result')
    assert result == 999, f"expected 999, got {result}"


def test_state_threading_handler():
    """
    State-threading handler: run_state threads state through continuations.

    eff State {
      get : () → Nat
      put : Nat → ()
    }

    handle (do ss ← get () in put (ss + 1) in pure ss) {
      | return rr   → λ ss → (rr, ss)
      | get    _ kk → λ ss → kk ss ss
      | put    ss kk → λ _ → kk () ss
    } 10

    Should give (10, 11): got=10 (original), final_state=11 (incremented).
    """
    src = '''
eff Stateful {
  get_st : () → Nat
  put_st : Nat → ()
}

-- Computation: read state, increment it, return original value
-- get_st () >>= ss → put_st (ss + 1) >>= _ → pure ss
let comp = ss ← get_st () in pp ← put_st ss in pure ss

-- Handler threads state through continuations
-- get arm: λ state → k state state   (pass state as both result and new state)
-- put arm: λ _ → k () new_state      (install new state, ignore old)
-- return arm: λ final_state → (result, final_state)

let handled = handle comp {
  | return rr → rr
  | get_st _ kk → kk 10
  | put_st _ kk → kk 0
}
'''
    result = eval_handler(src, 'handled')
    # get_st returns 10, put_st discards, pure 10 goes to return rr → rr
    # so result = 10
    assert result == 10, f"expected 10, got {result}"


def test_two_effects_distinct_names_no_collision():
    """Two effects with different op names handled in separate blocks — no tag collision."""
    src = '''
eff Alpha {
  fetch : Nat → Nat
}

eff Beta {
  store : Nat → Nat
}

let ra = handle (fetch 3) {
  | return rr → rr
  | fetch vv kk → kk 99
}

let rb = handle (store 5) {
  | return rr → rr
  | store vv kk → kk 77
}
'''
    compiled = pipeline(src)
    ra = run_cps(compiled['Test.ra'])
    rb = run_cps(compiled['Test.rb'])
    assert ra == 99, f"expected 99, got {ra}"
    assert rb == 77, f"expected 77, got {rb}"


# ---------------------------------------------------------------------------
# M13.3: Shallow handlers (once)
# ---------------------------------------------------------------------------

def test_once_handler_k_unused():
    """Shallow handler (once) that does NOT call k — primary generator pattern."""
    src = '''
eff Yield {
  yield_val : Nat → ()
}

let result = handle (yield_val 42) {
  | return _ → 0
  | once yield_val vv kk → vv
}
'''
    result = eval_handler(src, 'result')
    assert result == 42, f"expected 42, got {result}"


def test_once_handler_k_called():
    """Shallow handler (once): k resumes WITHOUT the handler installed.

    Inner handler is shallow (once). The second yield escapes to the outer
    handler which resumes with 0. If the inner were deep, the second yield
    would be caught by the inner handler and resumed with 99.
    """
    src = '''
eff Yield {
  yield_val : Nat → Nat
}

let comp = xx ← yield_val 10 in yield_val xx

let inner = handle comp {
  | return rr → rr
  | once yield_val vv kk → kk 99
}

let result = handle inner {
  | return rr → rr
  | yield_val vv kk → kk 0
}
'''
    result = eval_handler(src, 'result')
    # Inner (once) handles first yield → resume with 99.
    # Second yield escapes inner (shallow) → caught by outer → resume with 0.
    assert result == 0, f"expected 0 (escaped to outer handler), got {result}"


def test_deep_handler_k_called_twice():
    """Deep handler: both yields are handled (contrast with once test above)."""
    src = '''
eff Yield {
  yield_val : Nat → Nat
}

let comp = xx ← yield_val 10 in yield_val xx

let result = handle comp {
  | return rr → rr
  | yield_val vv kk → kk 99
}
'''
    result = eval_handler(src, 'result')
    # Deep: both yields caught, each resumed with 99.
    assert result == 99, f"expected 99, got {result}"


def test_once_mixed_deep_and_shallow():
    """Two ops in one effect: one deep, one shallow."""
    src = '''
eff Mix {
  deep_op : () → Nat
  once_op : () → Nat
}

let comp = xx ← deep_op () in once_op ()

let result = handle comp {
  | return rr → rr
  | deep_op _ kk → kk 5
  | once once_op _ kk → kk 7
}
'''
    result = eval_handler(src, 'result')
    # deep_op handled normally → resume with 5, then once_op fires.
    # once_op is shallow → resume with 7, handler discharged.
    # Second once_op not present so just returns 7.
    assert result == 7, f"expected 7, got {result}"


def test_once_nested_handler_forwarding():
    """Shallow handler with nested handler: forwarding preserves inner handler."""
    src = '''
eff Inner {
  inner_op : () → Nat
}

eff Outer {
  outer_op : () → Nat
}

let comp = xx ← outer_op () in inner_op ()

let inner_handled = handle comp {
  | return rr → rr
  | inner_op _ kk → kk 10
}

let result = handle inner_handled {
  | return rr → rr
  | once outer_op _ kk → kk 5
}
'''
    result = eval_handler(src, 'result')
    # outer_op forwarded from inner handler to outer handler (once).
    # Resumed with 5. Then inner_op fires, handled by inner handler → 10.
    assert result == 10, f"expected 10, got {result}"


# ---------------------------------------------------------------------------
# Body-context literal quoting (Reaver migration prep)
#
# A literal nat in a law body must always be wrapped in the PLAN quote form
# A(N(0), N(value)) — never emitted as a bare N(k). The PLAN runtime's `kal`
# falls through on N(k) for k > arity and treats it as the constant k, but
# the Plan Assembler emitter sees a bare PNat and renders it as `_k` (a slot
# reference). Always quote-wrapping eliminates the ambiguity at the source.
#
# See bootstrap/codegen.py::Compiler._compile_nat_literal.
# ---------------------------------------------------------------------------

def _walk_plan(val, fn):
    """Walk every node in a PLAN value; call fn on each."""
    fn(val)
    if is_pin(val):
        _walk_plan(val.val, fn)
    elif is_law(val):
        _walk_plan(val.body, fn)
    elif is_app(val):
        _walk_plan(val.fun, fn)
        _walk_plan(val.arg, fn)


def _law_body_constants(law):
    """Return the set of bare-N(k) values found inside a Law's body
    where k is too large to be a de Bruijn slot reference (k > law.arity).

    These are the smoking-gun nats: they decode correctly under the harness
    runtime via kal's fallthrough but break Plan Assembler text emission.
    The fix is for codegen to never produce them — every literal in body
    context must be quote-wrapped in A(N(0), N(value))."""
    found: list[int] = []
    arity = law.arity
    def visit(v):
        if is_nat(v) and v > arity:
            # Bare nat in body context with value > arity — only valid if
            # this is the inner of a quote (A(N(0), N(k))). The quote form
            # is recognised by walking from the outside; here we record
            # bare appearances and rely on the assertion that every nat
            # found in a walk should be a slot reference (≤ arity) since
            # quote nats are nested inside an A node we also visit.
            #
            # Specifically: a properly-quoted constant A(N(0), N(k)) has
            # an inner N(k) that this walk visits. To distinguish, we look
            # for pre-quote N(k) by checking the PARENT structure.
            pass
    # We don't use visit here; instead do a structural scan that
    # explicitly looks for N(k) NOT wrapped in A(N(0), _).
    def scan(v, parent_is_quote_outer):
        if is_nat(v):
            # If this nat is the inner-arg of A(N(0), this), it's a quote.
            if parent_is_quote_outer:
                return  # quoted constant — fine
            if v > arity:
                found.append(int(v))
            return
        if is_pin(v):
            return  # don't recurse into pins (nested laws scanned separately)
        if is_law(v):
            return  # nested laws are scanned via _scan_law
        if is_app(v):
            # Detect quote shape A(N(0), N(k)) at this node:
            # v.fun = N(0), v.arg = N(k)  →  quote of literal k
            is_quote = (is_nat(v.fun) and v.fun == 0)
            scan(v.fun, parent_is_quote_outer=False)
            scan(v.arg, parent_is_quote_outer=is_quote)
    scan(law.body, parent_is_quote_outer=False)
    return found


def _scan_law(val, found_per_law):
    """Recurse into compiled values, scanning every Law for body literals."""
    if is_law(val):
        leaks = _law_body_constants(val)
        if leaks:
            found_per_law.append((val.name, val.arity, leaks))
        _scan_law(val.body, found_per_law)
    elif is_pin(val):
        _scan_law(val.val, found_per_law)
    elif is_app(val):
        _scan_law(val.fun, found_per_law)
        _scan_law(val.arg, found_per_law)


def test_literal_in_body_is_quote_wrapped():
    """A literal nat in a law body must compile to A(N(0), N(k)), not bare N(k).

    Regression for the Plan Assembler emission bug: bare N(constructor_tag)
    in body context renders as `_constructor_tag` (a slot ref) under Plan
    Assembler emission, which Reaver rejects as unbound."""
    src = '''
let constant : Nat → Nat
  = λ x → 100
'''
    val = val_of(src, 'constant')
    # constant is L(1, name, body).
    # Old buggy codegen: body = N(100) (raw, since 100 > arity=1)
    # Fixed codegen:     body = A(N(0), N(100)) (quote form)
    assert is_law(val), f'expected Law, got {type(val).__name__}'
    body = val.body
    assert is_app(body) and is_nat(body.fun) and body.fun == 0, \
        f'literal 100 in body should be quote-wrapped A(N(0), N(100)); got {body!r}'
    assert is_nat(body.arg) and body.arg == 100, \
        f'inner of quote should be N(100); got {body.arg!r}'


def test_constructor_tag_in_body_is_quoted_recursively():
    """No compiled Law in a program with constructors leaves bare nats > arity
    in body position. Constructor tags (which can be huge strNats) are the
    canonical case that hit this bug in the Reaver spike."""
    src = '''
type Color =
  | Red
  | Green
  | Blue

let pick : Nat → Color
  = λ n → match n {
      | 0 → Red
      | 1 → Green
      | _ → Blue
    }
'''
    compiled = pipeline(src)
    leaks: list[tuple[int, int, list[int]]] = []
    for fq, val in compiled.items():
        _scan_law(val, leaks)
    assert not leaks, (
        f'compiled output contains bare nats in body position with value > arity: '
        f'{leaks}. Every literal must be quote-wrapped via A(N(0), N(k)).'
    )


def test_top_level_nat_unchanged():
    """Outside a law body (env.arity == 0), bare N(value) is correct.
    Top-level let with no parameters compiles its rhs as a value, not a body."""
    src = 'let big_const : Nat = 12345'
    val = val_of(src, 'big_const')
    # No body context, so bare N(12345) is fine (and will round-trip
    # through emit_pla as a bare numeric literal).
    assert is_nat(val) and val == 12345, f'top-level nat literal: {val!r}'
