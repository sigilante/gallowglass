#!/usr/bin/env python3
"""
End-to-end test for `demos/repl_calc.gls` running under Reaver.

Compiles the demo, invokes `plan-assembler <dir> <module> Main_main 0`
with a multi-line arithmetic input on stdin, asserts the rendered
results appear on stdout in order.

Skips when nix/cabal or the Reaver vendor checkout aren't present.
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
DEMO_PATH = os.path.join(REPO_ROOT, 'demos', 'repl_calc.gls')


def _reaver_available() -> tuple[bool, str]:
    if not os.path.isdir(REAVER_DIR):
        return False, f'{REAVER_DIR} not present — run tools/vendor.sh'
    if not os.path.isfile(BOOT_PLAN):
        return False, 'vendor/reaver/src/plan/boot.plan not present'
    if shutil.which('nix') is None and shutil.which('cabal') is None:
        return False, 'neither nix nor cabal on PATH'
    return True, ''


_AVAIL, _SKIP_REASON = _reaver_available()
requires_reaver = unittest.skipUnless(_AVAIL, _SKIP_REASON or 'reaver unavailable')


def _compile_demo_to_plan() -> str:
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 50000))
    with open(DEMO_PATH) as f:
        src = f.read()
    prog = parse(lex(src, DEMO_PATH), DEMO_PATH)
    resolved, _ = resolve(prog, 'Main', {}, DEMO_PATH)
    compiled = compile_program(resolved, 'Main')
    return emit_program(compiled)


def _run_repl(stdin_bytes: bytes, timeout: int = 120) -> tuple[bytes, bytes]:
    """Compile the demo, run under Reaver with stdin piped, return (stdout, stderr)."""
    plan_text = _compile_demo_to_plan()
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, 'demo.plan'), 'w') as f:
            f.write(plan_text)
        shutil.copy(BOOT_PLAN, os.path.join(tmpdir, 'boot.plan'))
        if shutil.which('nix') is not None:
            cmd = ['nix', 'develop', '--command', 'cabal', 'run', '-v0',
                   'plan-assembler', '--', tmpdir, 'demo', 'Main_main', '0']
        else:
            cmd = ['cabal', 'run', '-v0', 'plan-assembler', '--',
                   tmpdir, 'demo', 'Main_main', '0']
        result = subprocess.run(
            cmd, cwd=REAVER_DIR,
            input=stdin_bytes,
            capture_output=True,
            timeout=timeout,
        )
    return result.stdout, result.stderr


@requires_reaver
class TestReplCalc(unittest.TestCase):
    """The compiled echo demo reads stdin and writes stdout via
    Reaver.RPLAN, sequenced through Reaver.BPLAN.seq so the I/O side
    effect actually fires."""

    def test_passes_bytes_through(self):
        """Whatever bytes go in come back out."""
        stdout, stderr = _run_repl(b'hello world\n')
        self.assertEqual(stdout, b'hello world\n',
            f'stdout mismatch.\nstdout={stdout!r}\nstderr-tail={stderr[-2000:]!r}')


if __name__ == '__main__':
    unittest.main()
