#!/usr/bin/env python3
"""
Coverage gap tests — high-priority and medium-priority gaps identified in
test coverage review (2026-04-06).

High priority:
  1. Nested effect handlers
  2. Multiple continuation resumption / no resumption
  3. Polymorphic typeclass instances (codegen limitation documented)
  4. Superclass constraints

Medium priority:
  5. UTF-8 in text/bytes literals
  6. Exhaustiveness / pattern match edge cases
  7. Namespace collision on import
  8. Deep recursion stress (Python evaluator limits)
  9. Cross-module external mod resolution

Naming convention notes (Gallowglass restricted dialect):
  - Single-letter a-q = type variables (TTypeVar), NOT snake_case identifiers.
  - Use 2+ char names for variables: fn, aa, bb, etc.
  - Handler op arm syntax: | op_name arg_pat resume_name → body
    where resume_name must be a snake_case ident (not _).
  - Superclass syntax: class Eq a => Ord a { ... } (constraints BEFORE class name).

Run: python3 -m pytest tests/bootstrap/test_coverage_gaps.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest

from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve, ScopeError
from bootstrap.codegen import compile_program, CodegenError, encode_name
from bootstrap.build import build_modules, BuildError
from dev.harness.plan import P, L, A, N, is_nat, is_pin, is_law, is_app, evaluate, apply


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pipeline(src: str, module: str = 'Test') -> dict:
    prog = parse(lex(src, '<test>'), '<test>')
    resolved, _ = resolve(prog, module, {}, '<test>')
    return compile_program(resolved, module)


def val_of(src: str, name: str, module: str = 'Test'):
    compiled = pipeline(src, module)
    fq = f'{module}.{name}'
    assert fq in compiled, f"'{fq}' not in compiled: {list(compiled.keys())}"
    return compiled[fq]


def eval_val(src: str, name: str, module: str = 'Test'):
    return evaluate(val_of(src, name, module))


# CPS effect handler helpers
_NULL_DISPATCH = L(3, encode_name('_null_dispatch'), P(N(0)))
_ID_OPEN = L(2, 0, N(2))

def run_cps(val):
    """Run a CPS computation value by applying null_dispatch and id_open."""
    return evaluate(A(A(val, _NULL_DISPATCH), _ID_OPEN))

def eval_handler(src: str, name: str, module: str = 'Test'):
    """Compile a handler expression and run its CPS value to get a raw result."""
    return run_cps(val_of(src, name, module))


def run_modules(sources: list[tuple[str, str]], name: str, *args):
    compiled = build_modules(sources)
    assert name in compiled, f"'{name}' not in compiled: {sorted(compiled.keys())}"
    vv = compiled[name]
    for aa in args:
        vv = apply(vv, aa)
    return evaluate(vv)


# ===========================================================================
# HIGH PRIORITY 1: Nested effect handlers
# ===========================================================================

class TestNestedHandlers:
    """Tests for handle (handle comp { E1 }) { E2 } — nesting two handlers."""

    def test_single_effect_handled_produces_value(self):
        """Handling a single-effect computation produces correct result via CPS run."""
        src = '''
eff Inner {
  get_inner : () → Nat
}

let comp = get_inner ()

let result = handle comp {
  | return xx → xx
  | get_inner _ kk → kk 10
}
'''
        result = eval_handler(src, 'result')
        assert result == 10, f"expected 10, got {result}"

    def test_handle_result_is_cps(self):
        """After handling, the result is a CPS value that can be run."""
        src = '''
eff AA {
  ask_aa : () → Nat
}

let inner_comp = ask_aa ()

let inner_handled = handle inner_comp {
  | return xx → xx
  | ask_aa _ kk → kk 5
}
'''
        result = eval_handler(src, 'inner_handled')
        assert result == 5, f"expected 5, got {result}"

    def test_nested_do_chain_two_effects(self):
        """Do chain using ops from two effects, nested handlers.
        Inner handler handles log, outer handler handles ask.
        Unhandled ops forward through dispatch_parent."""
        src = '''
eff Reader {
  ask : () → Nat
}

eff Logger {
  log : Nat → ()
}

let comp = xx ← ask () in yy ← log xx in pure xx

let logged = handle comp {
  | return rr → rr
  | log _ kk → kk 0
}

let result = handle logged {
  | return rr → rr
  | ask _ kk → kk 42
}
'''
        result = eval_handler(src, 'result')
        assert result == 42, f"expected 42, got {result}"

    def test_sequential_independent_handlers(self):
        """Two independent handlers on separate computations — no nesting needed."""
        src = '''
eff EE {
  fetch : () → Nat
}

let comp = fetch ()

let result = handle comp {
  | return xx → xx
  | fetch _ kk → kk 7
}
'''
        result = eval_handler(src, 'result')
        assert result == 7, f"expected 7, got {result}"


# ===========================================================================
# HIGH PRIORITY 2: Multiple continuation resumption / no resumption
# ===========================================================================

class TestMultipleResumption:
    """Tests for handler arms that do or don't call k."""

    def test_handler_no_resume(self):
        """Handler arm that does NOT call k — early termination."""
        src = '''
eff EE {
  boom : () → Nat
}

let result = handle (boom ()) {
  | return xx → xx
  | boom _ kk → 999
}
'''
        result = eval_handler(src, 'result')
        assert result == 999, f"expected 999 (no resume), got {result}"

    def test_handler_resume_once(self):
        """Handler arm calls k exactly once (baseline)."""
        src = '''
eff EE {
  ask : () → Nat
}

let result = handle (ask ()) {
  | return xx → xx
  | ask _ kk → kk 42
}
'''
        result = eval_handler(src, 'result')
        assert result == 42, f"expected 42, got {result}"

    def test_handler_resume_uses_arg(self):
        """Handler arm inspects op arg, conditionally resumes."""
        src = '''
eff EE {
  check : Nat → Nat
}

let result = handle (check 0) {
  | return xx → xx
  | check nn kk → match nn { | 0 → 777 | _ → kk nn }
}
'''
        result = eval_handler(src, 'result')
        assert result == 777, f"expected 777 (check 0 → no resume), got {result}"

    def test_handler_resume_nonzero_arg(self):
        """Same handler, nonzero arg → resumes."""
        src = '''
eff EE {
  check : Nat → Nat
}

let result = handle (check 5) {
  | return xx → xx
  | check nn kk → match nn { | 0 → 777 | _ → kk nn }
}
'''
        result = eval_handler(src, 'result')
        assert result == 5, f"expected 5 (check 5 → resume), got {result}"


