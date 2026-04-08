#!/usr/bin/env python3
"""
Build the Gallowglass prelude as a pinned DAG.

Compiles all 8 Core modules with pin_wrap=True, produces per-module
manifests and a combined manifest, and optionally emits per-definition
seed files.

Usage:
    python3 -m bootstrap.build_prelude [--seeds]

Output:
    prelude/manifest/Core.Nat.json      per-module manifest
    prelude/manifest/Core.List.json     ...
    prelude/manifest/prelude.json       combined manifest
    prelude/pins/*.seed                 (with --seeds) per-definition seeds
"""

from __future__ import annotations

import json
import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bootstrap.build import build_modules
from bootstrap.pin import build_manifest, save_manifest
from bootstrap.emit import emit_pinned

CORE_DIR = os.path.join(os.path.dirname(__file__), '..', 'prelude', 'src', 'Core')
MANIFEST_DIR = os.path.join(os.path.dirname(__file__), '..', 'prelude', 'manifest')
PINS_DIR = os.path.join(os.path.dirname(__file__), '..', 'prelude', 'pins')

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


def build_prelude(emit_seeds: bool = False) -> dict:
    """Build the full prelude as a pinned DAG.

    Returns:
        Combined manifest dict.
    """
    # Load sources
    sources = []
    for mod in MODULES:
        short = mod.split('.')[-1]
        path = os.path.join(CORE_DIR, f'{short}.gls')
        with open(path) as f:
            sources.append((mod, f.read()))

    # Compile with pin wrapping
    compiled = build_modules(sources, pin_wrap=True)

    # Create manifest directory
    os.makedirs(MANIFEST_DIR, exist_ok=True)

    # Per-module manifests
    combined_pins = {}
    for mod in MODULES:
        manifest = build_manifest(compiled, mod)
        save_manifest(manifest, os.path.join(MANIFEST_DIR, f'{mod}.json'))
        combined_pins.update(manifest['pins'])

    # Combined manifest
    combined = {'module': 'Core', 'pins': combined_pins}
    save_manifest(combined, os.path.join(MANIFEST_DIR, 'prelude.json'))

    # Optional: emit per-definition seed files
    if emit_seeds:
        for mod in MODULES:
            emit_pinned(compiled, mod, PINS_DIR)

    return combined


def main():
    emit_seeds = '--seeds' in sys.argv
    manifest = build_prelude(emit_seeds=emit_seeds)
    n = len(manifest['pins'])
    print(f"Built prelude manifest: {n} pins across {len(MODULES)} modules")
    print(f"Manifests written to {MANIFEST_DIR}/")
    if emit_seeds:
        print(f"Seed files written to {PINS_DIR}/")


if __name__ == '__main__':
    main()
