#!/usr/bin/env python3
"""
Phase G #2 smoke tests — bootstrap-compiled `compiler/src/Compiler.gls`
runs as a Reaver process via `main_reaver`.

The test suite is deliberately narrow:

* `test_compiler_loads_under_reaver` — the bootstrap-emitted Plan
  Assembler text parses cleanly under Reaver. After AUDIT.md D8, all
  ~568 bindings (including the new `read_all_loop`,
  `decode_input_chunk`, `bytesBar_encode`, and `main_reaver`) load
  without `law: unbound` or other parse errors.
* `test_main_reaver_empty_source_runs` — invoking `main_reaver` with
  empty stdin drains zero bytes from stdin, runs the pure `main`
  pipeline on `(MkPair 0 0)`, and writes the empty result to stdout.
  The test asserts the process exits cleanly.

Phase G #3 (forward work) will add the byte-identity test against
non-trivial fixtures, gated on the recursive-arithmetic emit path
either becoming fast enough (Reaver jet matching) or being scoped to
small enough fixtures.
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
COMPILER_GLS = os.path.join(REPO_ROOT, 'compiler', 'src', 'Compiler.gls')


def _reaver_available() -> tuple[bool, str]:
    if not os.path.isdir(REAVER_DIR):
        return False, f'{REAVER_DIR} not present — run tools/vendor.sh'
    if not os.path.isfile(BOOT_PLAN):
        return False, 'vendor/reaver/src/plan/boot.plan not present'
    if not os.path.isfile(COMPILER_GLS):
        return False, f'{COMPILER_GLS} not present'
    if shutil.which('nix') is None and shutil.which('cabal') is None:
        return False, 'neither nix nor cabal on PATH'
    return True, ''


_AVAIL, _SKIP_REASON = _reaver_available()
requires_reaver = unittest.skipUnless(_AVAIL, _SKIP_REASON or 'reaver unavailable')


_COMPILED_PLAN_TEXT: str | None = None


def _compile_compiler_to_plan() -> str:
    """Bootstrap-compile `compiler/src/Compiler.gls` to Plan Assembler text.
    Cached at module level so the heavy compile only runs once per test
    session.
    """
    global _COMPILED_PLAN_TEXT
    if _COMPILED_PLAN_TEXT is not None:
        return _COMPILED_PLAN_TEXT
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 50000))
    with open(COMPILER_GLS) as f:
        src = f.read()
    prog = parse(lex(src, COMPILER_GLS), COMPILER_GLS)
    resolved, _ = resolve(prog, 'Compiler', {}, COMPILER_GLS)
    compiled = compile_program(resolved, 'Compiler')
    _COMPILED_PLAN_TEXT = emit_program(compiled)
    return _COMPILED_PLAN_TEXT


def _run_compiler(stdin_bytes: bytes, *, function: str = 'Compiler_main_reaver',
                  arg: str = '0', timeout: int = 60) -> tuple[bytes, bytes, int]:
    """Run the bootstrap-emitted Compiler.gls under Reaver with given stdin.
    Returns (stdout, stderr, exit_code).
    """
    plan_text = _compile_compiler_to_plan()
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, 'compiler.plan'), 'w') as f:
            f.write(plan_text)
        shutil.copy(BOOT_PLAN, os.path.join(tmpdir, 'boot.plan'))
        if shutil.which('nix') is not None:
            cmd = ['nix', 'develop', '--command', 'cabal', 'run', '-v0',
                   'plan-assembler', '--', tmpdir, 'compiler', function, arg]
        else:
            cmd = ['cabal', 'run', '-v0', 'plan-assembler', '--',
                   tmpdir, 'compiler', function, arg]
        result = subprocess.run(
            cmd, cwd=REAVER_DIR, input=stdin_bytes,
            capture_output=True, timeout=timeout,
        )
    return result.stdout, result.stderr, result.returncode


@requires_reaver
class TestPhaseG2Smoke(unittest.TestCase):
    """Phase G #2 smoke tests — Compiler.gls runs as a Reaver process.

    These tests are *not* the byte-identity self-host gate (Phase G
    #3 forward work). They check the I/O surface — that
    bootstrap-compiled `Compiler.gls` parses cleanly under Reaver and
    that `main_reaver` can be invoked without crashing on empty
    input.
    """

    def test_compiler_loads_under_reaver(self):
        """All bindings emitted from Compiler.gls parse cleanly under
        Reaver — no `law: unbound`, no `parse error`, no missing
        primops. Pre-AUDIT.md-D8 this test would have failed at
        `Compiler_lex_skip_ws` with `law: unbound: "_3"`.
        """
        # Loading-only invocation (no function name) verifies the
        # text format parses.  Reaver's `loadAssembly` returns N 0
        # when no function is given.
        plan_text = _compile_compiler_to_plan()
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, 'compiler.plan'), 'w') as f:
                f.write(plan_text)
            shutil.copy(BOOT_PLAN, os.path.join(tmpdir, 'boot.plan'))
            if shutil.which('nix') is not None:
                cmd = ['nix', 'develop', '--command', 'cabal', 'run', '-v0',
                       'plan-assembler', '--', tmpdir, 'compiler']
            else:
                cmd = ['cabal', 'run', '-v0', 'plan-assembler', '--',
                       tmpdir, 'compiler']
            result = subprocess.run(
                cmd, cwd=REAVER_DIR, capture_output=True, timeout=60,
            )
        # Exit code 0 means all bindings loaded.  Reaver's stderr in
        # success contains only the binding-name listing (one quoted
        # symbol per line).  A failure would surface as a non-zero
        # exit and a `law:` or `error:` line in stderr.
        self.assertEqual(
            result.returncode, 0,
            f'plan-assembler load failed (exit {result.returncode}):\n'
            f'stderr-tail={result.stderr[-1500:]!r}',
        )
        # Sanity: at least the new Phase G entry points are present
        # in the binding listing.
        combined = (result.stdout + result.stderr).decode('utf-8', errors='replace')
        for sym in [
            'Compiler_main_reaver',
            'Compiler_read_all',
            'Compiler_bytesBar_encode',
            'Reaver_RPLAN_input',
            'Reaver_RPLAN_output',
            'Reaver_BPLAN_add',
            'Reaver_BPLAN_bex',
        ]:
            self.assertIn(sym, combined, f'expected symbol {sym!r} not in load listing')

    def test_main_reaver_empty_source_runs(self):
        """Invoke `main_reaver` with empty stdin.  The pure `main`
        pipeline runs on `MkPair 0 0`, returns `MkPair 0 0`, and
        `bytesBar_encode (MkPair 0 0) = bex 0 = 1`.  `Output 1` writes
        zero bytes (Reaver's `natBytes 1` is empty — the topmost byte
        is the marker).  The test asserts the process exits cleanly
        with empty stdout.
        """
        stdout, stderr, exit_code = _run_compiler(b'')
        self.assertEqual(
            exit_code, 0,
            f'main_reaver on empty stdin failed (exit {exit_code}):\n'
            f'stderr-tail={stderr[-1500:]!r}',
        )
        # Stdout from `Output 1` is zero bytes (natBytes drops the
        # marker byte).  Anything else means main_reaver fell into an
        # unexpected branch.
        self.assertEqual(
            stdout, b'',
            f'expected empty stdout for empty source, got {stdout!r}\n'
            f'stderr-tail={stderr[-500:]!r}',
        )


if __name__ == '__main__':
    unittest.main()
