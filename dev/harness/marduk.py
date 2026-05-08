"""Marduk backend — runtime evaluator alternative to ``dev.harness.bplan``.

Converts gallowglass codegen output (``P``/``L``/``A``/``N`` from
``dev.harness.plan``) into Marduk ``Val`` objects and forces them
through Marduk's spec-faithful interpreter. Intended as a drop-in
replacement for ``dev.harness.bplan.bevaluate`` once codegen no longer
emits direct-dispatch ``<1>``/``<2>`` opcode pins (see commit 91ab3a1
on this branch).

Public surface mirrors ``dev.harness.bplan``:

* :func:`bevaluate(val)` — force ``val`` to WHNF, return result. The
  return value is a Marduk ``Val``; for ``Nat`` results, ``.nat``
  gives the int. Pin / Law / App returns are also Marduk Vals.
* :func:`register_jets(compiled_dict)` — install gallowglass's
  built-in compiler-jet table against the FQ names in ``compiled_dict``.
* :func:`register_prelude_jets(compiled_dict)` — install the
  ``Core.Nat`` / ``Core.Text`` / ``Core.List`` prelude jets.

Both jet-registration functions read the same ``_COMPILER_JETS`` and
``_PRELUDE_JETS`` tables ``dev.harness.bplan`` does, so the migration
inherits jet coverage exactly. The legacy backend stays untouched —
nothing here mutates anything in ``dev/harness/{plan,bplan}.py``.

The converter is recursive on the legacy Val tree. For programs of
realistic size (the calculator demo's compiled ``main`` is ~10 KB of
nested App nodes) the recursion fits inside Python's default
1000-frame limit; for the self-host compiler we bump
``sys.setrecursionlimit`` the same way the legacy harness does.

Caching: each legacy Val (identified by ``id``) is converted once.
Two legacy Vals that share Python identity (which gallowglass codegen
does heavily — every law referenced from multiple call sites is the
same Python object) get the same converted Marduk Val, preserving
the jet-relevant ``id(law.box)`` identity inside Marduk's registry.
"""

from __future__ import annotations

import sys
from typing import Any

from dev.harness.plan import is_app, is_law, is_nat, is_pin
# Re-use bplan's jet tables — they're the same set of native
# implementations regardless of which evaluator drives them.
from dev.harness.bplan import _COMPILER_JETS, _PRELUDE_JETS

try:
    from marduk import (
        App as _MApp, Law as _MLaw, Nat as _MNat, Pin as _MPin, Val as _MVal,
        evaluate as _m_evaluate,
        register_jet as _m_register_jet,
        clear_jets as _m_clear_jets,
    )
except ImportError as e:  # pragma: no cover — surface a clear setup hint
    raise ImportError(
        "marduk is not installed. From the gallowglass repo root: "
        "`pip install -e vendor/marduk/packages/marduk`. The Marduk "
        "checkout is gitignored under vendor/; see vendor.lock."
    ) from e


__all__ = [
    "bevaluate",
    "register_jets",
    "register_prelude_jets",
    "convert",
]


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------

def convert(val: Any, _cache: dict[int, _MVal] | None = None) -> _MVal:
    """Convert a legacy gallowglass Val into a Marduk ``Val``.

    Memoizes on Python identity so the converted graph preserves the
    sharing the legacy graph had — important for the jet registry
    (which is keyed on ``id(law.box)``)."""
    if _cache is None:
        _cache = {}
    cached = _cache.get(id(val))
    if cached is not None:
        return cached

    if is_nat(val):
        out: _MVal = _MNat(val)
    elif is_pin(val):
        out = _MPin(convert(val.val, _cache))
    elif is_law(val):
        out = _MLaw(
            convert(val.name, _cache),
            _MNat(val.arity),
            convert(val.body, _cache),
        )
    elif is_app(val):
        out = _MApp(
            convert(val.fun, _cache),
            convert(val.arg, _cache),
        )
    else:
        raise TypeError(
            f"convert: unknown legacy value type {type(val).__name__}: {val!r}"
        )
    _cache[id(val)] = out
    return out


# ---------------------------------------------------------------------------
# Jet registration
# ---------------------------------------------------------------------------

def _register_table(table: dict, compiled: dict) -> None:
    """For each ``(fq_name, (arity, fn))`` in ``table``, register a jet
    on the Marduk-converted law for the matching ``fq_name`` in
    ``compiled``. Vals not present in ``compiled`` are skipped silently
    — the legacy harness does the same.

    Caveat: a jet's signature is the legacy ``fn(*evaluated_args)`` where
    ``evaluated_args`` are *unwrapped* (legacy ``_unwrap`` strips
    ``P(N(k))`` opcode pins to bare ints). Marduk passes raw ``Val``
    args; the jet wrapper here forces each arg via Marduk's
    ``evaluate`` and unwraps Pin'd-nat constants to bare ints to
    match the legacy contract.
    """
    for fq_name, (arity, fn) in table.items():
        legacy_val = compiled.get(fq_name)
        if legacy_val is None:
            continue
        marduk_val = convert(legacy_val)
        # The compiled value is either L or P(L); dig down to the inner Law.
        if marduk_val.type == "pin" and marduk_val.item.type == "law":
            target = marduk_val.item
        elif marduk_val.type == "law":
            target = marduk_val
        else:
            continue
        _m_register_jet(target, _wrap_legacy_jet(fn))


def _wrap_legacy_jet(fn):
    """Adapt a legacy-style jet ``fn(*ints)`` to Marduk's
    ``fn(*Val)`` calling convention."""
    def wrapped(*marduk_args):
        unwrapped = []
        for a in marduk_args:
            _m_evaluate(a)
            if a.type == "nat":
                unwrapped.append(a.nat)
            elif a.type == "pin" and a.item.type == "nat":
                unwrapped.append(a.item.nat)
            else:
                unwrapped.append(a)
        result = fn(*unwrapped)
        # Legacy jets return either an int (for nat results) or a
        # legacy Val. Convert to Marduk Val on the way out.
        if isinstance(result, int):
            return _MNat(result)
        if isinstance(result, _MVal):
            return result
        # Legacy Val — convert.
        return convert(result)
    return wrapped


def register_jets(compiled: dict) -> None:
    """Install the gallowglass compiler-jet table against
    ``compiled``. Idempotent within a process: re-registering the same
    law overwrites the previous registration. Clears any prior
    registrations to match the legacy ``register_jets`` contract
    (legacy resets the registry on every call)."""
    _m_clear_jets()
    _register_table(_COMPILER_JETS, compiled)


def register_prelude_jets(compiled: dict) -> None:
    """Install the Core.{Nat,Text,List} prelude jets against
    ``compiled``. Additive — does not clear prior registrations."""
    _register_table(_PRELUDE_JETS, compiled)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def bevaluate(val: Any) -> _MVal:
    """Force ``val`` to WHNF via Marduk's evaluator. Accepts either a
    legacy Val (auto-converted) or a Marduk ``Val`` (passed through).

    The PLAN recursion limit gets bumped (matching the legacy
    harness's behavior) — production gallowglass programs reach
    PLAN-level depths that overflow Python's default 1000-frame
    ceiling without it.
    """
    if not isinstance(val, _MVal):
        val = convert(val)
    old = sys.getrecursionlimit()
    if old < 50_000:
        sys.setrecursionlimit(50_000)
    try:
        return _m_evaluate(val)
    finally:
        if sys.getrecursionlimit() != old:
            sys.setrecursionlimit(old)
