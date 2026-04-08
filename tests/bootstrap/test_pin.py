#!/usr/bin/env python3
"""
M16.1 tests — PinId computation and manifest format.

Run: python3 -m pytest tests/bootstrap/test_pin.py -v
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.pin import compute_pin_id, build_manifest, save_manifest, load_manifest
from dev.harness.plan import P, L, A, N


class TestPinId(unittest.TestCase):
    """PinId computation via BLAKE3-256 of seed bytes."""

    def test_deterministic(self):
        """Same PLAN value produces the same PinId."""
        val = N(42)
        id1 = compute_pin_id(val)
        id2 = compute_pin_id(val)
        self.assertEqual(id1, id2)

    def test_different_values_different_ids(self):
        """Different PLAN values produce different PinIds."""
        id1 = compute_pin_id(N(42))
        id2 = compute_pin_id(N(43))
        self.assertNotEqual(id1, id2)

    def test_hex_string_format(self):
        """PinId is a hex string of expected length (64 chars for 256-bit hash)."""
        pin_id = compute_pin_id(N(1))
        self.assertEqual(len(pin_id), 64)
        int(pin_id, 16)  # should not raise

    def test_complex_value(self):
        """PinId works on complex PLAN values (laws, apps, pins)."""
        law = L(2, 100, A(A(N(0), N(1)), N(2)))
        pin_id = compute_pin_id(law)
        self.assertEqual(len(pin_id), 64)
        # Deterministic
        self.assertEqual(pin_id, compute_pin_id(law))

    def test_pin_wrapped_value(self):
        """Pin-wrapping a value produces a different PinId than the raw value."""
        raw = N(42)
        wrapped = P(raw)
        self.assertNotEqual(compute_pin_id(raw), compute_pin_id(wrapped))


class TestManifest(unittest.TestCase):
    """Manifest build, save, and load."""

    def _sample_compiled(self):
        return {
            'Core.Nat.add': L(2, 100, A(A(N(0), N(1)), N(2))),
            'Core.Nat.sub': L(2, 200, A(A(N(0), N(1)), N(2))),
            'Core.Bool.not_': L(1, 300, N(1)),
            'Core.Nat.zero': N(0),
        }

    def test_build_manifest_filters_by_module(self):
        """build_manifest only includes entries from the specified module."""
        compiled = self._sample_compiled()
        manifest = build_manifest(compiled, 'Core.Nat')
        self.assertEqual(manifest['module'], 'Core.Nat')
        self.assertIn('Core.Nat.add', manifest['pins'])
        self.assertIn('Core.Nat.sub', manifest['pins'])
        self.assertIn('Core.Nat.zero', manifest['pins'])
        self.assertNotIn('Core.Bool.not_', manifest['pins'])

    def test_different_modules_different_manifests(self):
        """Different modules produce different manifests."""
        compiled = self._sample_compiled()
        m1 = build_manifest(compiled, 'Core.Nat')
        m2 = build_manifest(compiled, 'Core.Bool')
        self.assertNotEqual(m1['pins'], m2['pins'])

    def test_manifest_roundtrip(self):
        """Manifest survives save → load roundtrip."""
        compiled = self._sample_compiled()
        manifest = build_manifest(compiled, 'Core.Nat')
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            path = f.name
        try:
            save_manifest(manifest, path)
            loaded = load_manifest(path)
            self.assertEqual(manifest, loaded)
        finally:
            os.unlink(path)

    def test_manifest_json_format(self):
        """Saved manifest is valid JSON with expected structure."""
        compiled = self._sample_compiled()
        manifest = build_manifest(compiled, 'Core.Nat')
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            path = f.name
        try:
            save_manifest(manifest, path)
            with open(path) as f:
                raw = f.read()
            parsed = json.loads(raw)
            self.assertIn('module', parsed)
            self.assertIn('pins', parsed)
            self.assertIsInstance(parsed['pins'], dict)
        finally:
            os.unlink(path)


if __name__ == '__main__':
    unittest.main()
