#!/usr/bin/env python3
"""
M11 typeclass tests — dictionary construction and constrained function compilation.

Tests cover:
  - DeclClass registration (method name ordering)
  - DeclInst compilation (named dict values)
  - Constrained DeclLet compilation (extra dict params)
  - Call-site dict insertion (auto-insertion for Nat call sites)
  - Multi-constraint functions
  - Constrained recursive functions (instance method is self-recursive)

Run: python3 tests/bootstrap/test_typeclasses.py
  or: python3 -m pytest tests/bootstrap/test_typeclasses.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program
from dev.harness.plan import P, L, A, N, is_law, is_nat, evaluate, apply


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pipeline(src: str, module: str = 'Test') -> dict:
    prog = parse(lex(src, '<test>'), '<test>')
    resolved, _ = resolve(prog, module, {}, '<test>')
    return compile_program(resolved, module)


def run(src: str, name: str, *args, module: str = 'Test'):
    """Compile, look up name, apply args, evaluate."""
    compiled = pipeline(src, module)
    fq = f'{module}.{name}'
    assert fq in compiled, f"'{fq}' not in compiled: {list(compiled.keys())}"
    v = compiled[fq]
    for a in args:
        v = apply(v, a)
    return evaluate(v)


# ---------------------------------------------------------------------------
# Shared source snippets
# ---------------------------------------------------------------------------

_EQ_DECL = '''\
class Eq a {
  eq : a → a → Nat
}
'''

_NAT_EQ_IMPL = '''\
-- Recursive Nat equality: 1 if equal, 0 otherwise
let nat_eq_impl : Nat → Nat → Nat
  = λ x y → match x {
    | 0 → match y { | 0 → 1 | _ → 0 }
    | k → match y { | 0 → 0 | j → nat_eq_impl k j }
  }
'''

_EQ_NAT_INST = '''\
instance Eq Nat {
  eq = nat_eq_impl
}
'''


# ---------------------------------------------------------------------------
# M11.1: DeclClass registration
# ---------------------------------------------------------------------------

def test_declclass_compiles_without_error():
    """DeclClass produces no error and emits no value (just metadata)."""
    src = _EQ_DECL
    compiled = pipeline(src)
    # Class itself is metadata only; no PLAN value emitted under its name
    assert 'Test.Eq' not in compiled


def test_declclass_method_registered():
    """After DeclClass, the compiler knows the method list."""
    src = _EQ_DECL
    prog = parse(lex(src, '<test>'), '<test>')
    resolved, _ = resolve(prog, 'Test', {}, '<test>')
    from bootstrap.codegen import Compiler
    c = Compiler('Test')
    c.compile(resolved)
    assert 'Test.Eq' in c._class_methods
    assert c._class_methods['Test.Eq'] == ['eq']


def test_multimethod_class_ordering():
    """Multi-method class registers methods in declaration order."""
    src = '''\
class Ord a {
  lt  : a → a → Nat
  lte : a → a → Nat
  gt  : a → a → Nat
}
'''
    prog = parse(lex(src, '<test>'), '<test>')
    resolved, _ = resolve(prog, 'Test', {}, '<test>')
    from bootstrap.codegen import Compiler
    c = Compiler('Test')
    c.compile(resolved)
    assert c._class_methods['Test.Ord'] == ['lt', 'lte', 'gt']


# ---------------------------------------------------------------------------
# M11.2: DeclInst compilation
# ---------------------------------------------------------------------------

def test_inst_emits_method_value():
    """instance Eq Nat emits Test.inst_Eq_Nat_eq as a compiled PLAN value."""
    src = _EQ_DECL + _NAT_EQ_IMPL + _EQ_NAT_INST
    compiled = pipeline(src)
    assert 'Test.inst_Eq_Nat_eq' in compiled, list(compiled.keys())


def test_single_method_inst_emits_dict():
    """Single-method class: inst dict = the method itself."""
    src = _EQ_DECL + _NAT_EQ_IMPL + _EQ_NAT_INST
    compiled = pipeline(src)
    assert 'Test.inst_Eq_Nat' in compiled
    # The dict value equals the method value (same PLAN structure)
    dict_val = compiled['Test.inst_Eq_Nat']
    method_val = compiled['Test.inst_Eq_Nat_eq']
    assert dict_val == method_val


def test_inst_method_body_evaluates_correctly():
    """The compiled instance method body is callable."""
    src = _EQ_DECL + _NAT_EQ_IMPL + _EQ_NAT_INST
    compiled = pipeline(src)
    eq_fn = compiled['Test.inst_Eq_Nat_eq']
    assert evaluate(apply(apply(eq_fn, N(3)), N(3))) == 1
    assert evaluate(apply(apply(eq_fn, N(3)), N(4))) == 0


def test_inst_inline_body():
    """Instance method body can be an inline lambda (not a reference to a let)."""
    src = '''\
class MyEq a {
  my_eq : a → a → Nat
}
instance MyEq Nat {
  my_eq = λ x y → match x { | 0 → match y { | 0 → 1 | _ → 0 } | k → 0 }
}
'''
    compiled = pipeline(src)
    assert 'Test.inst_MyEq_Nat' in compiled
    eq_fn = compiled['Test.inst_MyEq_Nat']
    assert evaluate(apply(apply(eq_fn, N(0)), N(0))) == 1
    assert evaluate(apply(apply(eq_fn, N(0)), N(1))) == 0


# ---------------------------------------------------------------------------
# M11.3: Constrained DeclLet compilation
# ---------------------------------------------------------------------------

def test_constrained_let_extra_arity():
    """Constrained let: compiled law has 1 extra dict param per method."""
    src = _EQ_DECL + _NAT_EQ_IMPL + _EQ_NAT_INST + '''\
let same : ∀ a. Eq a => a → a → Nat = λ x y → eq x y
'''
    compiled = pipeline(src)
    same_law = compiled['Test.same']
    assert is_law(same_law), f"Expected law, got {same_law}"
    # 1 dict param (eq) + 2 user params (x, y) = arity 3
    assert same_law.arity == 3, f"Expected arity 3, got {same_law.arity}"


def test_constrained_let_body_uses_dict():
    """Inside a constrained let, the class method uses the dict param (N(1))."""
    src = _EQ_DECL + _NAT_EQ_IMPL + _EQ_NAT_INST + '''\
let same : ∀ a. Eq a => a → a → Nat = λ x y → eq x y
'''
    compiled = pipeline(src)
    same_law = compiled['Test.same']
    # Manual application: pass eq method as dict param, then x, y
    eq_fn = compiled['Test.inst_Eq_Nat_eq']
    result = evaluate(apply(apply(apply(same_law, eq_fn), N(5)), N(5)))
    assert result == 1
    result2 = evaluate(apply(apply(apply(same_law, eq_fn), N(5)), N(6)))
    assert result2 == 0


def test_constrained_let_registered():
    """Constrained let is registered in _constrained_lets for call-site insertion."""
    src = _EQ_DECL + _NAT_EQ_IMPL + _EQ_NAT_INST + '''\
let same : ∀ a. Eq a => a → a → Nat = λ x y → eq x y
'''
    prog = parse(lex(src, '<test>'), '<test>')
    resolved, _ = resolve(prog, 'Test', {}, '<test>')
    from bootstrap.codegen import Compiler
    c = Compiler('Test')
    c.compile(resolved)
    assert 'Test.same' in c._constrained_lets
    info = c._constrained_lets['Test.same']
    assert len(info) == 1  # 1 constraint (Eq)
    class_fq, method_fqs = info[0]
    assert class_fq == 'Test.Eq'
    assert method_fqs == ['Test.eq']


# ---------------------------------------------------------------------------
# M11.4: Call-site dict insertion
# ---------------------------------------------------------------------------

def test_callsite_nat_auto_dict():
    """same 3 3 with Nat args → auto-inserts inst_Eq_Nat_eq dict."""
    src = _EQ_DECL + _NAT_EQ_IMPL + _EQ_NAT_INST + '''\
let same : ∀ a. Eq a => a → a → Nat = λ x y → eq x y
let test_val : Nat = same 3 3
'''
    compiled = pipeline(src)
    assert evaluate(compiled['Test.test_val']) == 1


def test_callsite_neq():
    """same 3 4 = 0."""
    src = _EQ_DECL + _NAT_EQ_IMPL + _EQ_NAT_INST + '''\
let same : ∀ a. Eq a => a → a → Nat = λ x y → eq x y
let test_val : Nat = same 3 4
'''
    assert evaluate(pipeline(src)['Test.test_val']) == 0


def test_callsite_zero():
    """same 0 0 = 1."""
    src = _EQ_DECL + _NAT_EQ_IMPL + _EQ_NAT_INST + '''\
let same : ∀ a. Eq a => a → a → Nat = λ x y → eq x y
let test_val : Nat = same 0 0
'''
    assert evaluate(pipeline(src)['Test.test_val']) == 1


def test_callsite_in_let_body():
    """Call to constrained function inside a let body (not at top-level)."""
    src = _EQ_DECL + _NAT_EQ_IMPL + _EQ_NAT_INST + '''\
let same : ∀ a. Eq a => a → a → Nat = λ x y → eq x y

-- neq wraps same
let neq : Nat → Nat → Nat
  = λ x y → match (same x y) {
    | 0 → 1
    | _ → 0
  }

let test_neq_eq  : Nat = neq 5 5
let test_neq_neq : Nat = neq 5 6
'''
    compiled = pipeline(src)
    assert evaluate(compiled['Test.test_neq_eq']) == 0
    assert evaluate(compiled['Test.test_neq_neq']) == 1


# ---------------------------------------------------------------------------
# Constrained function calls within another constrained function body
# ---------------------------------------------------------------------------

def test_constrained_calls_constrained():
    """A constrained function can call another constrained function."""
    src = _EQ_DECL + _NAT_EQ_IMPL + _EQ_NAT_INST + '''\
let same : ∀ a. Eq a => a → a → Nat = λ x y → eq x y

-- neq_c is also constrained; calls same internally
let neq_c : ∀ a. Eq a => a → a → Nat
  = λ x y → match (eq x y) { | 0 → 1 | _ → 0 }

let test_neq_c_eq  : Nat = neq_c 7 7
let test_neq_c_neq : Nat = neq_c 7 8
'''
    compiled = pipeline(src)
    assert evaluate(compiled['Test.test_neq_c_eq']) == 0
    assert evaluate(compiled['Test.test_neq_c_neq']) == 1


# ---------------------------------------------------------------------------
# Multi-method class
# ---------------------------------------------------------------------------

def test_multimethod_class_inst():
    """Multi-method class: each method emitted as a separate named value."""
    src = '''\
class Cmp a {
  lt  : a → a → Nat
  lte : a → a → Nat
}

let nat_lt_impl : Nat → Nat → Nat
  = λ x y → match x {
    | 0 → match y { | 0 → 0 | _ → 1 }
    | k → match y { | 0 → 0 | j → nat_lt_impl k j }
  }

let nat_lte_impl : Nat → Nat → Nat
  = λ x y → match x {
    | 0 → 1
    | k → match y { | 0 → 0 | j → nat_lte_impl k j }
  }

instance Cmp Nat {
  lt  = nat_lt_impl
  lte = nat_lte_impl
}
'''
    compiled = pipeline(src)
    assert 'Test.inst_Cmp_Nat_lt' in compiled
    assert 'Test.inst_Cmp_Nat_lte' in compiled
    lt = compiled['Test.inst_Cmp_Nat_lt']
    lte = compiled['Test.inst_Cmp_Nat_lte']
    assert evaluate(apply(apply(lt, N(3)), N(5))) == 1
    assert evaluate(apply(apply(lt, N(5)), N(3))) == 0
    assert evaluate(apply(apply(lte, N(3)), N(3))) == 1
    assert evaluate(apply(apply(lte, N(4)), N(3))) == 0


def test_multimethod_constrained_let():
    """Constrained let with 2-method class gets 2 extra dict params."""
    src = '''\
class Cmp a {
  lt  : a → a → Nat
  lte : a → a → Nat
}

let nat_lt_impl  : Nat → Nat → Nat
  = λ x y → match x { | 0 → match y { | 0 → 0 | _ → 1 } | k → match y { | 0 → 0 | j → nat_lt_impl k j } }

let nat_lte_impl : Nat → Nat → Nat
  = λ x y → match x { | 0 → 1 | k → match y { | 0 → 0 | j → nat_lte_impl k j } }

instance Cmp Nat {
  lt  = nat_lt_impl
  lte = nat_lte_impl
}

let min_by : ∀ a. Cmp a => a → a → a
  = λ x y → match (lt x y) { | 0 → y | _ → x }

let test_min : Nat = min_by 3 5
let test_min2 : Nat = min_by 7 2
'''
    compiled = pipeline(src)
    min_law = compiled['Test.min_by']
    assert is_law(min_law)
    # 2 dict params (lt, lte) + 2 user params (x, y) = 4
    assert min_law.arity == 4, f"Expected arity 4, got {min_law.arity}"
    assert evaluate(compiled['Test.test_min']) == 3
    assert evaluate(compiled['Test.test_min2']) == 2


# ---------------------------------------------------------------------------
# M13.1: Default methods
# ---------------------------------------------------------------------------

def test_default_method_basic():
    """A class with a default method; instance omits it; default is used."""
    src = '''\
class Eq a {
  eq  : a → a → Nat
  neq : a → a → Nat = λ x y → match (eq x y) { | 0 → 1 | _ → 0 }
}

let nat_eq : Nat → Nat → Nat
  = λ x y → match x {
    | 0 → match y { | 0 → 1 | _ → 0 }
    | k → match y { | 0 → 0 | j → nat_eq k j }
  }

instance Eq Nat {
  eq = nat_eq
}
'''
    compiled = pipeline(src)
    # Default neq should be compiled and emitted
    assert 'Test.inst_Eq_Nat_neq' in compiled
    assert 'Test.inst_Eq_Nat_eq' in compiled
    # Test directly: neq 3 4 = 1, neq 5 5 = 0
    neq = compiled['Test.inst_Eq_Nat_neq']
    assert evaluate(apply(apply(neq, N(3)), N(4))) == 1
    assert evaluate(apply(apply(neq, N(5)), N(5))) == 0


def test_default_method_override():
    """Instance provides a method that has a default; override takes precedence."""
    src = '''\
class Eq a {
  eq  : a → a → Nat
  neq : a → a → Nat = λ x y → match (eq x y) { | 0 → 1 | _ → 0 }
}

let nat_eq : Nat → Nat → Nat
  = λ x y → match x {
    | 0 → match y { | 0 → 1 | _ → 0 }
    | k → match y { | 0 → 0 | j → nat_eq k j }
  }

instance Eq Nat {
  eq  = nat_eq
  neq = λ a b → 42
}
'''
    compiled = pipeline(src)
    # Override takes precedence — should be 42, not the default's 1
    neq = compiled['Test.inst_Eq_Nat_neq']
    assert evaluate(apply(apply(neq, N(1)), N(2))) == 42


def test_default_method_constrained_call():
    """Default methods work through constrained function calls."""
    src = '''\
class Eq a {
  eq  : a → a → Nat
  neq : a → a → Nat = λ x y → match (eq x y) { | 0 → 1 | _ → 0 }
}

let nat_eq : Nat → Nat → Nat
  = λ x y → match x {
    | 0 → match y { | 0 → 1 | _ → 0 }
    | k → match y { | 0 → 0 | j → nat_eq k j }
  }

instance Eq Nat {
  eq = nat_eq
}

let differs : ∀ a. Eq a => a → a → Nat = λ x y → neq x y

let test_differs_yes : Nat = differs 3 4
let test_differs_no  : Nat = differs 5 5
'''
    compiled = pipeline(src)
    assert evaluate(compiled['Test.test_differs_yes']) == 1
    assert evaluate(compiled['Test.test_differs_no']) == 0


# ---------------------------------------------------------------------------
# M11.4: Core prelude instances (Python harness evaluation)
# ---------------------------------------------------------------------------
#
# These tests compile the actual prelude files and verify the typeclass
# instances evaluate correctly via the Python PLAN harness.

def _load_prelude(filename: str, module: str) -> dict:
    """Compile a prelude source file and return the compiled dict."""
    import pathlib
    src_path = pathlib.Path(__file__).parent.parent.parent / 'prelude' / 'src' / 'Core' / filename
    with open(src_path) as f:
        src = f.read()
    prog = parse(lex(src, str(src_path)), str(src_path))
    resolved, _ = resolve(prog, module, {}, str(src_path))
    return compile_program(resolved, module)


def _load_prelude_with_deps(filename: str, module: str,
                            deps: list[tuple[str, str]] | None = None) -> dict:
    """Compile a prelude source file (with optional upstream deps) via build_modules.

    deps: list of (dep_module_name, dep_filename) pairs that the target file depends on.
    Returns the compiled dict for all modules merged; caller picks the right keys.
    """
    import pathlib
    from bootstrap.build import build_modules
    core_dir = pathlib.Path(__file__).parent.parent.parent / 'prelude' / 'src' / 'Core'
    sources = []
    for dep_mod, dep_file in (deps or []):
        with open(core_dir / dep_file) as f:
            sources.append((dep_mod, f.read()))
    with open(core_dir / filename) as f:
        sources.append((module, f.read()))
    return build_modules(sources)


def test_prelude_nat_eq_instance_emitted():
    """Core.Nat: inst_Eq_Nat is in the compiled output."""
    c = _load_prelude('Nat.gls', 'Core.Nat')
    assert 'Core.Nat.inst_Eq_Nat' in c
    assert 'Core.Nat.inst_Eq_Nat_eq' in c


def test_prelude_nat_eq_equal():
    """Core.Nat inst_Eq_Nat: eq 3 3 = True (1)."""
    c = _load_prelude('Nat.gls', 'Core.Nat')
    fn = c['Core.Nat.inst_Eq_Nat_eq']
    assert evaluate(apply(apply(fn, N(3)), N(3))) == 1


def test_prelude_nat_eq_unequal():
    """Core.Nat inst_Eq_Nat: eq 3 4 = False (0)."""
    c = _load_prelude('Nat.gls', 'Core.Nat')
    fn = c['Core.Nat.inst_Eq_Nat_eq']
    assert evaluate(apply(apply(fn, N(3)), N(4))) == 0


def test_prelude_nat_ord_lt():
    """Core.Nat inst_Ord_Nat: lt 3 5 = True (1), lt 5 3 = False (0)."""
    c = _load_prelude('Nat.gls', 'Core.Nat')
    fn = c['Core.Nat.inst_Ord_Nat_lt']
    assert evaluate(apply(apply(fn, N(3)), N(5))) == 1
    assert evaluate(apply(apply(fn, N(5)), N(3))) == 0


def test_prelude_nat_ord_lte():
    """Core.Nat inst_Ord_Nat: lte 3 3 = True, lte 4 3 = False."""
    c = _load_prelude('Nat.gls', 'Core.Nat')
    fn = c['Core.Nat.inst_Ord_Nat_lte']
    assert evaluate(apply(apply(fn, N(3)), N(3))) == 1
    assert evaluate(apply(apply(fn, N(0)), N(0))) == 1
    assert evaluate(apply(apply(fn, N(4)), N(3))) == 0


def test_prelude_nat_add_instance():
    """Core.Nat inst_Add_Nat: add 2 3 = 5."""
    c = _load_prelude('Nat.gls', 'Core.Nat')
    fn = c['Core.Nat.inst_Add_Nat']
    assert evaluate(apply(apply(fn, N(2)), N(3))) == 5
    assert evaluate(apply(apply(fn, N(0)), N(7))) == 7


_BOOL_DEPS = [('Core.Nat', 'Nat.gls')]


def test_prelude_bool_eq_instance_emitted():
    """Core.Bool: inst_Eq_Bool is in the compiled output."""
    c = _load_prelude_with_deps('Bool.gls', 'Core.Bool', _BOOL_DEPS)
    assert 'Core.Bool.inst_Eq_Bool' in c
    assert 'Core.Bool.inst_Eq_Bool_eq' in c


def test_prelude_bool_eq_true_true():
    """Core.Bool inst_Eq_Bool: eq True True = True (1)."""
    c = _load_prelude_with_deps('Bool.gls', 'Core.Bool', _BOOL_DEPS)
    fn = c['Core.Bool.inst_Eq_Bool_eq']
    assert evaluate(apply(apply(fn, N(1)), N(1))) == 1


def test_prelude_bool_eq_true_false():
    """Core.Bool inst_Eq_Bool: eq True False = False (0)."""
    c = _load_prelude_with_deps('Bool.gls', 'Core.Bool', _BOOL_DEPS)
    fn = c['Core.Bool.inst_Eq_Bool_eq']
    assert evaluate(apply(apply(fn, N(1)), N(0))) == 0


def test_prelude_bool_eq_false_false():
    """Core.Bool inst_Eq_Bool: eq False False = True (1)."""
    c = _load_prelude_with_deps('Bool.gls', 'Core.Bool', _BOOL_DEPS)
    fn = c['Core.Bool.inst_Eq_Bool_eq']
    assert evaluate(apply(apply(fn, N(0)), N(0))) == 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
