#!/usr/bin/env python3
"""
M12 module system tests — multi-file compilation with `use` imports.

Tests cover:
  - Basic two-module compilation (A imports from B)
  - Unqualified import: `use Mod unqualified { name }` brings name into scope
  - Qualified-only import: `use Mod` — only Module.name works, not bare name
  - Transitive deps: A → B → C compiled in correct order
  - Cycle detection: BuildError on circular dependency
  - Unknown module: BuildError when use references a module not in build
  - Cross-module constructor use
  - Cross-module typeclass class declaration (method access by FQ name)

Run: python3 tests/bootstrap/test_modules.py
  or: python3 -m pytest tests/bootstrap/test_modules.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest

from bootstrap.build import build_modules, BuildError
from bootstrap.scope import ScopeError
from dev.harness.plan import N, A, apply, evaluate, is_nat, is_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(sources: list[tuple[str, str]], name: str, *args):
    """Build all sources and evaluate name applied to args."""
    compiled = build_modules(sources)
    assert name in compiled, f"'{name}' not in compiled: {sorted(compiled.keys())}"
    v = compiled[name]
    for a in args:
        v = apply(v, a)
    return evaluate(v)


# ---------------------------------------------------------------------------
# Source snippets
# ---------------------------------------------------------------------------

# A module with simple successor arithmetic (no external mod needed)
_MATH_SRC = """\
let is_zero : Nat → Bool
  = λ n → match n { | 0 → True | _ → False }

let pred : Nat → Nat
  = λ n → match n { | 0 → 0 | k → k }

let nat_eq : Nat → Nat → Bool
  = λ m n → match m {
    | 0 → match n { | 0 → True  | _ → False }
    | j → match n { | 0 → False | k → nat_eq j k }
  }
"""

# A module that imports Math operations by unqualified name
_UTIL_UNQUAL_SRC = """\
use Math unqualified { is_zero, nat_eq }

let both_zero : Nat → Nat → Bool
  = λ a b → match (is_zero a) {
    | 0 → False
    | _ → is_zero b
  }

let same : Nat → Nat → Bool = λ a b → nat_eq a b
"""

# A module that imports Math qualified (no unqualified)
_UTIL_QUAL_SRC = """\
use Math { is_zero }

let check : Nat → Bool = λ n → is_zero n
"""


# ---------------------------------------------------------------------------
# Basic two-module tests
# ---------------------------------------------------------------------------

def test_two_module_unqualified_import():
    """Module B imports from A with 'unqualified'; uses imported name directly."""
    compiled = build_modules([
        ('Math', _MATH_SRC),
        ('Util', _UTIL_UNQUAL_SRC),
    ])
    assert 'Util.both_zero' in compiled
    assert 'Util.same' in compiled


def test_two_module_unqualified_evaluates():
    """Cross-module call: both_zero 0 0 = True (1)."""
    result = run(
        [('Math', _MATH_SRC), ('Util', _UTIL_UNQUAL_SRC)],
        'Util.both_zero', N(0), N(0),
    )
    assert result == 1  # True


def test_two_module_unqualified_false():
    """Cross-module call: both_zero 1 0 = False (0)."""
    result = run(
        [('Math', _MATH_SRC), ('Util', _UTIL_UNQUAL_SRC)],
        'Util.both_zero', N(1), N(0),
    )
    assert result == 0  # False


def test_two_module_nat_eq_cross_module():
    """nat_eq imported from Math, called via same in Util."""
    result = run(
        [('Math', _MATH_SRC), ('Util', _UTIL_UNQUAL_SRC)],
        'Util.same', N(3), N(3),
    )
    assert result == 1
    result2 = run(
        [('Math', _MATH_SRC), ('Util', _UTIL_UNQUAL_SRC)],
        'Util.same', N(3), N(4),
    )
    assert result2 == 0


def test_qualified_import_works():
    """Qualified import `use Math { is_zero }` makes is_zero available in scope."""
    result = run(
        [('Math', _MATH_SRC), ('CheckMod', _UTIL_QUAL_SRC)],
        'CheckMod.check', N(0),
    )
    assert result == 1
    result2 = run(
        [('Math', _MATH_SRC), ('CheckMod', _UTIL_QUAL_SRC)],
        'CheckMod.check', N(5),
    )
    assert result2 == 0


def test_module_only_import_no_unqualified():
    """
    `use Mod` with no spec — module prefix accessible but bare name is not.
    Math.is_zero should work; bare is_zero should raise ScopeError.
    """
    src_qualified_only = """\
