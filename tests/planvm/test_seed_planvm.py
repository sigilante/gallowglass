#!/usr/bin/env python3
"""
planvm seed validation tests — DEPRECATION SHIM (AUDIT.md C2).

xocore-tech/PLAN's xplan VM is no longer a Gallowglass deployment
target after the Reaver migration (Phase E, 2026-04-30); see
`DECISIONS.md §"Why XPLAN compatibility is being abandoned"`.  All
~110 tests that previously lived here were unconditionally skipped
via `requires_planvm` and contributed no signal — only collection
time and noise in CI output.

This module is now a slim shim that exports the three names
historical importers still depend on:

  - `requires_planvm` — `unittest.skip(reason)` decorator that
    *always* skips, with a message pointing at the canonical
    runtime gate (`tests/reaver/`).
  - `seed_loads(seed_bytes) -> bool` — stub returning `False`.
    Consumers always guard their tests with `requires_planvm`
    first, so this is never called in practice.
  - `PLANVM` — env var fallback, preserved for any tooling that
    reads it.

Test bodies were removed in C2; git history preserves them.  The
runtime gate has moved to `tests/reaver/`.
"""

import os
import unittest


PLANVM = os.environ.get('PLANVM', 'planvm')


requires_planvm = unittest.skip(
    'xocore-tech/PLAN xplan VM no longer a Gallowglass target — see '
    'DECISIONS.md §"Why XPLAN compatibility is being abandoned" (2026-04-30). '
    'For runtime validation, see tests/reaver/.'
)


def seed_loads(seed_bytes: bytes) -> bool:
    """Stub: planvm is unavailable.  Consumers always skip via
    `requires_planvm` first; if this is ever called, return False so
    a passing assertion path doesn't claim a non-existent runtime
    accepted the seed."""
    return False


def planvm_available() -> bool:
    """Stub: planvm is no longer a target.  Always False."""
    return False


def compile_to_seed(src: str, name: str, module: str = 'Test') -> bytes:
    """Stub for historical importers.  Compiles src to a legacy seed
    byte string via the bootstrap, but the result has no consumer in
    the active test suite — every caller is gated behind
    `requires_planvm` and skipped before evaluation."""
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    from bootstrap.emit import emit
    prog = parse(lex(src, '<shim>'), '<shim>')
    resolved, _env = resolve(prog, module, {}, '<shim>')
    compiled = compile_program(resolved, module)
    return emit(compiled, f'{module}.{name}')
