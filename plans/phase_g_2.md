# Phase G #2 — `Compiler.main` I/O re-shape for Reaver

**Status:** planned, pre-flight checks pending
**Owner:** —
**Roadmap reference:** `ROADMAP.md` § "Phase G — RPLAN self-host validation on Reaver" deliverable 2

## Goal

Make the bootstrap-compiled `compiler/src/Compiler.gls` run as a
Reaver process: read source on stdin, run the existing
`lex → parse → resolve → compile → emit` pipeline, write Plan Asm
on stdout. The pure `main : Bytes → Bytes` stays as-is; we add a new
top-level `main_reaver : Nat → Nat` alongside it.

This is the load-bearing piece that turns the rest of Phase G
(deliverables 3–5: a self-host test, CI gate, and lifting the seven
deferred GLS skips) into mostly test/infrastructure work.

## Current state

`Compiler.gls:6394`:

```gls
let main : Bytes → Bytes
  = λ src →
      let tokens   = lex src 0 1 1 in
      let rt       = collect_record_types tokens in
      let decls    = parse_program rt tokens in
      let decls2   = resolve_program decls nn_Compiler in
      let compiled = compile_program decls2 nn_Compiler in
      emit_program compiled
```

A `run_main : Nat → Nat` further down targets the deprecated
xocore-tech `planvm` runtime via `Core.IO.Prim.write_op` (op 9).
Reaver has no op 9; `run_main` is dead code on the Reaver path. We
keep it (zero churn cost) and add a Reaver-flavored entry alongside.

## What the I/O wrapper needs

Five pieces, none individually deep:

1. **`external mod Reaver.RPLAN` and `external mod Reaver.BPLAN`
   declarations.** Bootstrap recognises these via PR #82 and PR #83
   (`bplan_deps.PRELUDE_INTRINSICS` plus `Reaver.<NAMESPACE>.<lower>`
   case-folding). Compiler.gls just declares them as externs; the
   bootstrap emits the right `(#pin (#law …))` entries.
2. **bytesBar codec.** Reaver's `Input N` returns the chunk as a nat
   with content packed little-endian and a marker bit at byte `len`.
   `Output x` writes `natBytes(x)`, which **drops the topmost byte**
   — so we must add a marker bit on output, strip it on input. The
   calc REPL has both halves; we lift the pattern.
3. **`read_all` loop.** `Input N` is single-syscall — for piped 300 KB
   source we MUST loop until `len == 0` (EOF) and concatenate chunks.
   The calc REPL's `repl_step` is the same shape.
4. **`main_reaver : Nat → Nat`.** Calls `read_all`, then `main`, then
   `Output`, sequenced through `BPLAN.seq` so the side effects fire.
5. **GLS prim registry update.** When the *self-hosted* compiler
   processes `Compiler.gls` and sees its own `external mod
   Reaver.BPLAN { … }` block, it must know how to compile each item.
   That means extending `cg_core_plan_bplan_prims` so
   `Reaver.BPLAN.add` etc. become BPLAN-primop pins on the self-host
   path, mirroring what `bplan_deps.py` already does on the bootstrap
   path.

That's ~80 lines of straightforward Gallowglass plus one table edit.
**It will work.** It will also be unrunnable on anything beyond a
50-byte fixture, for reasons in the next section.

## The hidden cost: Compiler.gls's internal arithmetic

The compiler's bottom layer is user-recursive code over `Core.PLAN.inc`:

```gls
let add  = λ m n → match n { | 0 → m | k → Core.PLAN.inc (add m k) }
let mul  = λ m n → match n { | 0 → 0 | k → add m (mul m k) }
let pow2 = λ n   → match n { | 0 → 1 | k → mul 2 (pow2 k) }
let shift_left  = λ n k → mul n (pow2 k)
let shift_right = λ n k → div_nat n (pow2 k)
```

Reaver runs user-defined recursion **faithfully** — there's no jet
registry yet (post-1.0), so it can't shortcut these to primops.
`pow2 240` reduces to ~2²⁴⁰ inc operations; `pow2 (8 × 30)` already
doesn't terminate in any practical time.

`bytes_concat` calls `shift_left b (8 × acc_len)`. Even the empty
source's emit phase exercises this — every `(#bind …)` line in the
output is appended via `bytes_concat`. So the I/O wrapper alone, on
top of the existing arithmetic, is a non-runner.

