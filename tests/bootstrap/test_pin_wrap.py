#!/usr/bin/env python3
"""
M16.2 tests — pin-wrapped compilation and pinned emission.

Run: python3 -m pytest tests/bootstrap/test_pin_wrap.py -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.build import build_modules
from bootstrap.emit_seed import emit_pinned
from dev.harness.plan import P, N, is_pin


class TestPinWrappedBuild(unittest.TestCase):
    """Pin-wrapped compilation via build_modules(pin_wrap=True)."""

    def _simple_sources(self):
        return [
            ('Test.A', 'let foo = 42\nlet bar = 99'),
        ]

    def test_pin_wrap_produces_pins(self):
        """With pin_wrap=True, all values are P(...)."""
        compiled = build_modules(self._simple_sources(), pin_wrap=True)
        for fq, val in compiled.items():
            self.assertIsInstance(val, P, f"{fq} should be pin-wrapped")

    def test_no_pin_wrap_no_pins(self):
        """With pin_wrap=False (default), values are not wrapped."""
        compiled = build_modules(self._simple_sources(), pin_wrap=False)
        for fq, val in compiled.items():
            self.assertNotIsInstance(val, P, f"{fq} should not be pin-wrapped")

    def test_pin_wrapped_content_matches(self):
        """Pin-wrapped values contain the same content as unwrapped."""
        sources = self._simple_sources()
        plain = build_modules(sources, pin_wrap=False)
        pinned = build_modules(sources, pin_wrap=True)
        for fq in plain:
            self.assertIn(fq, pinned)
            self.assertEqual(pinned[fq].val, plain[fq])


class TestEmitPinned(unittest.TestCase):
    """Pinned emission: per-definition seed files + manifest."""

    def test_emit_pinned_produces_files(self):
        """emit_pinned creates seed files and manifest.json."""
        sources = [('Test.Mod', 'let foo = 1\nlet bar = 2')]
        compiled = build_modules(sources)
        with tempfile.TemporaryDirectory() as out_dir:
            manifest = emit_pinned(compiled, 'Test.Mod', out_dir)
            # Check manifest file exists
            self.assertTrue(os.path.exists(os.path.join(out_dir, 'manifest.json')))
            # Check seed files exist
            for fq_name in manifest['pins']:
                safe = fq_name.replace('.', '_')
                seed_path = os.path.join(out_dir, f'{safe}.seed')
                self.assertTrue(os.path.exists(seed_path), f"missing {seed_path}")

    def test_emit_pinned_manifest_has_pins(self):
        """emit_pinned manifest contains PinIds for all module definitions."""
        sources = [('Test.Mod', 'let foo = 1\nlet bar = 2')]
        compiled = build_modules(sources)
        with tempfile.TemporaryDirectory() as out_dir:
            manifest = emit_pinned(compiled, 'Test.Mod', out_dir)
            self.assertEqual(manifest['module'], 'Test.Mod')
            self.assertIn('Test.Mod.foo', manifest['pins'])
            self.assertIn('Test.Mod.bar', manifest['pins'])


if __name__ == '__main__':
    unittest.main()