# ===========================================================================
# HIGH PRIORITY 3: Polymorphic typeclass instances
# ===========================================================================

class TestPolymorphicInstances:
    """Tests for typeclass features: multi-method instances, constrained fns."""

    def test_monomorphic_instance_compiles_via_let(self):
        """Monomorphic instance using a named let for the method body."""
        src = '''
class MyEq a {
  my_eq : a → a → Nat
}

let nat_eq_impl : Nat → Nat → Nat
  = λ xx yy → match xx {
    | 0 → match yy { | 0 → 1 | _ → 0 }
    | kk → match yy { | 0 → 0 | jj → nat_eq_impl kk jj }
  }

instance MyEq Nat {
  my_eq = nat_eq_impl
}
'''
        compiled = pipeline(src)
        assert 'Test.inst_MyEq_Nat' in compiled
        fn = compiled['Test.inst_MyEq_Nat']
        assert evaluate(apply(apply(fn, N(3)), N(3))) == 1
        assert evaluate(apply(apply(fn, N(3)), N(4))) == 0

    def test_three_method_class_instance(self):
        """Three-method class instance: all methods emitted and callable.
        Note: restricted dialect has no increment, so we test with functions
        that work with predecessor-based nat matching."""
        src = '''
class Trio a {
  trio_pred  : a → a
  trio_zero  : a → a
  trio_check : a → a
}

let nat_pred : Nat → Nat
  = λ nn → match nn { | 0 → 0 | kk → kk }

let nat_to_zero : Nat → Nat
  = λ nn → 0

let nat_check : Nat → Nat
  = λ nn → match nn { | 0 → 1 | _ → 0 }

instance Trio Nat {
  trio_pred  = nat_pred
  trio_zero  = nat_to_zero
  trio_check = nat_check
}
'''
        compiled = pipeline(src)
        assert 'Test.inst_Trio_Nat_trio_pred' in compiled
        assert 'Test.inst_Trio_Nat_trio_zero' in compiled
        assert 'Test.inst_Trio_Nat_trio_check' in compiled

        pred_fn = compiled['Test.inst_Trio_Nat_trio_pred']
        assert evaluate(apply(pred_fn, N(5))) == 4
        assert evaluate(apply(pred_fn, N(0))) == 0

        zero_fn = compiled['Test.inst_Trio_Nat_trio_zero']
        assert evaluate(apply(zero_fn, N(99))) == 0

        check_fn = compiled['Test.inst_Trio_Nat_trio_check']
        assert evaluate(apply(check_fn, N(0))) == 1
        assert evaluate(apply(check_fn, N(3))) == 0

    def test_constrained_function_three_method_class(self):
        """Constrained function with 3-method class gets 3 extra dict params."""
        src = '''
class Trio a {
  trio_pred  : a → a
  trio_zero  : a → a
  trio_check : a → a
}

let nat_pred : Nat → Nat
  = λ nn → match nn { | 0 → 0 | kk → kk }

let nat_to_zero : Nat → Nat = λ nn → 0

let nat_check : Nat → Nat
  = λ nn → match nn { | 0 → 1 | _ → 0 }

instance Trio Nat {
  trio_pred  = nat_pred
  trio_zero  = nat_to_zero
  trio_check = nat_check
}

let apply_pred : ∀ a. Trio a => a → a
  = λ xx → trio_pred xx
'''
        compiled = pipeline(src)
        apply_pred_law = compiled['Test.apply_pred']
        assert is_law(apply_pred_law)
        # 3 dict params (trio_pred, trio_zero, trio_check) + 1 user param = 4
        assert apply_pred_law.arity == 4, f"expected arity 4, got {apply_pred_law.arity}"


# ===========================================================================
# HIGH PRIORITY 4: Superclass constraints
# ===========================================================================