This is exactly why seven tests in
`tests/compiler/test_{utils,lexer,driver}.py` skip with reasons like
"uses bit_or/shift_left (recursive) — too slow."

## Resolution: BPLAN primop migration in the same PR

Two of the ten arithmetic primitives are already primops
(`Core.PLAN.inc`, `Core.PLAN.unpin` — registered in
`cg_core_plan_bplan_prims`). The other eight are user code. Replace
them by changing **bodies, not call sites**:

```gls
external mod Reaver.BPLAN {
  add : Nat → Nat → Nat
  sub : Nat → Nat → Nat
  mul : Nat → Nat → Nat
  div : Nat → Nat → Nat
  mod : Nat → Nat → Nat
  bex : Nat → Nat
  lsh : Nat → Nat → Nat
  rsh : Nat → Nat → Nat
  seq : Nat → Nat → Nat
}

let add         : Nat → Nat → Nat = λ m n → BPLAN.add m n
let sub         : Nat → Nat → Nat = λ m n → BPLAN.sub m n
let mul         : Nat → Nat → Nat = λ m n → BPLAN.mul m n
let div_nat     : Nat → Nat → Nat = λ a b → BPLAN.div a b
let mod_nat     : Nat → Nat → Nat = λ a b → BPLAN.mod a b
let pow2        : Nat → Nat       = λ n   → BPLAN.bex n
let shift_left  : Nat → Nat → Nat = λ x k → BPLAN.lsh x k
let shift_right : Nat → Nat → Nat = λ x k → BPLAN.rsh x k
```

Names stay; every call site (and there are many) is unaffected.

### Two correctness checks that must run before swapping

- **`bit_or`.** General `bit_or a b` for overlapping bits is computed
  correctly by the recursive impl. In Compiler.gls's call sites it
  is *believed to be* used only in `bytes_concat`-style patterns
  where one operand has been left-shifted past the other
  (non-overlapping). For non-overlapping bits `bit_or = +`. **Grep
  every call site to confirm.** If anything escapes that pattern,
  keep the recursive `bit_or` for general use and only replace the
  non-overlapping call sites with `BPLAN.add`.
- **`bit_and`.** Same story: only used as `bit_and x 255` (truncate
  to byte). For that pattern `bit_and x m = x mod (m+1)` when
  `m+1` is a power of 2. **Grep every call site to confirm.** If
  anything escapes, keep the recursive impl as a fallback.

## What's still slow even after the swap

`emit_program` builds the output via a **chain of `bytes_concat`** —
once per `(#bind …)` line. For a K-byte output that's roughly K
`BPLAN.add`s on bignums of growing size. `BPLAN.add` on K-byte
bignums is GHC-Integer O(K) time, so total is O(K²). For K ≈ 300 KB,
~9 × 10¹⁰ byte-ops; at typical GHC bignum throughput, **15 minutes**.
Runnable, but unpleasant.

The right fix is Reaver jet matching for `bytes_concat`'s body
(recognise the shape, dispatch to a single `Integer.shiftL+or` in
the runtime). **Out of scope for #2** — a Reaver-side change, not a
Gallowglass one.

For the smoke test we use a tiny fixture. For the byte-identity test
in #3 we either accept a slow CI run or scope the test fixture
small. If/when jet matching lands, the constraint disappears.

## PR composition

Single PR titled roughly **"Phase G #2: `main_reaver` I/O re-shape and
BPLAN primop migration"**:

1. `external mod Reaver.BPLAN { … }` and `external mod Reaver.RPLAN { … }`
   declarations in `compiler/src/Compiler.gls`.
2. Rewrite the eight arithmetic/bit-op bodies to BPLAN primops.
   Names unchanged.
3. `decode_input : Nat → Pair Nat Nat` — bytesBar decoder.
4. `encode_output : Pair Nat Nat → Nat` — bytesBar encoder (adds the
   marker bit).
5. `read_all_loop` / `read_all` — drain stdin until EOF, concatenating.
6. `main_reaver : Nat → Nat` — the wired entry point.
7. Extend `cg_core_plan_bplan_prims` so the self-hosted compiler can
   compile programs that use `Reaver.BPLAN.*` and `Reaver.RPLAN.*`
   externs.
8. **Smoke test** in `tests/reaver/test_selfhost.py`: bootstrap-compile
   the new Compiler.gls, run under Reaver on a ~50-byte fixture (e.g.
   `let n : Nat = 42`), byte-identity check vs
   `bootstrap.emit_pla.emit_program` over the same input.
