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
        """``let id = fn x -> x`` — Law emit; validates lambda codegen.

        ``id`` encodes to strNat 25705 (little-endian 'i'=0x69, 'd'=0x64),
        keeping nat comparisons cheap.  Exercises the full lambda/Law emit
        path without triggering multi-billion-step nat_eq calls.

        Uses ASCII ``->`` to exercise the lexer's ASCII arrow path
        alongside the Unicode ``→`` recognition.
        """
        self._assert_byte_identical('let id = fn x -> x')

    def test_type_annotation(self):
        """``let zz : Nat = 1`` — exercises ``skip_ann`` / ``tok_skip_to_equal``.

        Without the type annotation the parser never enters
        ``tok_skip_to_equal``; the residual ``nat_eq``-on-Token bug there
        was undetectable from the bare-let fixtures.  This pins the
        BPLAN.eq fix at ``compiler/src/Compiler.gls:1356``.
        """
        self._assert_byte_identical('let zz : Nat = 1')

    def test_multi_decl_source_order(self):
        """Two top-level bindings emit in source order, not reverse.

        ``compile_program`` accumulates by prepending; without an explicit
        ``reverse`` at the boundary the output flipped the binding order
        relative to the Python bootstrap.  Single-decl fixtures never
        surfaced this.
        """
        self._assert_byte_identical(
            'let two_decls_a = 7\nlet two_decls_b = 9'
        )

    def test_nested_lambda(self):
        """``let kk = fn x -> fn y -> x`` — `fn`-`->`-`fn`-`->` chain.

        Exercises ``parse_lambda_params`` recursion across multiple ``fn``
        keywords and the lambda body re-entering ``parse_expr_dispatch``.
        Bootstrap-emitted Law has arity 2 (both params lifted into a
        single law), body is the first parameter slot.

        ``kk`` rather than ``k`` because single-letter snake-case names
        in ``[a..q]`` are type variables, not let bindings.
        """
        self._assert_byte_identical('let kk = fn x -> fn y -> x')

    def test_application_in_body(self):
        """``let ap = fn ff -> fn xx -> ff xx`` — emits an App in the law body.

        Pins the ``(_1 _2)`` body-apply form (``emit_bval_papp_*``).
        Previously xfailed: the self-host's ``cg_is_lam`` (single-arm
        binary constructor match) miscompiled and treated ``EApp`` as
        ``ELam``, so ``cg_flatten_lam`` over-collected params, producing
        arity 3 + body ``_2`` instead of arity 2 + body ``(_1 _2)``.
        Fixed by adding a tag check to the binary single-arm path in
        ``bootstrap/codegen.py::_build_field_arm_law`` (same class of bug
        as the unary "wildcard arm drop" already documented in
        ``CLAUDE.md §Bootstrap Codegen Pitfalls``).
        """
        self._assert_byte_identical('let ap = fn ff -> fn xx -> ff xx')

    def test_match_top_level_wildcard(self):
        """``let mm = match 0 { | _ → 9 }`` — top-level match, wildcard arm only.

        Pinned the parser fix: ``parse_app_go_pe`` only treats ``{`` as a
        record update when the next token is an identifier.  Without the
        lookahead, ``match 0 { ... }`` mis-parsed as ``0 { ... }`` (a
        record update of nat 0) and the ``| _ → 9`` was consumed as
        garbage record fields, leaving the match arms empty.  Bug was
        pre-existing; surfaced once the Phase H if/match fixtures were
        added.
        """
        self._assert_byte_identical('let mm = match 0 { | _ → 9 }')

    def test_match_multi_arm(self):
        """``let classify = λ n → match n { | 0 → 10 | 1 → 20 | 2 → 30 | _ → 99 }``.

        Exercises the multi-arm nat-dispatch path with three named arms +
        a wildcard.  Pins the ``idx`` threading through
        ``cg_build_nat_dispatch``: each succ law in the chain is named
        ``<hint>_succ_<idx>`` (matching Python's
        _build_nat_dispatch.make_succ_law line 1969).  Without idx
        threading every succ law gets name 0, diverging in the
        ``(#law "<name>" ...)`` field at every level.

        Also pins the new ``cg_b_decimal`` helper (small LE-packed
        decimal-byte encoder) which makes the suffix idx names like
        ``_succ_1``, ``_succ_2`` constructible inside the codegen layer
        without depending on the later-defined emit-layer
        ``nat_to_decimal``.
        """
        self._assert_byte_identical(
            'let classify = λ n → match n { | 0 → 10 | 1 → 20 | 2 → 30 | _ → 99 }'
        )

    def test_match_nat_in_function_body(self):
        """``let pick = λ x → match x { | 0 → 100 | _ → 200 }`` — match
        inside a lambda body (arity > 0).  Exercises the full nat-dispatch
        chain plus three further pre-existing fixes surfaced together:

          * ``emit_bval_papp_nat`` quote-form handling — ``(0 x)`` with
            non-nat x now renders as ``ep x`` (top-level form), not
            ``(_0 x_body)`` which mis-applied slot 0.
          * ``cg_make_wild_succ`` — lambda-lifts the wildcard body at
            arity > 0 via ``cg_make_pred_succ_law`` instead of plain
            ``const2``-wrap, so outer-local captures thread through;
            mirrors Python's _build_nat_dispatch dispatch lines 1988-1997.
          * ``cg_compile_lam_as_law`` / ``cg_compile_lam_lifted`` — pass
            the parent ``hint`` (not ``0``) into the body compile, so
            nested lifted laws get the proper ``<hint>_wild_succ`` name.
        """
        self._assert_byte_identical(
            'let pick = λ x → match x { | 0 → 100 | _ → 200 }'
        )

    def test_if_expression(self):
        """``let mm = if 1 then 5 else 10`` — pins Phase H #1 + #2 end-to-end.

        Exercises the full BPLAN-66-wrapped Elim dispatch:

          * ``cg_build_op2`` emits ``((#pin 66) (Elim id id id z m scr))``
            with the BPLAN ``'B'`` gateway pin and the name nat for
            ``Elim`` as the inner head (commit 65efbc4).
          * ``cg_compile_if`` lambda-lifts both branches into Pin'd 1-arg
            thunk laws via ``cg_make_pred_succ_law`` and appends the
            ``N(0)`` trampoline (commit 65efbc4).
          * ``cg_make_pred_succ_law`` names each lifted law
            ``<hint>_then_succ`` / ``<hint>_else_succ`` via
            ``cg_concat_under`` (commit 457e5a5), matching Python's
            ``encode_name(name_hint + '_succ')``.
          * ``cg_quote_nat`` always quote-wraps body-context literals so
            the thunk bodies emit ``5`` / ``10`` as quoted constants, not
            as ``_5`` / ``_10`` slot refs (commit 457e5a5).
          * Cross-binding refs to ``id_law`` and ``const2_law`` flow
            through the new ``PNamed`` variant rather than inlining
            (commit 3f0766c).

        Locks in roughly 300 bytes of byte-identity across every
        Phase H foundational fix.
        """
        self._assert_byte_identical('let mm = if 1 then 5 else 10')

    @unittest.expectedFailure
    def test_same_constructor_literal_field_collapses(self):
        """`match (MkPair 0 99) { | MkPair 0 _ -> 1 | MkPair n _ -> 2 }` —
        the same-constructor collapse pass auto-rewrites this in the
        Python bootstrap (``bootstrap/codegen.py::_collapse_same_tag_arms``).
        Currently xfail: the Gallowglass self-host's own codegen
        (``compiler/src/Compiler.gls``) does not yet carry that pass, so
        the self-host trips on the old behaviour.  Porting the collapse
        to ``Compiler.gls`` closes this.  AUDIT.md D9 follow-up.
        """
        src = (
            'type Pair a b = | MkPair a b\n'
            'let tt = match (MkPair 0 99) {\n'
            '  | MkPair 0 _ -> 1\n'
            '  | MkPair n _ -> 2\n'
            '}'
        )
        self._assert_byte_identical(src)


if __name__ == '__main__':
    unittest.main()
