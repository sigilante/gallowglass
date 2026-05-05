#!/usr/bin/env python3
"""
Reaver RPLAN I/O smoke test.

End-to-end gate that the `external mod Reaver.RPLAN { ... }` source
binding plus the bootstrap codegen path produces a Plan Assembler
program Reaver actually runs as a process — reading stdin, writing
stdout — using the runtime ops at `vendor/reaver/src/hs/Plan.hs op 82`.

Test shape: compile a tiny `echo` program that calls `input` then
`output`, emit Plan Asm, run via `plan-assembler <dir> <mod> main`
with bytes piped into stdin, assert the bytes appear on stdout.

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


def _run_reaver_with_stdin(plan_text: str, stdin_bytes: bytes,
                           module: str = 'demo', funcname: str = 'Demo_main',
                           timeout: int = 60) -> tuple[bytes, bytes]:
    """Write plan_text into a temp dir, run `plan-assembler <dir> <mod>
    <fn> 0` with stdin_bytes piped to stdin, return (stdout, stderr).

    The trailing `0` is the CLI arg `runReplFn` applies to `funcname`.
    Reaver encodes args as strNats; the program reads stdin via the
    Reaver.RPLAN.input op.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, f'{module}.plan'), 'w') as f:
            f.write(plan_text)
        shutil.copy(BOOT_PLAN, os.path.join(tmpdir, 'boot.plan'))

        if shutil.which('nix') is not None:
            cmd = ['nix', 'develop', '--command', 'cabal', 'run', '-v0',
                   'plan-assembler', '--', tmpdir, module, funcname, '0']
        else:
            cmd = ['cabal', 'run', '-v0', 'plan-assembler', '--',
                   tmpdir, module, funcname, '0']

        result = subprocess.run(
            cmd, cwd=REAVER_DIR,
            input=stdin_bytes,
            capture_output=True,
            timeout=timeout,
        )
    return result.stdout, result.stderr


def _compile_to_plan(src: str, module: str = 'Demo',
                    trailer: str | None = None) -> str:
    prog = parse(lex(src, '<rplan-io>'), '<rplan-io>')
    resolved, _ = resolve(prog, module, {}, '<rplan-io>')
    compiled = compile_program(resolved, module)
    return emit_program(compiled, trailer=trailer)


@requires_reaver
class TestRplanIO(unittest.TestCase):
    """Compiled gallowglass programs read stdin and write stdout via
    Reaver.RPLAN named ops."""

    def test_echo_passes_bytes_through(self):
        """A program that reads up to N bytes from stdin and writes them
        back out via Reaver.RPLAN.output should produce stdout = stdin
        for any input under the read budget.

        `input n` returns a `bytesBar`-encoded nat: little-endian byte
        contents plus a high-bit length marker at position `len*8`.
        `output` extracts bytes via `natBytes`, which strips the
        high-bit marker. So `output (input n)` is byte-identical
        passthrough for ≤ n input bytes.
        """
        src = '''
external mod Reaver.RPLAN {
  input  : Nat → Nat
  output : Nat → Nat
}

let main : Nat → Nat
  = λ _ → Reaver.RPLAN.output (Reaver.RPLAN.input 1024)
'''
        plan_text = _compile_to_plan(src, module='Demo')
        stdout, stderr = _run_reaver_with_stdin(
            plan_text, b'hello world\n', funcname='Demo_main',
        )
        # Reaver loads boot.plan first which prints binding names to
        # stderr; our actual program output is on stdout. The
        # passthrough should be byte-identical.
        self.assertEqual(stdout, b'hello world\n',
            f'stdout mismatch.\nstdout={stdout!r}\nstderr-tail={stderr[-300:]!r}')


if __name__ == '__main__':
    unittest.main()
