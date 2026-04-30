"""
BPLAN named-op dependencies of the Gallowglass codegen.

Per `DECISIONS.md §"Upstream PLAN authority"`, the canonical PLAN spec is
`vendor/reaver/src/hs/Plan.hs`. PLAN proper and the Plan Asm text format are
frozen; the BPLAN named-op set may drift over time.

This module enumerates every BPLAN intrinsic that gallowglass-emitted code
relies on, by name and arity. `tests/sanity/test_bplan_deps.py` greps the
pinned `vendor/reaver/src/hs/Plan.hs` and asserts presence at the right arity.
This is the canary that fires when `vendor.lock` is bumped to a Reaver SHA
that has renamed, removed, or rearity'd one of our deps.

Each entry: `name -> arity`.

Categories:
  Core primitives — needed by codegen for opcode-equivalent operations.
  Arithmetic/bytes — needed by Core.Nat / Core.Text / Core.Bytes prelude.
  RPLAN — needed for I/O (deferred; see Phase G).
"""

from __future__ import annotations


# Core primitives gallowglass emits directly from codegen.
# Replaces the legacy P(N(0))..P(N(4)) opcode-pin shape with BPLAN named-Law
# references that delegate to (P("B")) ("Name" args).
CORE_PRIMITIVES: dict[str, int] = {
    'Pin':   1,    # was P(N(0))
    'Law':   3,    # was P(N(1)) / MkLaw
    'Inc':   1,    # was P(N(2))
    'Elim':  6,    # was P(N(3)) / Case_  — canonical 6-arity dispatch
    'Force': 1,    # was P(N(4))
}


# Prelude arithmetic / introspection — used by the BPLAN harness as Python
# fast-path jets, but their existence in Reaver is what makes gallowglass-
# emitted prelude code actually run on Reaver at all.
PRELUDE_INTRINSICS: dict[str, int] = {
    # Arithmetic
    'Add': 2,
    'Sub': 2,
    'Mul': 2,
    'Div': 2,
    'Mod': 2,
    'Dec': 1,
    # Comparison
    'Eq':  2,
    'Cmp': 2,
    # Bit ops (used by bytes encoding)
    'Lsh': 2,
    'Rsh': 2,
    # Introspection (used in the harness; future codegen may emit these)
    'Type':  1,
    'IsPin': 1,
    'IsLaw': 1,
    'IsApp': 1,
    'IsNat': 1,
    'Hd':    1,
    'Sz':    1,
    'Unpin': 1,
    # Sequencing
    'Seq':  2,
    # Diagnostics
    'Trace': 2,
}


# All BPLAN deps as a single dict, suitable for the sanity test.
ALL_DEPS: dict[str, int] = {
    **CORE_PRIMITIVES,
    **PRELUDE_INTRINSICS,
}


# strNat encoding helper — packs UTF-8 bytes little-endian into a Python int.
# Mirrors `bootstrap.codegen.encode_name`.  Defined here so `bplan_deps`
# stays free of bootstrap dependencies and can be imported from tests.
def str_nat(s: str) -> int:
    """Encode s as a little-endian nat (Reaver's `strNat`)."""
    raw = s.encode('utf-8')
    return int.from_bytes(raw, 'little')
