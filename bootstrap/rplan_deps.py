"""
RPLAN named-op dependencies of the Gallowglass codegen.

Per Sol (2026-04-30), RPLAN is **tentative, not frozen** — names, arities, and
the calling shape may change in future Reaver versions. RPLAN sits one tier
above BPLAN in churn risk.

This module enumerates every RPLAN intrinsic gallowglass-emitted code relies
on, by name and arity. `tests/sanity/test_rplan_deps.py` greps the pinned
`vendor/reaver/src/hs/Plan.hs:rplan` and asserts presence at the right arity.
This is the canary that fires when `vendor.lock` is bumped to a Reaver SHA
that has renamed, removed, or rearity'd one of our deps.

RPLAN ops (op 82) provide stdio + filesystem + clock primitives. They are
used for I/O-driven gallowglass programs (REPLs, demos that read stdin).
The set is mirrored by the `(rplan ...)` macro in
`vendor/reaver/src/plan/boot.plan`, which makes each name available as a
bare symbol in any Plan Asm program that starts with `@boot`.

Each entry: `name -> arity`.
"""

from __future__ import annotations


# RPLAN named ops, all dispatched via (P("R")) ("Name" args) at runtime
# per `vendor/reaver/src/hs/Plan.hs:rplan` (op 82). Arities match the
# case patterns there.
#
# We bind only the stdio/filesystem/clock subset. The actor and network
# ops in the same case-of (Spawn, Send, Recv, Listen, Accept, Read,
# Write, ...) are deliberately excluded — they are higher-churn and
# require their own design pass before gallowglass emits them.
RPLAN_OPS: dict[str, int] = {
    'Input':    1,   # n → bytes_nat (bytesBar-encoded: little-endian + high-bit length)
    'Output':   1,   # nat → 0 (writes natBytes(nat) to stdout)
    'Warn':     1,   # nat → 0 (writes natBytes(nat) to stderr)
    'ReadFile': 1,   # filename_strnat → contents_nat (bytesBar) or 0 on error
    'Print':    1,   # strnat → 0 (pretty-prints natStr(s))
    'Stamp':    1,   # filename_strnat → mtime_posix or 0 on error
    'Now':      1,   # _ → POSIX wall clock seconds
}


# Re-export `str_nat` from bplan_deps for the rare consumer that wants
# Reaver's strNat encoding without depending on the BPLAN module.
from bootstrap.bplan_deps import str_nat as str_nat  # noqa: F401