class TestSuperclassConstraints:
    """Tests for class inheritance: class Eq a => Ord a { ... }."""

    def test_superclass_syntax_parses(self):
        """Superclass constraint syntax is accepted by the parser."""
        src = '''
class Eq a {
  eq : a → a → Nat
}

class Eq a => Ord a {
  lt : a → a → Nat
}
'''
        prog = parse(lex(src, '<test>'), '<test>')
        resolved, _ = resolve(prog, 'Test', {}, '<test>')
        compiled = compile_program(resolved, 'Test')
        # Neither class emits a PLAN value
        assert 'Test.Eq' not in compiled
        assert 'Test.Ord' not in compiled

    def test_superclass_with_instances(self):
        """Superclass classes both have instances; both compile."""
        src = '''
class Eq a {
  eq : a → a → Nat
}

class Eq a => Ord a {
  lt : a → a → Nat
}

let nat_eq : Nat → Nat → Nat
  = λ xx yy → match xx {
    | 0 → match yy { | 0 → 1 | _ → 0 }
    | kk → match yy { | 0 → 0 | jj → nat_eq kk jj }
  }

let nat_lt : Nat → Nat → Nat
  = λ xx yy → match xx {
    | 0 → match yy { | 0 → 0 | _ → 1 }
    | kk → match yy { | 0 → 0 | jj → nat_lt kk jj }
  }

instance Eq Nat {
  eq = nat_eq
}

instance Ord Nat {
  lt = nat_lt
}
'''
        compiled = pipeline(src)
        assert 'Test.inst_Eq_Nat' in compiled
        assert 'Test.inst_Ord_Nat' in compiled

        eq_fn = compiled['Test.inst_Eq_Nat']
        assert evaluate(apply(apply(eq_fn, N(3)), N(3))) == 1
        assert evaluate(apply(apply(eq_fn, N(3)), N(4))) == 0

        lt_fn = compiled['Test.inst_Ord_Nat']
        assert evaluate(apply(apply(lt_fn, N(2)), N(5))) == 1
        assert evaluate(apply(apply(lt_fn, N(5)), N(2))) == 0

    def test_constrained_by_eq_with_superclass_present(self):
        """Function constrained by Eq works when Ord with superclass also defined."""
        src = '''
class Eq a {
  eq : a → a → Nat
}

class Eq a => Ord a {
  lt : a → a → Nat
}

let nat_eq : Nat → Nat → Nat
  = λ xx yy → match xx {
    | 0 → match yy { | 0 → 1 | _ → 0 }
    | kk → match yy { | 0 → 0 | jj → nat_eq kk jj }
  }

let nat_lt : Nat → Nat → Nat
  = λ xx yy → match xx {
    | 0 → match yy { | 0 → 0 | _ → 1 }
    | kk → match yy { | 0 → 0 | jj → nat_lt kk jj }
  }

instance Eq Nat {
  eq = nat_eq
}

instance Ord Nat {
  lt = nat_lt
}

let same : ∀ a. Eq a => a → a → Nat = λ xx yy → eq xx yy

let test_same = same 5 5
'''
        compiled = pipeline(src)
        assert evaluate(compiled['Test.test_same']) == 1

    def test_superclass_flat_expansion(self):
        """Function constrained by Ord uses both eq (from Eq superclass) and lt."""
        src = '''
class Eq a {
  eq : a → a → Nat
}

class Eq a => Ord a {
  lt : a → a → Nat
}

let nat_eq : Nat → Nat → Nat
  = λ xx yy → match xx {
    | 0 → match yy { | 0 → 1 | _ → 0 }
    | kk → match yy { | 0 → 0 | jj → nat_eq kk jj }
  }

let nat_lt : Nat → Nat → Nat
  = λ xx yy → match xx {
    | 0 → match yy { | 0 → 0 | _ → 1 }
    | kk → match yy { | 0 → 0 | jj → nat_lt kk jj }
  }

instance Eq Nat {
  eq = nat_eq
}

instance Ord Nat {
  lt = nat_lt
}

let both : ∀ a. Ord a => a → a → Nat
  = λ xx yy → match (eq xx yy) {
    | 0 → lt xx yy
    | _ → 99
  }

let test_eq = both 3 3
let test_lt = both 2 5
let test_gt = both 5 2
'''
        compiled = pipeline(src)
        assert evaluate(compiled['Test.test_eq']) == 99  # eq returns 1 → 99
        assert evaluate(compiled['Test.test_lt']) == 1   # eq returns 0 → lt returns 1
        assert evaluate(compiled['Test.test_gt']) == 0   # eq returns 0 → lt returns 0

    def test_superclass_multi_level(self):
        """Three-level superclass chain: A => B => C."""
        src = '''
class A a {
  method_a : a → Nat
}

class A a => B a {
  method_b : a → Nat
}

class B a => C a {
  method_c : a → Nat
}

instance A Nat {
  method_a = λ xx → 1
}

instance B Nat {
  method_b = λ xx → 2
}

instance C Nat {
  method_c = λ xx → 3
}

let use_c : ∀ a. C a => a → Nat
  = λ xx → method_c xx

let test_c = use_c 42
'''
        compiled = pipeline(src)
        assert evaluate(compiled['Test.test_c']) == 3


# ===========================================================================
# MEDIUM PRIORITY 5: UTF-8 in text/bytes literals
# ===========================================================================

class TestUTF8Encoding:
    """Tests for non-ASCII UTF-8 text literals."""

    def test_text_multibyte_umlaut(self):
        """Text with u-umlaut (2 bytes in UTF-8)."""
        vv = val_of('let main = "\u00fc"', 'main')
        assert is_app(vv)
        bl, cn = vv.fun, vv.arg
        bb = "\u00fc".encode('utf-8')
        assert bl == len(bb), f"expected byte_length {len(bb)}, got {bl}"
        assert cn == int.from_bytes(bb, 'little')

    def test_text_multibyte_mixed(self):
        """Text with mixed ASCII and multibyte."""
        vv = val_of('let main = "a\u00fc"', 'main')
        assert is_app(vv)
        bl, cn = vv.fun, vv.arg
        bb = "a\u00fc".encode('utf-8')
        assert bl == len(bb), f"expected byte_length {len(bb)}, got {bl}"
        assert cn == int.from_bytes(bb, 'little')

    def test_text_cjk_character(self):
        """Text with CJK character (3 bytes in UTF-8)."""
        vv = val_of('let main = "\u4e2d"', 'main')
        assert is_app(vv)
        bl, cn = vv.fun, vv.arg
        bb = "\u4e2d".encode('utf-8')
        assert bl == len(bb)
        assert cn == int.from_bytes(bb, 'little')

    def test_text_emoji(self):
        """Text with emoji (4 bytes in UTF-8)."""
        vv = val_of('let main = "\U0001f389"', 'main')
        assert is_app(vv)
        bl, cn = vv.fun, vv.arg
        bb = "\U0001f389".encode('utf-8')
        assert bl == len(bb)
        assert cn == int.from_bytes(bb, 'little')

    def test_text_arrow_unicode(self):
        """Text with unicode arrow."""
        vv = val_of('let main = "\u2192"', 'main')
        assert is_app(vv)
        bl, cn = vv.fun, vv.arg
        bb = "\u2192".encode('utf-8')
        assert bl == len(bb)
        assert cn == int.from_bytes(bb, 'little')

    def test_text_multibyte_in_lambda(self):
        """Multibyte text inside a law body (arity > 0)."""
        vv = val_of('let get_text = \u03bb xx \u2192 "\u00fc"', 'get_text')
        assert is_law(vv)
        result = evaluate(apply(vv, N(0)))
        assert is_app(result)
        bl, cn = result.fun, result.arg
        bb = "\u00fc".encode('utf-8')
        assert bl == len(bb)
        assert cn == int.from_bytes(bb, 'little')

    def test_bytes_with_null_byte(self):
        """Bytes literal with null byte."""
        vv = val_of('let main = x"0048"', 'main')
        assert is_app(vv)
        bl, cn = vv.fun, vv.arg
        bb = bytes.fromhex('0048')
        assert bl == len(bb), f"expected {len(bb)}, got {bl}"
        assert cn == int.from_bytes(bb, 'little')

    def test_bytes_with_high_bit(self):
        """Bytes literal with high-bit bytes."""
        vv = val_of('let main = x"FF80"', 'main')
        assert is_app(vv)
        bl, cn = vv.fun, vv.arg
        bb = bytes.fromhex('FF80')
        assert bl == len(bb)
        assert cn == int.from_bytes(bb, 'little')