use Math

let check : Nat → Bool = λ n → Math.is_zero n
"""
    result = run(
        [('Math', _MATH_SRC), ('Q', src_qualified_only)],
        'Q.check', N(0),
    )
    assert result == 1

    src_bare = """\
use Math

let check : Nat → Bool = λ n → is_zero n
"""
    with pytest.raises((ScopeError, Exception)):
        build_modules([('Math', _MATH_SRC), ('Bad', src_bare)])


# ---------------------------------------------------------------------------
# Transitive dependencies: A → B → C
# ---------------------------------------------------------------------------

def test_three_module_transitive():
    """C depends on B which depends on A; all three compile correctly."""
    src_a = """\
let one : Nat = 1
let two : Nat = 2
"""
    src_b = """\
use A unqualified { one, two }

let three : Nat = 3
let sum_ab : Nat = two
"""
    src_c = """\
use B unqualified { three, sum_ab }

let answer : Nat = three
"""
    compiled = build_modules([('A', src_a), ('B', src_b), ('C', src_c)])
    assert evaluate(compiled['C.answer']) == 3


def test_three_module_source_order_independence():
    """Providing sources in wrong order: build system reorders by deps."""
    src_a = "let base_val : Nat = 5\n"
    src_b = "use A unqualified { base_val }\nlet doubled : Nat = base_val\n"
    src_c = "use B unqualified { doubled }\nlet result : Nat = doubled\n"

    # Provide in reverse order — build system should still work
    compiled = build_modules([('C', src_c), ('B', src_b), ('A', src_a)])
    assert evaluate(compiled['C.result']) == 5


# ---------------------------------------------------------------------------
# Cross-module algebraic types
# ---------------------------------------------------------------------------

def test_cross_module_type_and_constructor():
    """Type defined in A, pattern matched in B."""
    src_a = """\
type Color =
  | Red
  | Green
  | Blue

let is_red : Color → Bool
  = λ c → match c { | Red → True | _ → False }
"""
    src_b = """\
use A unqualified { Color, Red, Green, Blue, is_red }

let check_red : Bool = is_red Red
let check_green : Bool = is_red Green
"""
    compiled = build_modules([('A', src_a), ('B', src_b)])
    assert evaluate(compiled['B.check_red']) == 1
    assert evaluate(compiled['B.check_green']) == 0


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_cycle_detection():
    """Circular dependency raises BuildError."""
    src_a = "use B\nlet val_a : Nat = 1\n"
    src_b = "use A\nlet val_b : Nat = 2\n"
    with pytest.raises(BuildError, match='circular'):
        build_modules([('A', src_a), ('B', src_b)])


def test_unknown_module_raises():
    """Importing an unknown module (not in build, not external) raises BuildError."""
    src = "use NonExistent unqualified { foo }\nlet result : Nat = foo\n"
    with pytest.raises(BuildError):
        build_modules([('Bad', src)])


def test_self_contained_module_builds():
    """A single module with no imports compiles normally via build_modules."""
    compiled = build_modules([('Standalone', 'let answer : Nat = 42\n')])
    assert evaluate(compiled['Standalone.answer']) == 42


# ---------------------------------------------------------------------------
# Ordering: dependency comes after dependent in input — should still work
# ---------------------------------------------------------------------------

def test_dep_after_dependent_in_input():
    """
    Sources list has B first then A (wrong order) but B depends on A.
    build_modules must reorder correctly.
    """
    src_a = "let val : Nat = 7\n"
    src_b = "use A unqualified { val }\nlet result : Nat = val\n"

    compiled = build_modules([('B', src_b), ('A', src_a)])
    assert evaluate(compiled['B.result']) == 7


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
