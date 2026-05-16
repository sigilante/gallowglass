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

    def test_match_adt_nullary_multi_arm(self):
        """``type Color = | Red | Green | Blue; let to_nat = ...`` — multi-arm
        nullary constructor match.

        Passing byte-identical already (no fixes from this session needed —
        the path through cg_compile_con_match's all-nullary branch uses
        cg_build_nat_dispatch with the idx-threaded succ-law name fix from
        7ab0dcf).  Pinned here as a regression sentinel.
        """
        src = (
            'type Color = | Red | Green | Blue\n'
            'let to_nat = λ c → match c { | Red → 1 | Green → 2 | Blue → 3 }\n'
            'let main : Nat = to_nat Green\n'
        )
        self._assert_byte_identical(src)

    def test_match_adt_multi_field(self):
        """``type IntList = | INil | ICons Nat IntList; let head_or = ...``.

        Binary-field constructor match (`ICons h t → h`).  Pins the
        `<hint>_inner` lifted-law name in `cg_build_binary_handler_body`
        (matching Python's _compile_con_match line 2801).  Without the
        hint threading, the inner field-binding law gets name `0`,
        diverging from the bootstrap output.

        Other hardcoded-`0` PLaw sites remain in
        cg_build_unary_handler_body's multi-arm path; not exercised
        here.  Tracked as a continuing follow-up.
        """
        src = (
            'type IntList = | INil | ICons Nat IntList\n'
            'let head_or = λ d xs → match xs { | INil → d | ICons h t → h }\n'
            'let main = head_or 99 (ICons 5 INil)\n'
        )
        self._assert_byte_identical(src)

    def test_match_adt_single_field(self):
        """``type Maybe a = | None | Some a; let unwrap = λ m → match m { ... }``.

        Constructor match with one nullary arm (None) and one
        single-field arm (Some n → n).  Pins two fixes:

        * The app-handler lifted-law name in ``cg_build_app_handler`` is
          now ``<hint>_app`` (mirroring Python's _compile_con_match).
          ``cg_build_app_handler`` accepts a hint parameter; the caller
          ``cg_compile_con_match`` threads it.

        * The body-context "constant 0 with no wildcard arm" fallback
          across three sites (`cg_build_reflect_app`, `cg_build_m_body`,
          `cg_build_unary_m_body`) now uses the quote form
          ``PApp (PNat 0) (PNat 0)`` — Plan Asm ``0`` — rather than
          ``PPin (PNat 0)`` which emitted ``(#pin 0)``.  The wire form
          had to match Python's bapp-form fallback shape.

        Multi-field ADT constructors (e.g., ``ICons Nat IntList``)
        still have a remaining ``<hint>_inner`` name divergence;
        tracked separately.
        """
        src = (
            'type Maybe a = | None | Some a\n'
            'let unwrap = λ m → match m { | None → 0 | Some n → n }\n'
            'let main = unwrap (Some 42)\n'
        )
        self._assert_byte_identical(src)

    def test_external_mod_decl(self):
        """``external mod X { sub : Nat }`` — single-item external module.

        Pinned two bugs in the self-host parser that were not exercised
        by any pre-Phase-H fixture:

        * `parse_ext_items` had inverted EOF arms: the `| 0 →` (not-EOF)
          arm returned Nil and the `| k →` (is-EOF) arm continued
          parsing.  Any non-empty external mod body produced an empty
          items list and left the token cursor mid-body, triggering
          sentinel `(#bind Compiler_ 0)` runs in `parse_program`.

        * `tok_skip_ext_type_body` stopped at the FIRST ident after `:`
          regardless of what followed it, so a type like `Nat → Nat`
          treated `Nat` as the next item's name.  Now stops only when
          the ident is followed by `:` (the start of the next item).

        Reproduces with any external mod containing items —
        ``external mod`` is heavily used in Compiler.gls itself,
        so this break would block any compile-self attempt.
        """
        self._assert_byte_identical('external mod X { sub : Nat }')

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

    def test_self_recursion_in_match_wildcard(self):
        """``let count_down = λ n → match n { | 0 → 0 | _ → count_down (sub n 1) }``
        — self-recursive call from inside a nat-match wildcard arm body.

        Pins the Phase H Task #10 fix for lifted-law outer-local capture
        of self-references when ``sr_dispatch`` failed to qualify the bare
        EVar to its FQ form (the documented "deep recursion" sr_dispatch
        gap).  Without the fix, ``cg_body_uses_self`` returns 0 for a body
        carrying a bare ``count_down`` (it only matched FQ
        ``Compiler.count_down``), so the lifted wild_succ law dropped the
        self capture — produced arity 2 instead of arity 3 and lost the
        recursive call.

        The fix is a safety net that mirrors Python's ``_body_uses_self_ref``:

        * New ``cg_short_after_dot`` helper extracts the segment after the
          last ``.`` from an LE-encoded name nat (e.g. ``Compiler.count_down``
          → ``count_down``) using ``Reaver.BPLAN.eq`` for O(1) byte compare
          (the recursive ``nat_eq`` is O(min m n) and would walk a Case_
          chain of cosmic length for encoded-name nats).

        * ``cg_body_uses_self`` checks both the FQ name *and* its short
          tail — either match counts as self-use.

        * ``cg_make_pred_succ_law``, ``cg_build_app_handler``, and
          ``cg_build_binary_handler_body`` alias the short name to the
          same slot as the FQ in their lifted envs, so the body's bare
          ``count_down`` EVar resolves to the captured self slot via
          ``cg_var_from_env``.

        * ``cg_compile_var`` accepts the short tail as a self-reference
          (compiles to ``N(0)``) at the OUTER law level.

        Also pins the related ``cg_var_from_env`` emit fix: when a binding
        is itself ``Pin``'d (e.g. ``Reaver.BPLAN.sub``), the cross-binding
        ref tag is now ``PNamed n val`` (not ``PPin (PNamed n inner)``),
        so emit produces bare ``Reaver_BPLAN_sub`` instead of the wrongly
        double-pinned ``(#pin Reaver_BPLAN_sub)`` — mirrors Python's
        identity-based ``_maybe_symbol`` dedup.
        """
        src = (
            'external mod Reaver.BPLAN {\n'
            '  sub : Nat → Nat → Nat\n'
            '}\n'
            '\n'
            'let count_down = λ n → match n { | 0 → 0 | _ → count_down (Reaver.BPLAN.sub n 1) }\n'
        )
        self._assert_byte_identical(src)

    def test_cross_binding_bare_ref_in_match_wildcard(self):
        """``let helper = λ n → n
            let count_down = λ n → match n { | 0 → 0 | _ → helper (sub n 1) }``
        — cross-binding bare reference inside a nat-match wildcard arm body.

        Sibling of ``test_self_recursion_in_match_wildcard``: same
        sr_dispatch deep-recursion gap (the bare ``helper`` EVar isn't
        qualified to FQ ``Compiler.helper``), but the self-ref short-name
        safety net doesn't fire because ``helper`` ≠ ``count_down``'s
        short.  Pins the globals-by-short fallback in
        ``cg_var_from_env``: when both local and bare-FQ globals lookup
        fail, scan globals for the first FQ whose short tail matches
        the bare name.  Without the fallback, ``helper`` resolves to
        ``PNat 0`` (= the lifted law's self, i.e. emits ``(_0 …)``
        instead of ``((#pin Compiler_helper) …)``).
        """
        src = (
            'external mod Reaver.BPLAN {\n'
            '  sub : Nat → Nat → Nat\n'
            '}\n'
            '\n'
            'let helper = λ n → n\n'
            'let count_down = λ n → match n { | 0 → 0 | _ → helper (Reaver.BPLAN.sub n 1) }\n'
        )
        self._assert_byte_identical(src)

    def test_match_adt_multi_arm_unary_mixed(self):
        """``type Shape = | Empty | Circle Nat | Square Nat; let area = ...``
        — match with one nullary arm + multiple unary arms.

        Pins two coupled fixes for the multi-arm unary-mixed path:

        * ``cg_build_precompiled_nat_dispatch`` now creates its `pred_env`
          by bumping the caller env's arity (preserving locals), so
          pre-compiled arm bodies' slot refs survive into the inner
          succ-law's frame.  Mirrors Python's `_build_tag_chain`'s
          `make_ext_env` + `partially_apply` — without this, the inner
          succ-law had arity 1 instead of `n_cap + 1` and outer locals
          were unreachable.

        * When `tag0 > 0` (no field arm matches Nat 0 — true when a
          nullary constructor precedes unary in the type), the outer
          Elim's z arm is now a fallback (`cg_quote_nat 0 n_cap`) and
          ALL tags shift down by 1.  Mirrors Python's `_build_tag_chain`
          `first_tag > 0` branch.  Implemented via top-level helpers
          `cg_pcd_z_for_op2` and `cg_pcd_pairs_for_inner` to keep the
          conditional at top-of-law (avoids let-lifting into deep
          sub-laws).

        * Also pins ``cg_contab_lookup_safe`` — the constructor name
          lookup falls back to a short-tail search (parallel to the
          globals-by-short fallback) when sr_dispatch failed to qualify
          the bare constructor name in the match arm.  Without this,
          ``contab_lookup ctab "Circle"`` returns None (only finds FQ
          ``Compiler.Circle``), defaults the tag to 0, and the dispatch
          structure collapses.
        """
        src = (
            'type Shape = | Empty | Circle Nat | Square Nat\n'
            'let area = λ s → match s { | Empty → 0 | Circle r → r | Square w → w }\n'
        )
        self._assert_byte_identical(src)

    def test_type_with_parenthesized_field(self):
        """``type Wrapped = | Wrap (Nat) Nat; let mk = Wrap 1 2``
        — constructor with one parenthesized field type and one bare.

        Pins ``parse_con_arity`` counting atom types, not raw tokens.
        The original implementation counted every token between the
        constructor name and the next stop-token, so ``Cons a (List a)``
        parsed as arity 5 (counting ``a``, ``(``, ``List``, ``a``, ``)``)
        instead of 2.  This minimal-repro case parses ``Wrap (Nat) Nat``
        as arity 4 (``(``, ``Nat``, ``)``, ``Nat``) instead of 2 — the
        constructor binding diverges:

            ref:    (#law "1885434455" (_0 _1 _2)         ((0 _1) _2))
            buggy:  (#law "1885434455" (_0 _1 _2 _3 _4)   ((((0 _1) _2) _3) _4))

        The new ``parse_con_arity_go`` (Compiler.gls L2553) tracks paren
        depth and treats each balanced group as a single atom — mirrors
        Python's ``_parse_atom_type`` (bootstrap/parser.py L330).
        Discovered as the first divergence of the compile-self gate.
        """
        src = (
            'type Wrapped = | Wrap (Nat) Nat\n'
            'let mk = Wrap 1 2\n'
        )
        self._assert_byte_identical(src)

    def test_type_with_nested_parens(self):
        """``type Box = | B (Wrap) (Wrap)`` — two parenthesized fields,
        with an inner constructor reference.

        Gates against regression of the paren-depth tracker in
        ``parse_con_arity_go`` (Compiler.gls L2557).  A token-count
        version would parse `B`'s field list as arity 6 (counting
        ``(``, ``Wrap``, ``)``, ``(``, ``Wrap``, ``)``) instead of 2.
        This case is byte-identical only when the depth tracker is
        correctly counting balanced groups as single atoms.
        """
        src = (
            'type Wrap = | Mk Nat\n'
            'type Box = | B (Wrap) (Wrap)\n'
            'let mk = B (Mk 1) (Mk 2)\n'
        )
        self._assert_byte_identical(src)

    def test_nullary_match_captures_outer_local(self):
        """``type Color = | Red | Green | Blue
            let pick = λ d → λ c → match c { | Red → d | Green → d | Blue → d }``
        — multi-arm nullary match whose arm bodies reference an outer
        lambda local.

        Gates two coupled `pred_env` locals-drop bugs flagged in the
        Dwarf review of this Phase H arc:

        * ``cg_build_nat_dispatch``'s multi-arm succ-law (Compiler.gls
          L4527) previously built `pred_env = cenv_make g Nil 1 None`
          — a fresh empty env that drops the caller's locals.  Arm
          bodies referencing outer locals collapsed to ``PNat 0`` (the
          succ law's own self) rather than the captured slot.

        * ``cg_build_m_body`` (Compiler.gls L4832) had the same shape
          for the nullary-tag>0 succ law in mixed con-matches.

        Both now do free-var analysis (mirroring Python's
        `make_succ_law`, bootstrap/codegen.py L1932) and partial-apply
        the captured locals at the call site.  The earlier
        captures-preserving fix in
        `cg_build_precompiled_nat_dispatch` (commit c216e46) addressed
        only the field-bearing path; the parallel paths in
        `cg_build_nat_dispatch` and `cg_build_m_body` survived until
        Dwarf flagged them — they go unexercised by every fixture
        whose arm bodies are pure literals (e.g. ``Red → 1``).
        """
        src = (
            'type Color = | Red | Green | Blue\n'
            'let pick = λ d → λ c → match c { | Red → d | Green → d | Blue → d }\n'
            'let main = pick 99 Red\n'
        )
        self._assert_byte_identical(src)

    def test_single_unary_arm_with_wildcard(self):
        """``match m { | Some x → x | _ → d }`` — single unary field arm
        plus wildcard, where the wildcard body (``d``) references an outer
        lambda parameter.

        Pins two coupled fixes in the App-handler path:

        * ``cg_build_app_handler`` now includes ``wild_body`` in the
          free-variable union before intersecting with ``env.locals``.
          Mirrors Python's ``_build_field_arm_law`` which gathers names
          from field bodies AND wild_body so outer captures referenced
          only by the wild (``d`` here) get lifted as a handler-law
          parameter.  Without this, the wild's reference to ``d`` became
          an unbound slot inside the lifted handler law's frame.

        * ``cg_build_unary_handler_body`` (single-arm path) now wraps the
          arm body in a reflect-dispatch tag-check when a wildcard is
          present, so non-matching constructors (including the Nat-shaped
          nullary case for tag 0) return the wild value instead of
          running the arm body on a mistyped scrutinee.  Mirrors
          Python's ``_build_field_arm_law`` L2664-2710 (``info.tag == 0``
          and ``info.tag > 0`` branches both implemented).

        * ``cg_build_precompiled_nat_dispatch``'s base case (single
          pair, tag0=0, wild=Some) now inlines ``bapp(const2,
          wild_compiled_in_env)`` instead of lifting via
          ``cg_make_wild_succ``.  Mirrors Python's ``_build_tag_chain``
          L2999-3005 — DO NOT regress this back to a ``_wild_succ`` law,
          since Python doesn't lift here and lifting flips byte-identity.
          The ``wild=None`` branch still uses ``cg_make_wild_succ`` to
          produce ``bapp(const2, Nat 0)``, also matching Python.
        """
        src = (
            'type Maybe a = | None | Some a\n'
            'let unwrap_or : Maybe Nat → Nat → Nat\n'
            '  = λ m d → match m { | Some x → x | _ → d }\n'
            'let main = unwrap_or (Some 42) 99\n'
        )
        self._assert_byte_identical(src)

    def test_mutual_recursion_two_member_scc(self):
        """``is_even``/``is_odd`` — two mutually recursive lets that form
        a single SCC.  Pins the Phase H Task A shared-pin encoding:
        Tarjan SCC detection (cg_build_dep_graph + cg_tarjan_scc), a
        selector law `{0 (n+1) 0}` partially applied to the n lambda-
        lifted member laws (cg_build_selector_law + cg_build_shared_row),
        external wrappers of the original arity per member
        (cg_build_mutual_wrapper), and the PMutual sentinel routed
        through cg_var_from_env to emit the `((_1 j) _1)` cross-call.

        Also pins the implicit-``__shared__`` capture rule in
        ``cg_cf_dispatch``: when a body EVar resolves to PMutual in
        globals, ``__shared__`` joins the free-var set so lifted wild-
        arm laws capture slot 1 (the shared row).  Without this, the
        lifted ``_wild_succ`` law would have arity 2 instead of 3 and
        cross-calls would target the wrong slot — see
        ``bootstrap/codegen.py::_collect_free`` L1640-1646.
        """
        src = (
            'external mod Reaver.BPLAN {\n'
            '  sub : Nat → Nat → Nat\n'
            '}\n'
            '\n'
            'let is_even : Nat → Nat\n'
            '  = λ n → match n {\n'
            '      | 0 → 1\n'
            '      | _ → is_odd (Reaver.BPLAN.sub n 1)\n'
            '    }\n'
            '\n'
            'let is_odd : Nat → Nat\n'
            '  = λ n → match n {\n'
            '      | 0 → 0\n'
            '      | _ → is_even (Reaver.BPLAN.sub n 1)\n'
            '    }\n'
        )
        self._assert_byte_identical(src)

    def test_mutual_recursion_app_handler_no_extra_shared(self):
        """Two mutually-recursive lets whose bodies have a constructor
        match referencing the other SCC member; the lifted App-handler law
        therefore sees a PMutual in its arm bodies.

        Pins ``cg_drop_shared`` (the filter that strips the synthetic
        ``__shared__`` name from the App-handler's free-locals).  Python's
        ``_build_field_arm_law`` collects captures via ``_collect_all_names``
        — a purely syntactic walker with no ``__shared__`` implicit-capture
        rule.  Self-host's generic ``cg_free_vars_bodies`` uses
        ``cg_cf_dispatch``, which DOES add ``__shared__`` whenever a body
        resolves to a PMutual.  In an App handler nested inside a wild-pred
        sub-law (whose outer ``_make_pred_succ_law`` frame already captures
        ``__shared__``), the extra capture grew the App handler's arity by 1
        and broke byte-identity with the bootstrap — observable as a
        cascade of +1-arity laws ``_0_wild_pred_app`` and
        ``_0_wild_pred_inner`` inside ``Compiler_parse_expr``.  See
        ``bootstrap/codegen.py::_build_field_arm_law`` L2629 for the
        Python reference.
        """
        src = (
            'external mod Reaver.BPLAN {\n'
            '  sub : Nat → Nat → Nat\n'
            '}\n'
            'type Shape = | Empty | Circle Nat | Square Nat\n'
            '\n'
            'let foo : Nat → Nat\n'
            '  = λ n → match n {\n'
            '      | 0 → 1\n'
            '      | _ → match (Circle n) {\n'
            '          | Empty → 0\n'
            '          | Circle x → bar x\n'
            '          | Square x → bar (Reaver.BPLAN.sub x 1)\n'
            '        }\n'
            '    }\n'
            '\n'
            'let bar : Nat → Nat\n'
            '  = λ n → match n {\n'
            '      | 0 → 2\n'
            '      | _ → foo (Reaver.BPLAN.sub n 1)\n'
            '    }\n'
        )
        self._assert_byte_identical(src)

    def test_match_nullary_arm_plus_wild_on_field_type(self):
        """Constructor match with only nullary explicit arms but a
        wildcard, on a type that has field-bearing siblings.  Python
        routes this through ``_compile_adt_dispatch`` (codegen.py
        L2380) and builds an explicit ``wild_app_handler`` for Elim's
        App branch so an App-shaped scrutinee (e.g. ``Some 42``) fires
        the wild body instead of being returned as-is by the default
        ``id_pin`` handler.  Pins ``cg_build_wild_app_handler`` and the
        ``cg_compile_con_match`` routing change (Phase H Task H).

        Without the fix, the self-host's ``cg_build_nat_dispatch`` path
        wraps the dispatch with ``cg_build_op2`` (App branch = id_pin),
        emitting a ``_wild_succ`` law where the bootstrap emits a
        ``_wild_app`` law.  Observable as the byte-542118 divergence in
        ``Compiler_collect_record_types_go``.
        """
        src = (
            'type Maybe a = | None | Some a\n'
            '\n'
            'let foo : Maybe Nat → Nat\n'
            '  = λ m → match m {\n'
            '      | None → 0\n'
            '      | _ → 1\n'
            '    }\n'
            '\n'
            'let main = foo (Some 42)\n'
        )
        self._assert_byte_identical(src)

    def test_nat_dispatch_first_tag_positive(self):
        """``cg_build_nat_dispatch``'s ``first_tag > 0`` shift path.

        When the outermost nullary tag in a constructor match is > 0
        (e.g. matching only ``TkEof`` from a Token type, where TkEof's
        tag is 4), the dispatch's z slot must be the wild body and the
        m slot must shift down through tag values until reaching tag 0.
        Mirrors bootstrap/codegen.py::_build_nat_dispatch L2025-2078.

        Without the shift path, self-host emits ``body0`` as z
        (incorrect tag semantics) and byte-diverges from REF, which
        chains ``_shifted_…`` sub-laws.  Observable as the byte-550551
        divergence in ``Compiler_collect_record_types_go``.  The guard
        ``idx == 0`` ensures the shift only fires at the outer entry
        (Python checks ``tag0 > 0`` at the top of ``_build_nat_dispatch``,
        not inside its recursive ``dispatch()`` helper).
        """
        src = (
            'external mod Reaver.BPLAN {\n'
            '  sub : Nat → Nat → Nat\n'
            '  eq : Nat → Nat → Nat\n'
            '}\n'
            'type Token = | TkA | TkB | TkC | TkD Nat | TkEof\n'
            'let tok_peek : Nat → Token\n'
            '  = λ n → TkA\n'
            'let tok_tail : Nat → Nat\n'
            '  = λ n → Reaver.BPLAN.sub n 1\n'
            'let tok_is : Nat → Nat → Nat\n'
            '  = λ n k → Reaver.BPLAN.eq n k\n'
            'let go : Nat → Nat → Nat\n'
            '  = λ toks acc →\n'
            '      match (tok_peek toks) {\n'
            '        | TkEof → acc\n'
            '        | _ →\n'
            '            match (tok_is toks 5) {\n'
            '              | 0 → go (tok_tail toks) acc\n'
            '              | k → go (tok_tail (tok_tail toks)) acc\n'
            '            }\n'
            '      }\n'
            'let main = go 100 0\n'
        )
        self._assert_byte_identical(src)

    # ------------------------------------------------------------------
    # Phase I — language-coverage parity for 1.0.0-rc3.  Each fixture
    # below exercises a feature that the bootstrap supports but that
    # ``Compiler.gls`` itself does not use directly (so Phase H's
    # compile-self gate doesn't pin them).  A passing fixture proves
    # the self-host's codegen for that feature is byte-identical to
    # the bootstrap; an ``xfail`` flags a known gap.
    # ------------------------------------------------------------------

    def test_fix_lambda_anonymous_recursion(self):
        """``fix λ self n → …`` — anonymous recursion via fix.

        Pins ``cg_compile_fix`` in the self-host: the fix expression
        binds the lambda's first param to the law's own self-pin so
        the body can recurse without a top-level name.
        """
        src = (
            'external mod Reaver.BPLAN {\n'
            '  sub : Nat → Nat → Nat\n'
            '}\n'
            'let countdown : Nat → Nat\n'
            '  = fix λ self n → match n {\n'
            '      | 0 → 999\n'
            '      | _ → self (Reaver.BPLAN.sub n 1)\n'
            '    }\n'
            'let main = countdown 5\n'
        )
        self._assert_byte_identical(src)

    def test_or_pattern_constructor(self):
        """``match c { | Red | Green → 1 | _ → 0 }`` — or-pattern
        across two nullary constructors of the same type.

        Pins the ctab ``has_field_sib`` flag (Phase I rc3-3):
        ``cg_compile_con_match``'s no-field-arms + wild branch routes
        through ``cg_build_nat_dispatch`` (App branch = id_pin) for
        pure-nullary types, matching Python's ``_build_nat_dispatch``
        path.  Previously self-host unconditionally lifted a
        ``wild_app_handler``, diverging from the bootstrap whenever a
        pure-nullary type was matched with a wild.
        """
        src = (
            'type Color = | Red | Green | Blue\n'
            'let is_warm : Color → Nat\n'
            '  = λ c → match c {\n'
            '      | Red | Green → 1\n'
            '      | _ → 0\n'
            '    }\n'
            'let main = is_warm Red\n'
        )
        self._assert_byte_identical(src)

    def test_or_pattern_nat(self):
        """``match n { | 0 | 1 → 0 | _ → 1 }`` — or-pattern across
        two Nat literals.  Pins the Nat-or-pattern handling in
        ``parse_match_arm_pe`` (Phase I rc3-3): when the Nat arm is
        followed by ``|`` instead of ``→``, recurse to collect the
        alternatives and synthesise a shared body, mirroring
        ``arm_con_upper_pe`` for constructor or-patterns."""
        src = (
            'let classify : Nat → Nat\n'
            '  = λ n → match n {\n'
            '      | 0 | 1 → 0\n'
            '      | _ → 1\n'
            '    }\n'
            'let main = classify 0\n'
        )
        self._assert_byte_identical(src)

    def test_list_literal_empty(self):
        """``[]`` desugars to ``Nil``."""
        src = (
            'type List a = | Nil | Cons a (List a)\n'
            'let xs : List Nat = []\n'
            'let main = xs\n'
        )
        self._assert_byte_identical(src)

    def test_list_literal_three(self):
        """``[1, 2, 3]`` desugars to ``Cons 1 (Cons 2 (Cons 3 Nil))``.
        Pins the top-level App inlining fix in ``cg_resolve_global_val``
        (Phase I rc3-3): a top-level ``let main = xs`` whose RHS is an
        App-valued global emits the structural App inlined, matching
        Python's emit-side behaviour where ``_bind_skip_id`` suppresses
        bind-symbol dedup for the value currently being emitted."""
        src = (
            'type List a = | Nil | Cons a (List a)\n'
            'let xs : List Nat = [1, 2, 3]\n'
            'let main = xs\n'
        )
        self._assert_byte_identical(src)

    def test_list_cons_pattern(self):
        """``match xs { | [] → 0 | h :: t → h }`` — list patterns
        with the cons operator."""
        src = (
            'type List a = | Nil | Cons a (List a)\n'
            'let head_or_zero : List Nat → Nat\n'
            '  = λ xs → match xs {\n'
            '      | [] → 0\n'
            '      | h :: t → h\n'
            '    }\n'
            'let main = head_or_zero [42]\n'
        )
        self._assert_byte_identical(src)

    def test_guard_pattern(self):
        """``match n { | x if guard → 1 | _ → 0 }`` — guarded match arm.
        Bootstrap M15.5 (per ``bootstrap/parser.py::_parse_match_arm``
        L1109) uses the ``if`` keyword between pattern and guard.
        Pins the self-host's existing guard desugar in
        ``parse_match_expr_pe`` (binds ``__guard_scrut``, rewrites
        each guarded arm into ``| pat → if guard then body else
        match __guard_scrut { remaining }``)."""
        src = (
            'external mod Reaver.BPLAN {\n'
            '  eq : Nat → Nat → Nat\n'
            '}\n'
            'let is_seven : Nat → Nat\n'
            '  = λ n → match n {\n'
            '      | x if Reaver.BPLAN.eq x 7 → 1\n'
            '      | _ → 0\n'
            '    }\n'
            'let main = is_seven 7\n'
        )
        self._assert_byte_identical(src)

    def test_record_construct(self):
        """``type Pt = { x : Nat, y : Nat }; let p = { x = 1, y = 2 }`` —
        record type and construction.  Bootstrap M15.1.

        Pins two coupled fixes in Phase I rc3-3:
        * ``parse_record_fields_go`` had its EOF check arms inverted,
          so it returned (count=0, names=[]) immediately for any
          non-EOF, non-``}`` token — every record-type declaration
          silently produced a nullary constructor with arity 0.
        * ``skip_record_field_type`` didn't actually advance past
          the field's type tokens (it returned the token stream
          unchanged for any non-comma, non-RBrace, non-EOF token).
        Both are now fixed; records construct, project, and pattern
        all work byte-identically with the bootstrap."""
        src = (
            'type Pt = { x : Nat, y : Nat }\n'
            'let origin : Pt = { x = 0, y = 0 }\n'
            'let main = origin\n'
        )
        self._assert_byte_identical(src)

    def test_record_pattern(self):
        """``match p { | { x = a, y = b } → add a b }`` — record
        pattern.  Note: ``sum_xy { x = 3, y = 4 }`` parses as record
        UPDATE on sum_xy, not function application — use a bound
        intermediate to disambiguate."""
        src = (
            'external mod Reaver.BPLAN {\n'
            '  add : Nat → Nat → Nat\n'
            '}\n'
            'type Pt = { x : Nat, y : Nat }\n'
            'let sum_xy : Pt → Nat\n'
            '  = λ p → match p {\n'
            '      | { x = a, y = b } → Reaver.BPLAN.add a b\n'
            '    }\n'
            'let pt : Pt = { x = 3, y = 4 }\n'
            'let main : Nat = sum_xy pt\n'
        )
        self._assert_byte_identical(src)

    @unittest.expectedFailure
    def test_typeclass_simple(self):
        """``class Eq_t a { eq_t : a → a → Bool }; instance Eq_t Nat {…}``
        — single-method typeclass, single Nat instance.  Bootstrap M11.

        Gap: self-host does not yet desugar ``class`` / ``instance``
        declarations into dictionary-passing.  Bootstrap's
        ``DeclClass`` / ``DeclInst`` handling needs to be ported."""
        src = (
            'external mod Reaver.BPLAN {\n'
            '  eq : Nat → Nat → Nat\n'
            '}\n'
            'class Eq_t a {\n'
            '  eq_t : a → a → Nat\n'
            '}\n'
            'instance Eq_t Nat {\n'
            '  eq_t = λ a b → Reaver.BPLAN.eq a b\n'
            '}\n'
            'let main : Nat = eq_t 7 7\n'
        )
        self._assert_byte_identical(src)

    @unittest.expectedFailure
    def test_do_notation_simple(self):
        """``x ← rhs in body`` — do-notation bind inside ``handle``.
        Bootstrap M10 (CPS transform for effect handlers).

        Gap: self-host's CPS codegen for effects doesn't match the
        bootstrap byte-for-byte (or doesn't fire at all in some
        paths).  Needs investigation."""
        src = (
            'eff State {\n'
            '  get : Nat → Nat\n'
            '}\n'
            'let prog : Nat\n'
            '  = handle (do n ← State.get 0 in pure n) {\n'
            '      | return v → v\n'
            '      | get _ k → k 42\n'
            '    }\n'
            'let main = prog\n'
        )
        self._assert_byte_identical(src)

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