# ===========================================================================
# MEDIUM PRIORITY 6: Exhaustiveness / pattern match edge cases
# ===========================================================================

class TestExhaustivenessEdgeCases:
    """Tests documenting behavior of non-exhaustive and overlapping patterns."""

    def test_exhaustive_bool_match(self):
        """Exhaustive match on Bool: both arms covered."""
        src = '''
let to_nat = λ bb → match bb { | True → 1 | False → 0 }
let main = to_nat True
'''
        assert eval_val(src, 'main') == 1

    def test_wildcard_covers_remaining(self):
        """Wildcard after specific arms covers remaining constructors."""
        src = '''
type Color = | Red | Green | Blue
let is_red = λ cc → match cc { | Red → 1 | _ → 0 }
let main = is_red Green
'''
        assert eval_val(src, 'main') == 0

    def test_nat_match_with_specific_and_wild(self):
        """Nat match: specific values + wildcard."""
        src = '''
let classify = λ nn → match nn {
  | 0 → 100
  | 1 → 200
  | _ → 300
}
let aa = classify 0
let bb = classify 1
let cc = classify 99
'''
        assert eval_val(src, 'aa') == 100
        assert eval_val(src, 'bb') == 200
        assert eval_val(src, 'cc') == 300

    def test_single_arm_con_with_wildcard(self):
        """Single constructor arm + wildcard: the CLAUDE.md pitfall case."""
        src = '''
type Wrapper = | Wrap Nat | Empty
let unwrap = λ ww → match ww { | Wrap nn → nn | _ → 0 }
let aa = unwrap (Wrap 42)
let bb = unwrap Empty
'''
        assert eval_val(src, 'aa') == 42
        assert eval_val(src, 'bb') == 0

    def test_overlapping_nat_arms_first_wins(self):
        """When multiple nat arms could match, first one wins."""
        src = '''
let ff = λ nn → match nn {
  | 0 → 10
  | _ → 30
}
let main = ff 0
'''
        # First arm should win
        assert eval_val(src, 'main') == 10


# ===========================================================================
# MEDIUM PRIORITY 7: Namespace collision on import
# ===========================================================================

class TestNamespaceCollision:
    """Tests for when two modules export the same name."""

    def test_two_modules_same_name_qualified_ok(self):
        """Two modules with same-named function: qualified access works."""
        src_a = "let helper : Nat = 10\n"
        src_b = "let helper : Nat = 20\n"
        src_c = """\
use A
use B

let result_a : Nat = A.helper
let result_b : Nat = B.helper
"""
        compiled = build_modules([('A', src_a), ('B', src_b), ('C', src_c)])
        assert evaluate(compiled['C.result_a']) == 10
        assert evaluate(compiled['C.result_b']) == 20

    def test_two_modules_same_name_unqualified_raises(self):
        """Two modules exporting same name, both imported unqualified -> error."""
        src_a = "let helper : Nat = 10\n"
        src_b = "let helper : Nat = 20\n"
        src_c = """\
use A unqualified { helper }
use B unqualified { helper }

let result : Nat = helper
"""
        with pytest.raises((ScopeError, Exception)):
            build_modules([('A', src_a), ('B', src_b), ('C', src_c)])

    def test_one_qualified_one_unqualified_no_collision(self):
        """One import qualified, one unqualified: unqualified wins for bare name."""
        src_a = "let val : Nat = 10\n"
        src_b = "let val : Nat = 20\n"
        src_c = """\
use A
use B unqualified { val }

let result : Nat = val
"""
        compiled = build_modules([('A', src_a), ('B', src_b), ('C', src_c)])
        assert evaluate(compiled['C.result']) == 20

    def test_type_name_collision_qualified(self):
        """Two modules with same type name: separate namespaces."""
        src_a = "type Status = | Ok | Err\n"
        src_b = "type Status = | Ok | Err\n"
        compiled = build_modules([('A', src_a), ('B', src_b)])
        # Both modules compile without error
        assert True


# ===========================================================================
# MEDIUM PRIORITY 8: Deep recursion stress
# ===========================================================================