9. Lift one or two of the seven deferred GLS skips that become cheap
   enough to run as standalone Reaver tests, as an empirical proof
   the migration buys the speed it claims.

## Out of scope / deferred to subsequent Phase G PRs

- **Self-host on Compiler.gls itself** (the deepest test) — needs
  the `bytes_concat` scaling fix or accepts a 15+ minute CI run.
  Tracked under Phase G #3.
- **CI gate** for the self-host test — Phase G #4.
- **The remaining deferred GLS skips** that don't trivially lift.
- **Any Reaver-side jet-matching work.** Post-1.0.

## Pre-flight checks (do these before writing any code)

1. **Grep `bit_or` and `bit_and` call sites in Compiler.gls.** Confirm
   the non-overlap and byte-mask assumptions above. If either escapes,
   the swap-by-body strategy still works but we keep the recursive
   bodies as fallbacks under different names.
2. **Empty-source smoke test against the *existing* Compiler.gls.**
   Bootstrap-compile it, drive the pure `main` from a Plan Asm
   wrapper that constructs `MkPair 0 0`, run under Reaver, observe
   either (a) it terminates with empty/preamble output, in which case
   the I/O re-shape is purely additive; or (b) it hangs/crashes for
   reasons unrelated to arithmetic, which is a blocker to surface
   before scoping #2. Even if it's slow, that just confirms the
   arithmetic story.

If both pre-flight checks come out clean, the PR scope above is
correct. If either turns up surprises, this plan gets edited.

## Pre-flight results (run 2026-05-05)

1. **`bit_or` / `bit_and` grep — clean.** Every `bit_or` call site
   outside its own recursive body is `bit_or X (shift_left Y N)` —
   strictly non-overlapping bits. Every `bit_and` call site is
   `bit_and x M` for `M ∈ {255, 0xFF, 2^64 − 1}` — power-of-2 mask.
   The swap-by-body strategy in §"Resolution" works as written.
2. **Empty-source smoke — fails on a separate codegen bug, AUDIT.md
   D8.** Bootstrap-compiling Compiler.gls and feeding it `MkPair 0 0`
   under Reaver crashes at `Compiler_lex_skip_ws` with `law: unbound:
   "_3"`. Reduced to a 14-line reproducer; the issue is
   `_compile_local_let` emitting `(1 rhs body)` for in-law lets
   regardless of position, but Reaver's `lawExp` parser only accepts
   that bind form at the law's body top. **D8 is a hard blocker.**

## Revised plan

Phase G #2 is now a two-PR sequence:

### PR 1 (D8 fix) — **prerequisite, separate arc**

Lambda-lift nested in-law lets at codegen time. `_compile_local_let`
gains a "is this let at the law body's top?" check; lets that fail
the check compile as `App(Pin(SubLaw), captures…, rhs)` instead of
`A(A(N(1), rhs), body)`. SubLaw uses the same capture-and-partial-
apply machinery as `_build_field_arm_law` and
`_build_wild_app_handler`. Top-of-law lets keep the existing
`(1 rhs body)` form so the Reaver-native bind shape stays in use
where it works.

Tests:

- `tests/bootstrap/test_codegen.py::test_d8_*` — pin the
  lambda-lifted shape for nested lets, the unchanged shape for
  top-of-law lets.
- `tests/reaver/test_differential.py::test_d8_let_in_arm_runs` —
  the 14-line reproducer, run to a known result on both runtimes.
- Reproducer fixture preserved at
  `tests/reaver/fixtures/repro_d8_let_in_arm.gls`.

Acceptance: existing tests stay green; the 14-line reproducer
runs to `16` on Reaver (where `go 5 10` evaluates to `(5 + 10) + 1
= 16`); bootstrap-compiled Compiler.gls round-trips through
Reaver-parse without `unbound` errors. (Whether it then *runs* in
useful time is the arithmetic-migration question, addressed in
PR 2.)

### PR 2 — Phase G #2 proper (this plan)

Scope as written above (Reaver.BPLAN/RPLAN externs, arithmetic
migration, bytesBar codec, read_all loop, `main_reaver`,
`cg_core_plan_bplan_prims` extension, smoke test). Builds on PR 1.

## Estimated effort

PR 1: 1–2 days. PR 2: 3–5 days. Total: ~1 week of focused work.
