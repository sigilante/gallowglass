#!/usr/bin/env python3
"""
M16.4 tests — pin store save/resolve.

Run: python3 -m pytest tests/sanity/test_pin_store.py -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.pin_store import PinStore, PinStoreError
from dev.harness.plan import N, L, A


class TestPinStore(unittest.TestCase):
    """Pin store save and resolve."""

    def test_save_and_resolve(self):
        """Save a value, resolve by PinId, get the correct value."""
        with tempfile.TemporaryDirectory() as d:
            store = PinStore(d)
            pin_id = store.save(N(42))
            resolved = store.resolve(pin_id)
            self.assertEqual(resolved, 42)

    def test_missing_pin_raises(self):
        """Resolving a missing pin raises PinStoreError."""
        with tempfile.TemporaryDirectory() as d:
            store = PinStore(d)
            with self.assertRaises(PinStoreError):
                store.resolve('deadbeef' * 8)

    def test_cached_resolution(self):
        """Cached resolution returns the same object."""
        with tempfile.TemporaryDirectory() as d:
            store = PinStore(d)
            val = N(42)
            pin_id = store.save(val)
            r1 = store.resolve(pin_id)
            r2 = store.resolve(pin_id)
            self.assertIs(r1, r2)

    def test_complex_value_roundtrip(self):
        """Complex PLAN value survives save → resolve."""
        with tempfile.TemporaryDirectory() as d:
            store = PinStore(d)
            val = L(2, 100, A(A(N(0), N(1)), N(2)))
            pin_id = store.save(val)
            # Clear cache to force disk load
            store._cache.clear()
            resolved = store.resolve(pin_id)
            self.assertEqual(resolved, val)

    def test_has(self):
        """has() returns True for saved pins, False for missing."""
        with tempfile.TemporaryDirectory() as d:
            store = PinStore(d)
            pin_id = store.save(N(1))
            self.assertTrue(store.has(pin_id))
            self.assertFalse(store.has('0' * 64))


if __name__ == '__main__':
    unittest.main()
