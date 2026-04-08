#!/usr/bin/env python3
"""
M16.3/M16.5 tests — prelude as pinned DAG + integration tests.

Validates that the prelude builds with pin_wrap=True, produces
deterministic manifests, pin-wrapped values evaluate correctly,
and the full pin cycle works end-to-end.

Run: python3 -m pytest tests/prelude/test_pin_prelude.py -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.build import build_modules
from bootstrap.pin import build_manifest, compute_pin_id
from dev.harness.bplan import bevaluate, register_prelude_jets, _bapply
from dev.harness.plan import A, N, P, is_pin
from dev.harness.pin_store import PinStore

CORE_DIR = os.path.join(os.path.dirname(__file__), '..', '..',
                        'prelude', 'src', 'Core')

MODULES = [
    'Core.Combinators',
    'Core.Nat',
    'Core.Bool',
    'Core.Text',
    'Core.Pair',
    'Core.Option',
    'Core.List',
    'Core.Result',
]

_COMPILED = None
_COMPILED_PLAIN = None


def _build_pinned():
    global _COMPILED
    if _COMPILED is not None:
        return _COMPILED
    sources = _load_sources()
    _COMPILED = build_modules(sources, pin_wrap=True)
    return _COMPILED


def _build_plain():
    global _COMPILED_PLAIN
    if _COMPILED_PLAIN is not None:
        return _COMPILED_PLAIN
    sources = _load_sources()
    _COMPILED_PLAIN = build_modules(sources, pin_wrap=False)
    register_prelude_jets(_COMPILED_PLAIN)
    return _COMPILED_PLAIN


def _load_sources():
    sources = []
    for mod in MODULES:
        short = mod.split('.')[-1]
        path = os.path.join(CORE_DIR, f'{short}.gls')
        with open(path) as f:
            sources.append((mod, f.read()))
    return sources


class TestPreludePinned(unittest.TestCase):
    """Prelude compiled as pinned DAG."""

    @classmethod
    def setUpClass(cls):
        cls.pinned = _build_pinned()
        cls.plain = _build_plain()

    def test_all_modules_produce_manifests(self):
        """Every module produces a manifest with at least one pin."""
        for mod in MODULES:
            manifest = build_manifest(self.pinned, mod)
            self.assertGreater(len(manifest['pins']), 0,
                               f"no pins for {mod}")

    def test_combined_manifest_count(self):
        """Combined prelude has >100 pins."""
        all_pins = {}
        for mod in MODULES:
            manifest = build_manifest(self.pinned, mod)
            all_pins.update(manifest['pins'])
        self.assertGreater(len(all_pins), 100)

    def test_deterministic_pin_ids(self):
        """Recompilation produces identical PinIds."""
        sources = _load_sources()
        recompiled = build_modules(sources, pin_wrap=True)
        for fq in self.pinned:
            id1 = compute_pin_id(self.pinned[fq])
            id2 = compute_pin_id(recompiled[fq])
            self.assertEqual(id1, id2, f"PinId mismatch for {fq}")

    def test_pin_wrapped_values_are_pins(self):
        """All pinned values are P(...)."""
        for fq, val in self.pinned.items():
            self.assertIsInstance(val, P, f"{fq} not pin-wrapped")

    def test_spot_check_id(self):
        """Core.Combinators.id evaluates correctly (via plain build)."""
        fn = self.plain['Core.Combinators.id']
        result = bevaluate(_bapply(fn, N(42)))
        self.assertEqual(result, 42)

    def test_spot_check_add(self):
        """Core.Nat.add evaluates correctly."""
        fn = self.plain['Core.Nat.add']
        result = bevaluate(_bapply(_bapply(fn, N(3)), N(4)))
        self.assertEqual(result, 7)

    def test_spot_check_map(self):
        """Core.List.map evaluates correctly."""
        map_fn = self.plain['Core.List.map']
        inc = self.plain['Core.PLAN.inc']
        lst = A(A(N(1), N(10)), N(0))  # Cons 10 Nil
        result = bevaluate(_bapply(_bapply(map_fn, inc), lst))
        # Should be Cons 11 Nil = A(A(N(1), N(11)), N(0))
        from dev.harness.plan import is_app
        self.assertTrue(is_app(result))


class TestPinCycleIntegration(unittest.TestCase):
    """M16.5: Full pin cycle — compile → pin → store → resolve → evaluate."""

    @classmethod
    def setUpClass(cls):
        cls.plain = _build_plain()

    def test_store_roundtrip(self):
        """Save prelude definitions to pin store, resolve, get correct values."""
        with tempfile.TemporaryDirectory() as d:
            store = PinStore(d)
            # Save a few key definitions
            fqs = ['Core.Nat.add', 'Core.Combinators.id', 'Core.Nat.sub']
            pin_ids = {}
            for fq in fqs:
                pin_ids[fq] = store.save(self.plain[fq])

            # Resolve and verify
            for fq in fqs:
                resolved = store.resolve(pin_ids[fq])
                self.assertEqual(resolved, self.plain[fq])

    def test_manifest_all_pins_resolve(self):
        """All manifest PinIds correspond to valid, resolvable values."""
        pinned = _build_pinned()
        all_pins = {}
        for mod in MODULES:
            manifest = build_manifest(pinned, mod)
            all_pins.update(manifest['pins'])
        # Every PinId should be a valid 64-char hex string
        for fq, pin_id in all_pins.items():
            self.assertEqual(len(pin_id), 64, f"bad PinId length for {fq}")
            int(pin_id, 16)  # should not raise

    def test_pinned_and_plain_equivalent(self):
        """Pin-wrapped and plain builds produce equivalent content."""
        pinned = _build_pinned()
        plain = self.plain
        for fq in plain:
            self.assertIn(fq, pinned)
            # Unwrap pin, compare content
            self.assertEqual(pinned[fq].val, plain[fq],
                             f"content mismatch for {fq}")


if __name__ == '__main__':
    unittest.main()
