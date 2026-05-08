"""Evaluator backend selector.

Single import seam for gallowglass tests, demos, and tools. Re-exports
``bevaluate``, ``register_jets``, ``register_prelude_jets`` from one
of two backends:

* ``marduk``  — Marduk's spec-faithful PLAN runtime via
  :mod:`dev.harness.marduk`. Requires ``pip install -e
  vendor/marduk/packages/marduk``. **Default.**
* ``legacy``  — gallowglass's pre-Marduk Python evaluator at
  :mod:`dev.harness.bplan`. Available via ``GALLOWGLASS_BACKEND=legacy``.

Selected by the ``GALLOWGLASS_BACKEND`` env var:

.. code-block:: bash

    # Default (marduk)
    pytest tests/

    # Run against legacy backend
    GALLOWGLASS_BACKEND=legacy pytest tests/

The seam is the migration scaffold: code that imports from here is
backend-neutral once the remaining test refactor lands. Code that
needs a specific backend (e.g. the legacy-vs-marduk benchmark)
should import directly from :mod:`dev.harness.bplan` or
:mod:`dev.harness.marduk`.

Migration complete
------------------

The Marduk backend is now the default. All 1417 tests pass under Marduk
with WHNF-faithful evaluation. The legacy backend remains available via
``GALLOWGLASS_BACKEND=legacy`` for performance comparisons and bisection.
"""

from __future__ import annotations

import os


_BACKEND_NAME = os.environ.get("GALLOWGLASS_BACKEND", "marduk").lower()


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


def _list_to_pylist(v) -> list:
    """Decode a gallowglass List into a Python list of element values.

    Backend-agnostic: works with both legacy and Marduk Vals.
    Encoding: Nil = Nat(0), Cons = App(App(Nat(1), head), tail).
    """
    from dev.harness.plan import is_nat, is_app
    out = []
    node = bevaluate(v)
    while True:
        if is_nat(node) and node == 0:
            break
        if not is_app(node):
            raise ValueError(f"_list_to_pylist: expected Nil or Cons, got {node!r}")
        inner = node.head
        if not is_app(inner):
            raise ValueError(f"_list_to_pylist: malformed Cons inner {inner!r}")
        out.append(inner.tail)
        node = bevaluate(node.tail)
    return out


__all__ = [
    "bevaluate",
    "register_jets",
    "register_prelude_jets",
    "apply",
    "backend_name",
    "_list_to_pylist",
]
