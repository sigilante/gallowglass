#!/usr/bin/env python3
"""
Reaver runtime smoke tests.

Compiles small Gallowglass programs through the bootstrap, emits Plan
Assembler text via `bootstrap.emit_pla`, runs the result under Reaver
(`vendor/reaver`), and asserts the traced output.

These tests gate the end-to-end pipeline against the canonical PLAN
runtime. They require a working Reaver build, which is built via the
project's Nix flake. If `nix` or the build aren't available, tests skip.

Run:
    make test-reaver
  or:
    python3 -m pytest tests/reaver/ -v

CI runs this in a separate `reaver` job (see `.github/workflows/ci.yml`)
that installs Nix and builds plan-assembler before the test step.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program
from bootstrap.emit_pla import emit_program


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
REAVER_DIR = os.path.join(REPO_ROOT, 'vendor', 'reaver')
BOOT_PLAN = os.path.join(REAVER_DIR, 'src', 'plan', 'boot.plan')


def _reaver_available() -> tuple[bool, str]:
    """Return (available, reason). Reaver requires the vendor checkout
    plus a working `cabal` (provided by `nix develop`)."""
    if not os.path.isdir(REAVER_DIR):
        return False, f'{REAVER_DIR} not present — run tools/vendor.sh'
    if not os.path.isfile(BOOT_PLAN):
        return False, f'{BOOT_PLAN} not present — vendor checkout incomplete'
    if shutil.which('nix') is None and shutil.which('cabal') is None:
        return False, 'neither nix nor cabal on PATH'
    return True, ''


_AVAIL, _SKIP_REASON = _reaver_available()
requires_reaver = unittest.skipUnless(_AVAIL, _SKIP_REASON or 'reaver unavailable')


def _run_reaver(plan_text: str, module: str = 'demo', timeout: int = 60) -> str:
    """Write plan_text into a temp dir as `<module>.plan`, copy boot.plan
    alongside it, run plan-assembler, return stdout+stderr.

    Reaver writes its trace output to stderr by default, so we capture both
    streams and return the combined string."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, f'{module}.plan'), 'w') as f:
            f.write(plan_text)
        shutil.copy(BOOT_PLAN, os.path.join(tmpdir, 'boot.plan'))

        # Prefer `nix develop` since the cabal build pulls in GHC + libs
        # via the flake. Fallback to bare cabal if nix is unavailable.
        if shutil.which('nix') is not None:
            cmd = ['nix', 'develop', '--command', 'cabal', 'run', '-v0',
                   'plan-assembler', '--', tmpdir, module]
        else:
            cmd = ['cabal', 'run', '-v0', 'plan-assembler', '--', tmpdir, module]

        result = subprocess.run(cmd, cwd=REAVER_DIR, capture_output=True,
                                timeout=timeout)
    return (result.stdout + result.stderr).decode('utf-8', errors='replace')


def _compile_demo(src: str, module: str) -> dict:
    """Lex → parse → resolve → codegen. Returns compiled dict."""
    prog = parse(lex(src, '<demo>'), '<demo>')
    resolved, _ = resolve(prog, module, {}, '<demo>')
    return compile_program(resolved, module)


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------

@requires_reaver
class TestReaverSmoke(unittest.TestCase):
    """End-to-end pipeline: gallowglass src → Plan Asm → Reaver execution."""

    def _trace(self, src: str, module: str, value_name: str) -> str:
        """Compile, emit with a `Trace` driver, run, and return Reaver's output."""
        compiled = _compile_demo(src, module)
        sym = f'{module}_{value_name}'.replace('.', '_')
        # `emit_program` prepends `@boot` so Elim/Inc/etc. are in scope
        # for the (#bind ...) lines that reference them.
        trailer = f'(Trace {sym} 0)\n'
        plan_text = emit_program(compiled, trailer=trailer)
        return _run_reaver(plan_text, module='demo')

    def test_const_law_returns_first_arg(self):
        """`λ x y → x` applied to 42 99 traces 42."""
        src = '''
let const : Nat -> Nat -> Nat
  = λ x y → x

let main : Nat
  = const 42 99
'''
        out = self._trace(src, 'Demo', 'main')
        # Trace output is the value followed by the cont return, then 0.
        self.assertIn('\n42\n', out, f'expected 42 in Reaver output:\n{out!r}')

    def test_inc_via_bplan_named_primitive(self):
        """`Core.PLAN.inc` dispatches via the BPLAN `Inc` named primitive."""
        src = '''
external mod Core.PLAN { inc : Nat -> Nat }

let succ : Nat -> Nat
  = λ n → PLAN.inc n

let main : Nat
  = succ (succ (succ 39))
'''
        out = self._trace(src, 'Demo', 'main')
        self.assertIn('\n42\n', out, f'expected 42 in Reaver output:\n{out!r}')

    def test_constructor_tag_emission(self):
        """Constructor tags (large strNats) round-trip without slot-ref confusion.

        Regression for the `_139140624211` bug fixed in PR #48 — a
        body-context PNat whose value > arity should be quote-wrapped, not
        rendered as a slot reference. Uses values > 255 so Reaver's
        showVal renders them as decimals instead of byte/string forms."""
        src = '''
type Color =
  | Red
  | Green
  | Blue

let pick : Nat -> Color
  = λ n → match n {
      | 0 → Red
      | 1 → Green
      | _ → Blue
    }

let main : Nat
  = match (pick 1) {
      | Red   → 1000
      | Green → 2000
      | Blue  → 3000
    }
'''
        out = self._trace(src, 'Demo', 'main')
        self.assertIn('\n2000\n', out, f'expected 2000 in Reaver output:\n{out!r}')


if __name__ == '__main__':
    unittest.main()