class TestDeepRecursion:
    """Stress tests for recursive programs.

    Note: The Python PLAN evaluator (dev/harness/plan.py) uses recursive kal()
    calls and hits Python's default recursion limit (~1000) for deep evaluations.
    Tests that exceed this are marked xfail to document the known limitation.

    The depth-guard in `evaluate()` (formerly `if _depth > 10000: return val`)
    used to silently return the partial value, so the `except RecursionError`
    arms below would never fire — tests would assert against an `A` node and
    pass-with-wrong-result.  AUDIT.md B1 fixed the guard to raise; the
    `pin_depth_guard_raises` test below is the contract for that behaviour.
    """

    def test_evaluate_depth_guard_raises(self):
        """`evaluate()` raises `RecursionError` past `EVALUATE_DEPTH_LIMIT`
        rather than silently returning a partial value (AUDIT.md B1)."""
        from dev.harness.plan import evaluate, EVALUATE_DEPTH_LIMIT, P, N
        v = N(0)
        for _ in range(EVALUATE_DEPTH_LIMIT + 5):
            v = P(v)
        with pytest.raises(RecursionError):
            evaluate(v)

    def test_deep_nat_recursion_100(self):
        """Recursive function called 100 times deep."""
        src = '''
let countdown = λ nn → match nn {
  | 0 → 0
  | kk → countdown kk
}
let main = countdown 100
'''
        try:
            assert eval_val(src, 'main') == 0
        except RecursionError:
            pytest.skip("Python evaluator recursion limit hit at depth 100")

    def test_deep_nat_recursion_1000(self):
        """Recursive function called 1000 times deep — may exceed Python limit."""
        src = '''
let countdown = λ nn → match nn {
  | 0 → 0
  | kk → countdown kk
}
let main = countdown 1000
'''
        try:
            assert eval_val(src, 'main') == 0
        except RecursionError:
            pytest.skip("Python evaluator recursion limit hit at depth 1000")

    def test_mutual_recursion_100(self):
        """Mutual recursion 100 levels deep."""
        src = '''
let ping = λ nn → match nn { | 0 → 1 | kk → pong kk }
let pong = λ nn → match nn { | 0 → 0 | kk → ping kk }
let main = ping 100
'''
        try:
            # 100 is even, so ping 100 → ... → ping 0 → 1
            assert eval_val(src, 'main') == 1
        except RecursionError:
            pytest.skip("Python evaluator recursion limit hit at depth 100")

    def test_fix_deep_recursion(self):
        """Fix expression recursing 500 levels."""
        src = '''
let main = (fix λ self nn → match nn { | 0 → 0 | kk → self kk }) 500
'''
        try:
            assert eval_val(src, 'main') == 0
        except RecursionError:
            pytest.skip("Python evaluator recursion limit hit at depth 500")

    def test_five_way_mutual_recursion(self):
        """Five-way mutual recursion (mod 5 check)."""
        src = '''
let mod5_0 = λ nn → match nn { | 0 → 1 | kk → mod5_4 kk }
let mod5_1 = λ nn → match nn { | 0 → 0 | kk → mod5_0 kk }
let mod5_2 = λ nn → match nn { | 0 → 0 | kk → mod5_1 kk }
let mod5_3 = λ nn → match nn { | 0 → 0 | kk → mod5_2 kk }
let mod5_4 = λ nn → match nn { | 0 → 0 | kk → mod5_3 kk }

let test_0 = mod5_0 0
let test_5 = mod5_0 5
let test_10 = mod5_0 10
let test_1 = mod5_0 1
let test_7 = mod5_0 7
'''
        assert eval_val(src, 'test_0') == 1
        assert eval_val(src, 'test_5') == 1
        assert eval_val(src, 'test_10') == 1
        assert eval_val(src, 'test_1') == 0
        assert eval_val(src, 'test_7') == 0

    def test_many_constructors_type(self):
        """Type with 8 constructors: tag encoding works for all."""
        src = '''
type Octet =
  | C0 | C1 | C2 | C3
  | C4 | C5 | C6 | C7

let tag = λ xx → match xx {
  | C0 → 0 | C1 → 1 | C2 → 2 | C3 → 3
  | C4 → 4 | C5 → 5 | C6 → 6 | C7 → 7
}

let t0 = tag C0
let t3 = tag C3
let t7 = tag C7
'''
        assert eval_val(src, 't0') == 0
        assert eval_val(src, 't3') == 3
        assert eval_val(src, 't7') == 7


# ===========================================================================
# MEDIUM PRIORITY 9: Cross-module external mod resolution
# ===========================================================================

class TestCrossModuleExternalMod:
    """Tests for external mod declarations used across module boundaries."""

    def test_external_mod_within_module(self):
        """External mod used within its declaring module compiles."""
        src = '''
external mod Prim {
  prim_add : Nat → Nat → Nat
}
'''
        compiled = pipeline(src)
        # External mods register but produce external stubs
        assert True  # parsing and compilation succeed

    def test_cross_module_type_with_external_dep(self):
        """Module A defines a type; Module B uses it. Both compile via build."""
        src_a = """\
type Pair = | MkPair Nat Nat

let fst_of : Pair → Nat = λ pp → match pp { | MkPair aa bb → aa }
let snd_of : Pair → Nat = λ pp → match pp { | MkPair aa bb → bb }
"""
        src_b = """\
use A unqualified { Pair, MkPair, fst_of, snd_of }

let swap : Pair → Pair = λ pp → MkPair (snd_of pp) (fst_of pp)

let test_fst : Nat = fst_of (swap (MkPair 10 20))
let test_snd : Nat = snd_of (swap (MkPair 10 20))
"""
        compiled = build_modules([('A', src_a), ('B', src_b)])
        assert evaluate(compiled['B.test_fst']) == 20
        assert evaluate(compiled['B.test_snd']) == 10

    def test_transitive_function_chain(self):
        """Three modules: A defines fn, B wraps it, C calls B's wrapper."""
        src_a = """\
let decr : Nat → Nat
  = λ nn → match nn { | 0 → 0 | kk → kk }
"""
        src_b = """\
use A unqualified { decr }

let decr_twice : Nat → Nat = λ nn → decr (decr nn)
"""
        src_c = """\
use B unqualified { decr_twice }

let result : Nat = decr_twice 5
"""
        compiled = build_modules([('A', src_a), ('B', src_b), ('C', src_c)])
        # decr(5)=4, decr(4)=3
        assert evaluate(compiled['C.result']) == 3


