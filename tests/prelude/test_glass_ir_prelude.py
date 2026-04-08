#!/usr/bin/env python3
"""
M17.5 tests — prelude Glass IR emission and round-trip verification.

Run: python3 -m pytest tests/prelude/test_glass_ir_prelude.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.build import build_modules
from bootstrap.glass_ir import render_fragment, verify_roundtrip, collect_pin_deps
from bootstrap.pin import build_manifest, compute_pin_id
from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program
from bootstrap.ast import DeclLet

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

_RESOLVED_CACHE = None
_COMPILED_CACHE = None


def _build_and_resolve():
    global _RESOLVED_CACHE, _COMPILED_CACHE
    if _RESOLVED_CACHE is not None:
        return _RESOLVED_CACHE, _COMPILED_CACHE

    sources = []
    for mod in MODULES:
        short = mod.split('.')[-1]
        path = os.path.join(CORE_DIR, f'{short}.gls')
        with open(path) as f:
            sources.append((mod, f.read()))

    # Compile plain (for PLAN values)
    compiled = build_modules(sources, pin_wrap=False)

    # Resolve each module (for ASTs)
    module_envs = {}
    resolved_modules = {}
    for mod, source_text in sources:
        filename = f'<{mod}>'
        prog = parse(lex(source_text, filename), filename)
        resolved_prog, env = resolve(prog, mod, module_envs, filename)
        module_envs[mod] = env
        resolved_modules[mod] = resolved_prog

    _RESOLVED_CACHE = resolved_modules
    _COMPILED_CACHE = compiled
    return resolved_modules, compiled


class TestPreludeGlassIR(unittest.TestCase):
    """Prelude Glass IR emission and verification."""

    @classmethod
    def setUpClass(cls):
        cls.resolved, cls.compiled = _build_and_resolve()
        cls.pin_ids = {fq: compute_pin_id(v) for fq, v in cls.compiled.items()}
        cls.manifest = {'pins': cls.pin_ids}

    def test_all_modules_produce_fragments(self):
        """Every let decl in every module produces a Glass IR fragment."""
        count = 0
        for mod in MODULES:
            resolved = self.resolved[mod]
            for decl in resolved.decls:
                if isinstance(decl, DeclLet):
                    fq = f'{mod}.{decl.name}'
                    pin_id = self.pin_ids.get(fq)
                    frag = render_fragment(fq, decl, pin_id=pin_id, module=mod)
                    self.assertIn('-- Snapshot:', frag)
                    self.assertIn(fq, frag)
                    count += 1
        self.assertGreater(count, 50)

    def test_fragments_have_fq_names(self):
        """Fragments use fully-qualified names, no bare unqualified names."""
        resolved = self.resolved['Core.Nat']
        for decl in resolved.decls:
            if isinstance(decl, DeclLet):
                fq = f'Core.Nat.{decl.name}'
                frag = render_fragment(fq, decl, pin_id=self.pin_ids.get(fq),
                                       module='Core.Nat')
                # Should contain FQ name, not bare 'let add ='
                self.assertIn(f'let {fq}', frag)

    def test_spot_check_add(self):
        """Core.Nat.add fragment has expected content."""
        resolved = self.resolved['Core.Nat']
        for decl in resolved.decls:
            if isinstance(decl, DeclLet) and decl.name == 'add':
                fq = 'Core.Nat.add'
                frag = render_fragment(fq, decl, pin_id=self.pin_ids.get(fq),
                                       module='Core.Nat')
                self.assertIn('Core.Nat.add', frag)
                self.assertIn('[pin#', frag)
                self.assertIn('λ', frag)
                break
        else:
            self.fail("Core.Nat.add not found")

    def test_spot_check_id(self):
        """Core.Combinators.id fragment has expected content."""
        resolved = self.resolved['Core.Combinators']
        for decl in resolved.decls:
            if isinstance(decl, DeclLet) and decl.name == 'id':
                fq = 'Core.Combinators.id'
                frag = render_fragment(fq, decl, pin_id=self.pin_ids.get(fq),
                                       module='Core.Combinators')
                self.assertIn('Core.Combinators.id', frag)
                self.assertIn('λ', frag)
                break
        else:
            self.fail("Core.Combinators.id not found")

    def test_roundtrip_combinators(self):
        """Core.Combinators round-trips correctly."""
        resolved = self.resolved['Core.Combinators']
        results = verify_roundtrip(resolved, self.compiled, 'Core.Combinators')
        for ok, msg in results:
            self.assertTrue(ok, msg)

    def test_roundtrip_nat(self):
        """Core.Nat round-trips correctly."""
        resolved = self.resolved['Core.Nat']
        results = verify_roundtrip(resolved, self.compiled, 'Core.Nat')
        for ok, msg in results:
            self.assertTrue(ok, msg)

    def test_cross_module_deps_rendered(self):
        """Definitions with cross-module deps include pin declarations."""
        # Core.List uses Core.Nat, Core.Text, etc.
        resolved = self.resolved['Core.List']
        found_dep = False
        for decl in resolved.decls:
            if isinstance(decl, DeclLet):
                fq = f'Core.List.{decl.name}'
                deps = collect_pin_deps(fq, decl, 'Core.List', self.manifest)
                if deps:
                    found_dep = True
                    frag = render_fragment(fq, decl, pin_id=self.pin_ids.get(fq),
                                           module='Core.List', deps=deps)
                    self.assertIn('@![pin#', frag)
                    break
        self.assertTrue(found_dep, "expected at least one cross-module dep in Core.List")


if __name__ == '__main__':
    unittest.main()
