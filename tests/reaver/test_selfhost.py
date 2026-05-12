#!/usr/bin/env python3
"""
Phase G smoke and byte-identity tests — bootstrap-compiled
`compiler/src/Compiler.gls` runs as a Reaver process via `main_reaver`.

Phase G #2 smoke tests (``TestPhaseG2Smoke``):

* ``test_compiler_loads_under_reaver`` — the bootstrap-emitted Plan
  Assembler text parses cleanly under Reaver.  All bindings (including
  ``read_all_loop``, ``decode_input_chunk``, ``bytesBar_encode``, and
  ``main_reaver``) load without ``law: unbound`` or parse errors.
* ``test_main_reaver_empty_source_runs`` — invoking ``main_reaver``
  with empty stdin runs the pure ``main`` pipeline on ``(MkPair 0 0)``
  and writes the empty result to stdout.  The test asserts a clean exit
  with empty stdout.

Phase G #3 byte-identity gate (``TestPhaseG3ByteIdentity``):

* Feeds small, prelude-free Gallowglass source to ``main_reaver`` and
  asserts that stdout is byte-identical to the Python bootstrap
  compiler's output for the same source compiled with module name
  ``Compiler``.  Validates the full round-trip:

    stdin bytes → bytesBar decode → PLAN compiler pipeline
    → bytesBar encode → natBytes → stdout bytes

  Timeout 300 s per fixture — conservative because Reaver has no jet
  substrate for recursive arithmetic yet.
"""

import os
import resource
import shutil
import signal
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

    Safety: the subprocess is placed in its own process group (start_new_session)
    so that on timeout the entire nix/cabal/plan-assembler child tree is killed
    with SIGKILL — not just the outermost nix wrapper.  Orphaned plan-assembler
    processes accumulating unbounded PLAN thunks were the cause of the 108GB OOM
    kernel panic; this guarantees the process tree is dead before we return.

    A 4 GB virtual-address cap is applied via RLIMIT_AS so that even if SIGKILL
    delivery is delayed (macOS D-state during heavy swap), the process can't
    allocate past the limit.
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

        _4GB = 4 * 1024 ** 3

        def _limit():
            try:
                resource.setrlimit(resource.RLIMIT_AS, (_4GB, _4GB))
            except (ValueError, resource.error):
                pass  # platform may not support AS limit; best-effort

        proc = subprocess.Popen(
            cmd, cwd=REAVER_DIR,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # own process group → kill whole tree
            preexec_fn=_limit,
        )
        try:
            stdout, stderr = proc.communicate(input=stdin_bytes, timeout=timeout)
        except subprocess.TimeoutExpired:
            # SIGKILL the entire process group (nix + cabal + plan-assembler).
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = proc.communicate()
            except Exception:
                stdout, stderr = b'', b''
            raise
        return stdout, stderr, proc.returncode


@requires_reaver
class TestPhaseG2Smoke(unittest.TestCase):
    """Phase G #2 smoke tests — Compiler.gls runs as a Reaver process.

    These tests check the I/O surface — that bootstrap-compiled
    ``Compiler.gls`` parses cleanly under Reaver and that ``main_reaver``
    can be invoked without crashing on empty input.  Byte-identity
    correctness is in ``TestPhaseG3ByteIdentity``.
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
        pipeline runs on `MkPair 0 0`, parses zero declarations, and
        `emit_program` emits just the `@boot\\n` prelude (6 bytes) so
        the result loads under Reaver alongside Reaver's named primitives.
        The test asserts the process exits cleanly with `b'@boot\\n'`.
        """
        stdout, stderr, exit_code = _run_compiler(b'')
        self.assertEqual(
            exit_code, 0,
            f'main_reaver on empty stdin failed (exit {exit_code}):\n'
            f'stderr-tail={stderr[-1500:]!r}',
        )
        # `emit_program []` returns the `@boot\n` prelude bytes.
        self.assertEqual(
            stdout, b'@boot\n',
            f"expected b'@boot\\n' for empty source, got {stdout!r}\n"
            f'stderr-tail={stderr[-500:]!r}',
        )


@requires_reaver
class TestPhaseG3ByteIdentity(unittest.TestCase):
    """Phase G #3 — byte-identity self-host gate.

    For each fixture, the Python bootstrap compiler (module name
    ``Compiler``) and the PLAN self-hosting compiler (``main_reaver``,
    which hardcodes ``nn_Compiler = 8243113893085146947``) must produce
    bit-for-bit identical Plan Assembler text.

    Fixtures are intentionally prelude-free so ``resolve`` can use an
    empty module-env dict, matching the PLAN compiler's own startup
    state.

    Timeout 300 s is conservative — Reaver interprets every PLAN
    reduction step in Haskell with no jet substrate yet for arithmetic.
    """

    _TIMEOUT = 300

    @classmethod
    def _reference(cls, src: str) -> bytes:
        prog = parse(lex(src, '<fixture>'), '<fixture>')
        resolved, _ = resolve(prog, 'Compiler', {}, '<fixture>')
        compiled = compile_program(resolved, 'Compiler')
        return emit_program(compiled).encode()

    def _assert_byte_identical(self, src: str) -> None:
        reference = self._reference(src)
        try:
            stdout, stderr, exit_code = _run_compiler(
                src.encode(), timeout=self._TIMEOUT
            )
        except subprocess.TimeoutExpired:
            self.fail(
                f'main_reaver timed out after {self._TIMEOUT}s for fixture {src!r}.\n'
                f'Reaver has no jet substrate for arithmetic yet; '
                f'raise _TIMEOUT or wait for jets.'
            )
        self.assertEqual(
            exit_code, 0,
            f'main_reaver failed (exit {exit_code}) for fixture {src!r}:\n'
            f'stderr-tail={stderr[-1500:]!r}',
        )
        self.assertEqual(
            stdout, reference,
            f'byte-identity mismatch for {src!r}:\n'
            f'  reference={reference!r}\n'
            f'  actual   ={stdout!r}\n'
            f'  stderr-tail={stderr[-500:]!r}',
        )

    def test_single_nat_binding(self):
        """``let xx = 42`` — Nat literal; exercises the full lex→emit path."""
        self._assert_byte_identical('let xx = 42')

    def test_identity_function(self):
        """``let id = fn x → x`` — Law emit; validates lambda codegen.

        ``id`` encodes to strNat 25705 (little-endian 'i'=0x69, 'd'=0x64),
        keeping nat comparisons cheap.  Exercises the full lambda/Law emit
        path without triggering multi-billion-step nat_eq calls.

        Uses Unicode arrow ``→`` (E2 86 92) — Compiler.gls's lexer only
        recognises the Unicode form; ASCII ``->`` is a future-work item.
        """
        self._assert_byte_identical('let id = fn x → x')


if __name__ == '__main__':
    unittest.main()
