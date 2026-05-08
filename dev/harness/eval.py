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

backend_name: str = _BACKEND_NAME

__all__ = [
    "bevaluate",
    "register_jets",
    "register_prelude_jets",
    "backend_name",
]
