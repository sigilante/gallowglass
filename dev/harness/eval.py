"""Evaluator backend selector.

Single import seam for gallowglass tests, demos, and tools. Re-exports
``bevaluate``, ``register_jets``, ``register_prelude_jets`` from one
of two backends:

* ``legacy``  — gallowglass's pre-Marduk Python evaluator at
  :mod:`dev.harness.bplan`. **Default** until the Marduk migration
  finishes — see "Why the default is still legacy" below.
* ``marduk``  — Marduk's spec-faithful PLAN runtime via
  :mod:`dev.harness.marduk`. Requires ``pip install -e
  vendor/marduk/packages/marduk``.

Selected by the ``GALLOWGLASS_BACKEND`` env var:

.. code-block:: bash

    # Default (legacy)
    pytest tests/

    # Run the same suite against marduk
    GALLOWGLASS_BACKEND=marduk pytest tests/

The seam is the migration scaffold: code that imports from here is
backend-neutral once the remaining test refactor lands. Code that
needs a specific backend (e.g. the legacy-vs-marduk benchmark)
should import directly from :mod:`dev.harness.bplan` or
:mod:`dev.harness.marduk`.

Why the default is still ``legacy``
-----------------------------------

Two gaps need closing before flipping:

1. **Performance**. The Marduk backend is 5–7x slower on
   list-heavy demos (see ``benchmarks/baseline_marduk.json`` vs
   ``baseline_legacy.json``). Test wall time projects from ~25s
   (legacy) to ~3 min (marduk).
2. **Val-shape coupling**. Many tests assert on legacy ``A``-class
   attributes (``result.fun``, ``result.arg``); Marduk's ``Val``
   uses ``.head`` / ``.tail``. Tests need backend-agnostic accessors
   before the result type can change.

Both are tracked as follow-up work; the seam means the eventual flip
is a one-line change here.
"""

from __future__ import annotations

import os


_BACKEND_NAME = os.environ.get("GALLOWGLASS_BACKEND", "legacy").lower()


def _import_backend(name: str):
    if name == "marduk":
        from dev.harness import marduk as backend
        return backend
    if name == "legacy":
        from dev.harness import bplan as backend
        return backend
    raise ValueError(
        f"GALLOWGLASS_BACKEND={name!r}; expected 'marduk' or 'legacy'"
    )


_backend = _import_backend(_BACKEND_NAME)

bevaluate = _backend.bevaluate
register_jets = _backend.register_jets
register_prelude_jets = _backend.register_prelude_jets


# ``apply`` — backend-agnostic application helper.
#
# Tests historically imported ``_bapply`` from :mod:`dev.harness.bplan`
# directly to build saturated applications without involving an
# evaluator yet. Under the Marduk backend that path doesn't work:
# legacy ``_bapply`` for an arity-1 application *eagerly* runs the
# function through the legacy interpreter, which has no prelude jets
# registered under Marduk default and blows the recursion ceiling on
# anything text-heavy. Use ``apply(f, x)`` instead — it builds an
# unforced ``App`` in the active backend's value space.
if _BACKEND_NAME == "marduk":
    from dev.harness.marduk import convert as _convert
    from marduk import App as _MApp

    def apply(f, x):
        return _MApp(_convert(f), _convert(x))

else:
    from dev.harness.bplan import _bapply as _legacy_bapply

    def apply(f, x):
        return _legacy_bapply(f, x)


backend_name: str = _BACKEND_NAME

__all__ = [
    "bevaluate",
    "register_jets",
    "register_prelude_jets",
    "apply",
    "backend_name",
]
