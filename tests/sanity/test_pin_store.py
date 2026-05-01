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


# ---------------------------------------------------------------------------
# AUDIT.md B2: atomic-write guarantees
# ---------------------------------------------------------------------------
#
# `PinStore.save` previously used `os.path.exists` → `open(wb)` → `write`,
# which leaves a half-written file visible to readers if a concurrent
# writer (e.g. `make -j`) interleaves between the existence check and
# `open`.  The fix writes to a per-process temp path and `os.replace`s
# into place atomically.  These tests pin the new guarantees.
# ---------------------------------------------------------------------------


class TestPinStoreAtomicWrite(unittest.TestCase):
    """Pin store save() is atomic: no half-written files, no tmp leakage."""

    def test_no_tmp_files_after_successful_save(self):
        """Successful save leaves only `<pin_id>.seed`, no `.tmp.*` artifacts."""
        with tempfile.TemporaryDirectory() as d:
            store = PinStore(d)
            store.save(N(42))
            entries = os.listdir(d)
            tmp_leaked = [e for e in entries if '.tmp.' in e]
            self.assertEqual(tmp_leaked, [],
                f'tmp file(s) leaked after successful save: {tmp_leaked}')

    def test_failed_save_cleans_tmp_and_leaves_no_partial(self):
        """If save_seed raises mid-write, no tmp file remains and no
        partial file appears at the final path."""
        from dev.harness import pin_store as ps_mod

        class Boom(Exception):
            pass

        def raising_save_seed(_val):
            raise Boom('simulated mid-write failure')

        with tempfile.TemporaryDirectory() as d:
            store = PinStore(d)
            original = ps_mod.save_seed
            ps_mod.save_seed = raising_save_seed
            try:
                with self.assertRaises(Boom):
                    store.save(N(7))
            finally:
                ps_mod.save_seed = original

            entries = os.listdir(d)
            self.assertEqual(entries, [],
                f'expected no files after failed save, got {entries}')

    def test_repeated_save_overwrites_atomically(self):
        """Saving the same value twice is idempotent and leaves a clean tree."""
        with tempfile.TemporaryDirectory() as d:
            store = PinStore(d)
            id1 = store.save(N(99))
            # Force the on-disk path to be re-written by clearing cache and
            # deleting the file (simulating a stale state); a new save must
            # produce identical bytes via the temp+replace path.
            os.unlink(os.path.join(d, f'{id1}.seed'))
            store._cache.clear()
            id2 = store.save(N(99))
            self.assertEqual(id1, id2)
            entries = os.listdir(d)
            self.assertEqual(entries, [f'{id1}.seed'])


if __name__ == '__main__':
    unittest.main()