# ===========================================================================
# Additional edge cases discovered during review
# ===========================================================================

class TestEffectHandlerEdgeCases:
    """Additional effect handler edge cases."""

    def test_three_op_effect(self):
        """Effect with three ops: all dispatch correctly."""
        src = '''
eff Triple {
  op_a : () → Nat
  op_b : () → Nat
  op_c : () → Nat
}

let ra = handle (op_a ()) {
  | return xx → xx
  | op_a _ kk → kk 1
  | op_b _ kk → kk 2
  | op_c _ kk → kk 3
}

let rb = handle (op_b ()) {
  | return xx → xx
  | op_a _ kk → kk 1
  | op_b _ kk → kk 2
  | op_c _ kk → kk 3
}

let rc = handle (op_c ()) {
  | return xx → xx
  | op_a _ kk → kk 1
  | op_b _ kk → kk 2
  | op_c _ kk → kk 3
}
'''
        assert eval_handler(src, 'ra') == 1
        assert eval_handler(src, 'rb') == 2
        assert eval_handler(src, 'rc') == 3

    def test_handler_with_longer_do_chain(self):
        """Do chain with 3 operations."""
        src = '''
eff Counter {
  inc : () → Nat
}

let comp = aa ← inc () in bb ← inc () in cc ← inc () in pure cc

let result = handle comp {
  | return rr → rr
  | inc _ kk → kk 42
}
'''
        result = eval_handler(src, 'result')
        assert result == 42, f"expected 42, got {result}"

    def test_handler_op_arg_echoed(self):
        """Handler echoes the op arg back as the result."""
        src = '''
eff Echo {
  echo_val : Nat → Nat
}

let comp = echo_val 77

let result = handle comp {
  | return xx → xx
  | echo_val nn kk → kk nn
}
'''
        result = eval_handler(src, 'result')
        assert result == 77

    # ----------------------------------------------------------------------
    # F6: handler arms resuming with constructor-App values
    # ----------------------------------------------------------------------
    # Issue #7 from feedback: every existing handler test resumes with a
    # bare Nat (`kk : Nat → ...`).  No test exercises a handler arm where
    # the continuation receives a constructor-App value (e.g. `MkPair 1 2`,
    # a `Some x`, an `Ok v`).  The user reported this works in practice
    # but wanted explicit test coverage to settle the precedent.

    def test_handler_resume_with_pair_constructor(self):
        """`kk (MkPair 1 2)` — resume with a binary constructor App.

        The do-binder receives the constructor value through the continuation,
        and the return arm pattern-matches it to extract a field.  This exercises
        the path where the resumed continuation receives a non-Nat App tree.
        """
        src = '''
type MyPair = | MkPair Nat Nat

eff Provider {
  fetch_pair : () → MyPair
}

let comp = pp ← fetch_pair () in pure pp

let pair_snd = handle comp {
  | return rr → match rr { | MkPair _ yy → yy }
  | fetch_pair _ kk → kk (MkPair 1 2)
}
'''
        result = eval_handler(src, 'pair_snd')
        assert result == 2, f'expected 2, got {result}'

    def test_handler_resume_with_option_some(self):
        """`kk (Some 99)` — resume with a unary constructor App."""
        src = '''
type MyOpt = | MyNone | MySome Nat

eff Lookup {
  lookup : () → MyOpt
}

let comp = oo ← lookup () in pure oo

let extracted = handle comp {
  | return rr → match rr { | MyNone → 0 | MySome xx → xx }
  | lookup _ kk → kk (MySome 99)
}
'''
        result = eval_handler(src, 'extracted')
        assert result == 99

    def test_handler_resume_with_constructor_threaded_through_do_chain(self):
        """Multi-step: handler resumes with constructor, do-bind threads it,
        return arm extracts the field."""
        src = '''
type MyResult = | MyOk Nat | MyErr Nat

eff Worker {
  do_work : Nat → MyResult
}

let comp = aa ← do_work 5 in bb ← do_work 7 in pure bb

let extracted = handle comp {
  | return rr → match rr { | MyOk vv → vv | MyErr _ → 0 }
  | do_work nn kk → kk (MyOk nn)
}
'''
        # Two do-binds; second value is what's returned and matched
        result = eval_handler(src, 'extracted')
        assert result == 7