@requires_reaver
@unittest.skipUnless(
    os.environ.get('GALLOWGLASS_RUN_COMPILE_SELF') == '1',
    'compile-self fixed-point gate is slow (~20-25 min under Reaver, '
    'no jets); set GALLOWGLASS_RUN_COMPILE_SELF=1 to run it.',
)
class TestPhaseHFixedPoint(unittest.TestCase):
    """Phase H — compile-self fixed point.

    The Reaver-hosted self-host compiler (``main_reaver`` driving the
    bootstrap-compiled ``Compiler.gls``) must produce output
    byte-identical to the Python bootstrap when both compile
    ``compiler/src/Compiler.gls`` itself.  This is the canonical
    self-hosting property — once true, the self-host can replace the
    Python bootstrap.

    Gated behind ``GALLOWGLASS_RUN_COMPILE_SELF=1`` because the run
    takes ~20-25 minutes under Reaver (no jet substrate for arithmetic
    yet — post-1.0).  CI and slow-suites should set the var; default
    pytest runs skip it.
    """

    _TIMEOUT = 1800  # 30 min — Reaver no-jets is slow.

    def test_compile_self(self):
        """Feed ``compiler/src/Compiler.gls`` to the Reaver-hosted
        self-host; assert byte-identity to the Python bootstrap output
        of the same source.
        """
        # Match the recursion limit used by `_compile_compiler_to_plan`
        # — Compiler.gls's nested ADT dispatch trees exceed Python's
        # default 1000-frame limit during emit.
        sys.setrecursionlimit(max(sys.getrecursionlimit(), 50000))
        with open(COMPILER_GLS) as f:
            src = f.read()

        # Python reference
        prog = parse(lex(src, COMPILER_GLS), COMPILER_GLS)
        resolved, _ = resolve(prog, 'Compiler', {}, COMPILER_GLS)
        compiled = compile_program(resolved, 'Compiler')
        reference = emit_program(compiled).encode()

        try:
            stdout, stderr, exit_code = _run_compiler(
                src.encode(), timeout=self._TIMEOUT
            )
        except subprocess.TimeoutExpired:
            self.fail(
                f'compile-self timed out after {self._TIMEOUT}s.  '
                f'Reaver no-jet arithmetic is slow; raise _TIMEOUT or '
                f'wait for jets.'
            )
        self.assertEqual(
            exit_code, 0,
            f'main_reaver failed (exit {exit_code}):\n'
            f'stderr-tail={stderr[-1500:]!r}',
        )
        self.assertEqual(
            len(stdout), len(reference),
            f'compile-self output length mismatch: '
            f'actual={len(stdout)} bytes, reference={len(reference)} bytes',
        )
        self.assertEqual(
            stdout, reference,
            f'compile-self byte-identity FAILED.  '
            f'First mismatch position: {next((i for i in range(min(len(stdout), len(reference))) if stdout[i:i+1] != reference[i:i+1]), -1)}',
        )


if __name__ == '__main__':
    unittest.main()
