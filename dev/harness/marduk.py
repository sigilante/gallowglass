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

    Idempotent: passing in a Marduk ``Val`` returns it unchanged.
    Memoizes on Python identity within a single conversion to preserve
    structural sharing in that pass.

    **Per-call cache only.** A module-global cache would seem to
    enable jet-registry hits across calls, but Marduk's evaluator
    mutates ``Val.box`` in-place during evaluation — cached Vals
    become stale once any prior evaluation has touched them. Per-call
    semantics keep correctness; the cost is that
    :func:`register_prelude_jets` can't reliably register against
    converted Laws (each conversion produces a fresh ``box`` id).
    Prelude jets are therefore no-op under Marduk; prelude operations
    run via the spec-faithful interpreter."""
    # Already a Marduk Val? Return as-is. Means callers can pass in a
    # value of either flavor and get back something Marduk can evaluate.
    if isinstance(val, _MVal):
        return val

    if _cache is None:
        _cache = {}
    cached = _cache.get(id(val))
    if cached is not None:
        return cached

    if isinstance(val, int):
        # Bare Python int — only legacy nats use this representation;
        # Marduk wraps via ``Nat``.
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
        # Use ``.head``/``.tail`` accessors which the alias work on
        # legacy ``A`` makes available alongside the original
        # ``.fun``/``.arg``. This route also handles any future Val
        # shape that exposes the Marduk-aligned names.
        out = _MApp(
            convert(val.head, _cache),
            convert(val.tail, _cache),
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


# ---------------------------------------------------------------------------
# Marduk-native prelude jets
#
# The legacy bplan ``_PRELUDE_JETS`` table mixes two categories:
#
# * Arithmetic (``Core.Nat.add``, ``mul``, ``Core.Text.sub``, ...): the
#   gallowglass wrapper Law's body invokes a BPLAN primitive
#   (``("B" ("Add" m n))`` etc). Marduk's BPLAN op table at
#   ``marduk.runtime.bplan`` already implements those natively, so the
#   wrapper Law lands at fast Python without any jet bridging needed.
#   We deliberately do NOT register jets for these — bridging would
#   add wrapper overhead without gaining anything.
#
# * Structural (``Core.List.map``, ``foldl``, ``foldr``, ``filter``,
#   ``length``, ``append``, ``concat_list``, plus
#   ``Core.Text.Prim.mk_text`` / ``text_len`` / ``text_nat``): these
#   walk the gallowglass Cons / Text encoding and have no BPLAN
#   primitive equivalent. Without jets, Marduk runs them through the
#   spec-faithful interpreter — correct but slow on long lists. The
#   implementations below port the legacy bplan jets to Marduk's
#   Val API so saturating one of these laws calls native Python
#   instead of walking the body.
#
# Encoding recap (identical between backends):
#   List a:  Nil   = Nat(0)
#            Cons  = App(App(Nat(1), head), tail)
#   Text:    App(Nat(byte_length), Nat(content_nat))
# ---------------------------------------------------------------------------

def _is_nil_m(v: _MVal) -> bool:
    return v.type == "nat" and v.nat == 0


def _is_cons_m(v: _MVal) -> bool:
    if v.type != "app":
        return False
    inner = v.head
    if inner.type != "app":
        return False
    tag = inner.head
    return tag.type == "nat" and tag.nat == 1


def _list_to_pylist_m(v: _MVal) -> list[_MVal]:
    """Decode a Marduk-shaped List into a Python list of Vals.

    Mirrors :func:`dev.harness.bplan._list_to_pylist` but uses Marduk's
    ``.head``/``.tail`` accessors. Forces the spine one Cons cell at
    a time so lazy ``Cons head tail`` chains evaluate progressively
    rather than triggering recursive forcing of the entire tail."""
    out: list[_MVal] = []
    _m_evaluate(v)
    while not _is_nil_m(v):
        if not _is_cons_m(v):
            raise ValueError(f"List jet: not a List spine node {v!r}")
        out.append(v.head.tail)        # head field of the Cons
        v = v.tail                      # tail field
        _m_evaluate(v)
    return out


def _pylist_to_list_m(items: list[_MVal]) -> _MVal:
    """Build a Marduk List from a Python list of Vals."""
    result: _MVal = _MNat(0)
    for item in reversed(items):
        result = _MApp(_MApp(_MNat(1), item), result)
    return result


def _apply_m(fn: _MVal, x: _MVal) -> _MVal:
    """Apply ``fn`` to ``x`` and force to WHNF."""
    out = _MApp(fn, x)
    _m_evaluate(out)
    return out


def _list_map_jet_m(fn: _MVal, xs: _MVal) -> _MVal:
    items = _list_to_pylist_m(xs)
    return _pylist_to_list_m([_apply_m(fn, x) for x in items])


def _list_foldl_jet_m(fn: _MVal, init: _MVal, xs: _MVal) -> _MVal:
    acc = init
    for x in _list_to_pylist_m(xs):
        acc = _apply_m(_apply_m(fn, acc), x)
    return acc


def _list_foldr_jet_m(fn: _MVal, init: _MVal, xs: _MVal) -> _MVal:
    items = _list_to_pylist_m(xs)
    acc = init
    for x in reversed(items):
        acc = _apply_m(_apply_m(fn, x), acc)
    return acc


def _list_filter_jet_m(fn: _MVal, xs: _MVal) -> _MVal:
    items = _list_to_pylist_m(xs)
    out: list[_MVal] = []
    for x in items:
        keep = _apply_m(fn, x)
        if keep.type == "nat" and keep.nat != 0:
            out.append(x)
    return _pylist_to_list_m(out)


def _list_length_jet_m(xs: _MVal) -> _MVal:
    return _MNat(len(_list_to_pylist_m(xs)))


def _list_append_jet_m(xs: _MVal, ys: _MVal) -> _MVal:
    return _pylist_to_list_m(
        _list_to_pylist_m(xs) + _list_to_pylist_m(ys)
    )


def _list_concat_jet_m(xss: _MVal) -> _MVal:
    items: list[_MVal] = []
    for sub in _list_to_pylist_m(xss):
        items.extend(_list_to_pylist_m(sub))
    return _pylist_to_list_m(items)


def _mk_text_jet_m(length: _MVal, content: _MVal) -> _MVal:
    _m_evaluate(length); _m_evaluate(content)
    l = length.nat if length.type == "nat" else 0
    c = content.nat if content.type == "nat" else 0
    return _MApp(_MNat(l), _MNat(c))


def _text_len_jet_m(t: _MVal) -> _MVal:
    _m_evaluate(t)
    if t.type == "app":
        head = t.head
        _m_evaluate(head)
        if head.type == "nat":
            return _MNat(head.nat)
    return _MNat(0)


def _text_nat_jet_m(t: _MVal) -> _MVal:
    _m_evaluate(t)
    if t.type == "app":
        tail = t.tail
        _m_evaluate(tail)
        if tail.type == "nat":
            return _MNat(tail.nat)
    return _MNat(0)


_PRELUDE_JETS_MARDUK = {
    # Lists
    "Core.List.map":         (2, _list_map_jet_m),
    "Core.List.foldl":       (3, _list_foldl_jet_m),
    "Core.List.foldr":       (3, _list_foldr_jet_m),
    "Core.List.filter":      (2, _list_filter_jet_m),
    "Core.List.length":      (1, _list_length_jet_m),
    "Core.List.append":      (2, _list_append_jet_m),
    "Core.List.concat_list": (1, _list_concat_jet_m),
    # Text primitives
    "Core.Text.Prim.mk_text":  (2, _mk_text_jet_m),
    "Core.Text.Prim.text_len": (1, _text_len_jet_m),
    "Core.Text.Prim.text_nat": (1, _text_nat_jet_m),
}


def register_prelude_jets(compiled: dict) -> None:
    """Currently a no-op under Marduk.

    The legacy gallowglass jet table couples Python-fn implementations
    to specific Law identities via the registry's ``id(law.box)``
    key. Mirroring that under Marduk requires a global identity-cached
    converter — but Marduk's evaluator mutates ``Val.box`` in place,
    so a global cache becomes corrupt as soon as anything is forced.
    The fundamentals don't compose: cached identity + in-place
    mutation = stale data on the second use.

    The Marduk-native list/text jet implementations are kept (see
    ``_PRELUDE_JETS_MARDUK`` below) for a future fix that resolves
    this — likely by NOT mutating in-place during evaluation, or by
    tying jet registration to Law-content rather than Law-identity.
    Until then, prelude-heavy code runs through Marduk's
    spec-faithful interpreter — slower than the legacy backend but
    correct.
    """
    return


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
