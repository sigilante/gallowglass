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
from bootstrap.glass_ir import render_fragment, render_decl, collect_pin_deps
from bootstrap.pin import build_manifest, save_manifest
from bootstrap.emit import emit_pinned

CORE_DIR = os.path.join(os.path.dirname(__file__), '..', 'prelude', 'src', 'Core')
MANIFEST_DIR = os.path.join(os.path.dirname(__file__), '..', 'prelude', 'manifest')
PINS_DIR = os.path.join(os.path.dirname(__file__), '..', 'prelude', 'pins')
GLASS_IR_DIR = os.path.join(os.path.dirname(__file__), '..', 'prelude', 'glass_ir')

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


def build_prelude(emit_seeds: bool = False, emit_glass_ir: bool = False) -> dict:
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

    # Optional: emit Glass IR fragments
    if emit_glass_ir:
        _emit_glass_ir(sources, combined)

    return combined


def _emit_glass_ir(sources, manifest):
    """Emit Glass IR fragments for all prelude definitions."""
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.typecheck import typecheck
    from bootstrap.ast import DeclLet

    os.makedirs(GLASS_IR_DIR, exist_ok=True)

    pin_ids = manifest.get('pins', {})
    module_envs = {}
    all_type_env = {}

    for mod, source_text in sources:
        filename = f'<{mod}>'
        prog = parse(lex(source_text, filename), filename)
        resolved, env = resolve(prog, mod, module_envs, filename)
        module_envs[mod] = env

        # Type-check module, accumulating type environment
        try:
            type_env = typecheck(resolved, env, mod, filename,
                                 prior_type_env=all_type_env)
            all_type_env.update(type_env)
        except Exception:
            # If typechecking fails, continue without types
            pass

        for decl in resolved.decls:
            if isinstance(decl, DeclLet):
                fq = f'{mod}.{decl.name}'
                pin_id = pin_ids.get(fq)
                deps = collect_pin_deps(fq, decl, mod, manifest)
                frag = render_fragment(fq, decl, pin_id=pin_id,
                                       module=mod, deps=deps,
                                       type_env=all_type_env)
                safe_name = fq.replace('.', '_')
                path = os.path.join(GLASS_IR_DIR, f'{safe_name}.gls')
                with open(path, 'w') as f:
                    f.write(frag)


def main():
    emit_seeds = '--seeds' in sys.argv
    emit_glass = '--glass-ir' in sys.argv
    manifest = build_prelude(emit_seeds=emit_seeds, emit_glass_ir=emit_glass)
    n = len(manifest['pins'])
    print(f"Built prelude manifest: {n} pins across {len(MODULES)} modules")
    print(f"Manifests written to {MANIFEST_DIR}/")
    if emit_seeds:
        print(f"Seed files written to {PINS_DIR}/")
    if emit_glass:
        print(f"Glass IR fragments written to {GLASS_IR_DIR}/")


if __name__ == '__main__':
    main()