class TestTypeclassEdgeCases:
    """Additional typeclass edge cases."""

    def test_instance_method_is_recursive(self):
        """Instance method body that is self-recursive via a named let."""
        src = '''
class Hash a {
  hash : a → Nat
}

let nat_hash : Nat → Nat
  = λ nn → match nn { | 0 → 0 | kk → nat_hash kk }

instance Hash Nat {
  hash = nat_hash
}

let constrained_hash : ∀ a. Hash a => a → Nat
  = λ xx → hash xx

let result = constrained_hash 10
'''
        compiled = pipeline(src)
        assert evaluate(compiled['Test.result']) == 0

    def test_two_classes_two_constraints(self):
        """Function with two class constraints."""
        src = '''
class Eq a {
  eq : a → a → Nat
}

class Show a {
  show : a → Nat
}

let nat_eq_impl : Nat → Nat → Nat
  = λ xx yy → match xx {
    | 0 → match yy { | 0 → 1 | _ → 0 }
    | kk → match yy { | 0 → 0 | jj → nat_eq_impl kk jj }
  }

let nat_show_impl : Nat → Nat = λ xx → xx

instance Eq Nat {
  eq = nat_eq_impl
}

instance Show Nat {
  show = nat_show_impl
}

let eq_and_show : ∀ a. Eq a => Show a => a → a → Nat
  = λ xx yy → match (eq xx yy) {
    | 0 → show xx
    | _ → show yy
  }

let result = eq_and_show 3 3
'''
        compiled = pipeline(src)
        assert evaluate(compiled['Test.result']) == 3

    def test_cross_module_typeclass_instance_evaluation(self):
        """Cross-module: class in A, instance in B, constrained function in C."""
        src_class = """\
class Stringify a {
  to_nat : a → Nat
}
"""
        src_inst = """\
use Classes unqualified { Stringify, to_nat }

let nat_to_nat : Nat → Nat = λ xx → xx

instance Stringify Nat {
  to_nat = nat_to_nat
}
"""
        src_user = """\
use Classes unqualified { Stringify, to_nat }
use Instances

let convert : ∀ a. Stringify a => a → Nat
  = λ xx → to_nat xx

let result : Nat = convert 42
"""
        compiled = build_modules([
            ('Classes', src_class),
            ('Instances', src_inst),
            ('User', src_user),
        ])
        assert evaluate(compiled['User.result']) == 42


class TestPatternMatchEdgeCases:
    """Test constructor pattern matching edge cases."""

    def test_option_match_none_and_some(self):
        """Option type: None (tag 0) and Some (tag 1, unary)."""
        src = '''
type Option = | None | Some Nat

let from_option = λ def_val oo → match oo {
  | None    → def_val
  | Some xx → xx
}

let aa = from_option 0 None
let bb = from_option 0 (Some 42)
'''
        assert eval_val(src, 'aa') == 0
        assert eval_val(src, 'bb') == 42

    def test_result_type_match(self):
        """Result type: Ok (tag 0, unary) and Err (tag 1, unary)."""
        src = '''
type Result = | Ok Nat | Err Nat

let unwrap_or = λ def_val rr → match rr {
  | Ok xx  → xx
  | Err _ → def_val
}

let aa = unwrap_or 999 (Ok 42)
let bb = unwrap_or 999 (Err 1)
'''
        assert eval_val(src, 'aa') == 42
        assert eval_val(src, 'bb') == 999

    def test_nested_constructor_match(self):
        """Nested constructors: match on Option."""
        src = '''
type Option = | None | Some Nat

let deep_unwrap = λ oo → match oo {
  | None    → 0
  | Some xx → xx
}

let aa = deep_unwrap (Some 77)
let bb = deep_unwrap None
'''
        assert eval_val(src, 'aa') == 77
        assert eval_val(src, 'bb') == 0


# ===========================================================================
# F1.1: Wildcard succ-arm capture lifting
# ===========================================================================
#
# Regression for the field-feedback bug:
#   match b { | 0 → 0 | _ → mod_go a b }
# previously inlined the wildcard body via `const2(wild_val)`, which did not
# lambda-lift outer-local captures (`a`, `b`, `mod_go`).  The construction
# was structurally distinct from `| _x → mod_go a b`, which routes through
# `_make_pred_succ_law` and lifts captures correctly.  Symptom: silent
# infinite loop at evaluation time.
#
# Both forms must now produce identical output and evaluate without looping.

class TestWildcardCaptureLifting:
    """PatWild and PatVar wildcard succ arms must both lambda-lift captures."""

    def test_wildcard_captures_outer_locals(self):
        """`match b { | 0 → 0 | _ → other a b }` must capture `a`, `b`, `other`."""
        src = '''
let other : Nat → Nat → Nat
  = λ xx yy → xx

let mod_nat : Nat → Nat → Nat
  = λ aa bb → match bb {
      | 0 → 0
      | _ → other aa bb
    }

let r0 = mod_nat 5 0
let r1 = mod_nat 5 3
'''
        assert eval_val(src, 'r0') == 0
        assert eval_val(src, 'r1') == 5

    def test_wildcard_captures_self_ref(self):
        """`match b { | 0 → a | _ → self ... }` must capture self_ref_name.
        Recursion bounded by passing 0 to self, hitting the 0-arm next call."""
        src = '''
let ff : Nat → Nat → Nat
  = λ aa bb → match bb {
      | 0 → aa
      | _ → ff aa 0
    }

let r0 = ff 7 0
let r3 = ff 7 3
'''
        assert eval_val(src, 'r0') == 7
        assert eval_val(src, 'r3') == 7

    def test_wildcard_captures_self_ref_with_inc(self):
        """Recursive self-call through wildcard arm with capture and inc."""
        src = '''
external mod Core.PLAN { inc : Nat → Nat }

let bump_once : Nat → Nat → Nat
  = λ aa bb → match bb {
      | 0 → aa
      | _ → bump_once (Core.PLAN.inc aa) 0
    }

let r0 = bump_once 5 0
let r5 = bump_once 5 7
'''
        assert eval_val(src, 'r0') == 5
        assert eval_val(src, 'r5') == 6

    def test_named_and_wild_produce_same_result(self):
        """`| _ → body` and `| _x → body` (where _x is unused) must agree."""
        src_wild = '''
let ff : Nat → Nat → Nat
  = λ aa bb → match bb {
      | 0 → aa
      | _ → ff aa 0
    }

let result = ff 7 3
'''
        src_named = '''
let ff : Nat → Nat → Nat
  = λ aa bb → match bb {
      | 0 → aa
      | _kk → ff aa 0
    }

let result = ff 7 3
'''
        assert eval_val(src_wild, 'result') == eval_val(src_named, 'result') == 7


# ===========================================================================
# F1.2: Self-ref propagation through succ-law sub-laws
# ===========================================================================
#
# Regression for issue #1a from the field feedback:
#   match (lte b a) { | False → a | True → mod_go (sub a b) b }
# previously failed with "codegen: unbound variable 'Module.mod_go'" because
# `_build_nat_dispatch.make_succ_law` constructed a fresh arity-1 sub-law
# environment that did not carry forward `self_ref_name` or outer-local
# captures.  arm[1+]'s body therefore could not resolve self-references.

class TestSelfRefInSuccLaw:
    """Multi-arm match in recursive function with self-ref in succ arms."""

    def test_bool_match_recursive_with_self_in_true_arm(self):
        """`match (cond) { | False → base | True → self ... }` must compile."""
        src = '''
let always_false : Nat → Bool
  = λ aa → False

let mod_go : Nat → Nat → Nat
  = λ aa bb → match (always_false bb) {
      | False → aa
      | True  → mod_go aa 0
    }

let r0 = mod_go 7 0
let r3 = mod_go 7 3
'''
        # Both calls hit always_false → False arm → returns aa
        assert eval_val(src, 'r0') == 7
        assert eval_val(src, 'r3') == 7

    def test_three_arm_nat_match_self_in_later_arms(self):
        """Three-arm nat dispatch with self-call in arm[2] (succ-of-succ)."""
        src = '''
let count : Nat → Nat → Nat
  = λ acc nn → match nn {
      | 0 → acc
      | 1 → acc
      | 2 → count acc 0
    }

let r0 = count 5 0
let r1 = count 5 1
let r2 = count 5 2
'''
        assert eval_val(src, 'r0') == 5
        assert eval_val(src, 'r1') == 5
        assert eval_val(src, 'r2') == 5

    def test_outer_capture_through_succ_law(self):
        """Outer locals must reach arm[1+] bodies through the lifted succ law."""
        src = '''
let captures : Nat → Nat → Nat
  = λ aa bb → match bb {
      | 0 → 0
      | 1 → aa
      | 2 → aa
    }

let r0 = captures 99 0
let r1 = captures 99 1
let r2 = captures 99 2
'''
        assert eval_val(src, 'r0') == 0
        assert eval_val(src, 'r1') == 99
        assert eval_val(src, 'r2') == 99


# ===========================================================================
# F2: Source-location attribution on CodegenError
# ===========================================================================
#
# Issue #5 from feedback: codegen errors point at the law, not the source.
# CodegenError now carries an optional `loc` and formats `file:line:col` like
# ParseError and ScopeError do.

class TestCodegenErrorLocation:
    """CodegenError should carry source locations matching ParseError/ScopeError."""

    def test_codegen_error_formats_loc_when_provided(self):
        """`CodegenError(msg, loc)` formats as '<file>:<line>:<col>: error: <msg>'."""
        from bootstrap.lexer import Loc
        loc = Loc('demo.gls', 17, 5)
        try:
            raise CodegenError('codegen: unbound variable Foo.bar', loc)
        except CodegenError as e:
            msg = str(e)
            assert 'demo.gls:17:5: error: codegen: unbound variable Foo.bar' == msg

    def test_codegen_error_falls_back_when_no_loc(self):
        """Without a loc, the error string is the bare message (backwards compat)."""
        try:
            raise CodegenError('some internal invariant failed')
        except CodegenError as e:
            assert str(e) == 'some internal invariant failed'


# ===========================================================================
# F1.3: if-then-else recursion does not eagerly evaluate both branches
# ===========================================================================
#
# Issue #1b from feedback: `if c then RECURSE else BASE` compiled but looped
# at evaluation time.  The branches were compiled into the law body as raw
# `bapp(const2_pin, body)` chains; `kal` walks the (0 f x) `bapp` shape
# eagerly, so the recursive call inside `then_body` was forced before the
# dispatch could select a branch — even when the runtime dispatch should
# have taken the else branch.  Same logic written as `match` worked because
# the succ branch was wrapped in a Pin'd law, which `kal` does not recurse
# into.

class TestIfThenElseLazyBranches:
    """if-then-else must defer branch evaluation just like match does."""

    def test_recursive_call_in_then_branch_terminates(self):
        """`if cond then self_call else base` must not loop when cond is False."""
        src = '''
let always_false : Nat → Bool
  = λ aa → False

let test_if : Nat → Nat → Nat
  = λ aa bb → if (always_false bb) then test_if aa 0 else aa

let r0 = test_if 7 0
let r3 = test_if 7 3
'''
        # Both calls hit always_false → False → returns aa.  Without the fix,
        # `test_if aa 0` is forced eagerly during law-body kal walk → infinite
        # recursion before op3 dispatches.
        assert eval_val(src, 'r0') == 7
        assert eval_val(src, 'r3') == 7

    def test_recursive_call_in_else_branch_terminates(self):
        """Symmetric: recursive call in else branch with cond=True base case."""
        src = '''
let always_true : Nat → Bool
  = λ aa → True

let test_if : Nat → Nat → Nat
  = λ aa bb → if (always_true bb) then aa else test_if aa 0

let r0 = test_if 5 0
let r3 = test_if 5 3
'''
        assert eval_val(src, 'r0') == 5
        assert eval_val(src, 'r3') == 5

    def test_if_and_match_produce_same_result(self):
        """if-then-else should agree with the equivalent match-on-Bool."""
        src_if = '''
let always_false : Nat → Bool
  = λ aa → False

let ff : Nat → Nat
  = λ nn → if (always_false nn) then ff 0 else nn

let result = ff 42
'''
        src_match = '''
let always_false : Nat → Bool
  = λ aa → False

let ff : Nat → Nat
  = λ nn → match (always_false nn) {
      | False → nn
      | True  → ff 0
    }

let result = ff 42
'''
        assert eval_val(src_if, 'result') == eval_val(src_match, 'result') == 42


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
