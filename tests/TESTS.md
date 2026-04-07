# Test Strategy

**Last updated:** 2026-04-07 — M13.4 GLS compiler parity (1008 tests: 907 pass, 101 skip)

This document describes the test architecture, what each layer verifies,
and the known gap between what is tested and what is not.

---

## Test Layers

### Layer 1a: Python harness (semantic correctness)

**Tool:** `dev/harness/plan.py` — a Python implementation of PLAN semantics.
**Runs:** Always; no external dependencies.
**Gate:** `make test` / `make test-harness`

The harness evaluates compiled PLAN values using Python. Tests at this layer
compile a Gallowglass snippet, evaluate it against arguments, and assert the
result.

This is the **primary correctness gate**. It catches semantic bugs in the
codegen — wrong de Bruijn indices, incorrect Case_ dispatch, broken lambda
lifting, etc.

**Coverage:**
- `tests/sanity/test_plan.py` — opcodes 0–4, law evaluation, Case_ dispatch
- `tests/sanity/test_seed.py` — seed format round-trip
- `tests/bootstrap/test_*.py` — lexer, parser, scope, typecheck, codegen (462 tests)
- `tests/bootstrap/test_programs.py` — integration battery: Fibonacci (self-recursive + fix), Ackermann, Sudan, even/odd (20 tests)
- `tests/bootstrap/test_typeclasses.py` — M11 typeclass: DeclClass/DeclInst compilation, constrained let arity, call-site dict insertion, multi-method, prelude Eq/Ord/Add instance evaluation (27 tests)
- `tests/bootstrap/test_data_csv.py` — M12.5 Data.Csv effect handler integration (9 tests)
- `tests/bootstrap/test_modules.py` — M12 multi-module build, cross-module instances (18 tests)

**Limitation:** The harness is our own implementation of PLAN semantics. If the
harness and the real planvm disagree on semantics (evaluation order, edge cases),
the harness tests pass but planvm execution fails. This is the evaluation gap.

---

### Layer 1b: BPLAN harness (arithmetic-jetted correctness)

**Tool:** `dev/harness/bplan.py` — PLAN evaluator extended with native jets.
**Runs:** Always; no external dependencies.
**Gate:** same as Layer 1a (`make test`)

The BPLAN harness adds a jet registry: when a pinned Law's id matches a registered
jet, `bevaluate` calls the native Python implementation instead of interpreting the
Law body. This makes O(n) recursive arithmetic (add, mul, bit_or, shift_left, …)
run in O(1), eliminating the Python recursion-depth limit that previously blocked
~24 compiler emitter tests.

**Jets (Compiler.*):** `add`, `sub`, `mul`, `div_nat`, `mod_nat`, `pow2`, `bit_or`, `bit_and`,
`shift_left`, `shift_right`, `nat_eq`, `nat_lt`, `lte`, `gte`, `max_nat`, `min_nat`,
`emit_bind`, `emit_program` (Plan Assembler emitter — avoids O(n²) bigint accumulation).

**Jets (Core.Nat.* / Core.Text.*):** `Core.Nat.{add,mul,pred,is_zero,nat_eq,nat_lt}`,
`Core.Text.{sub,pow2,div_nat,mod_nat}`, `Core.Text.Prim.{mk_text,text_len,text_nat}`.
Registered via `register_prelude_jets(compiled_dict)` in prelude test suites.

**Coverage:**
- `tests/compiler/test_emit.py` — all 39 M8.6 emitter tests (was: 15 active, 24 skipped)

**Relation to real BPLAN:** The jet registry is indexed by Python object identity
(`id(L_object)`) rather than content hash. This is equivalent to BPLAN semantics
for testing purposes because jet functions are provably correct (simple Python
arithmetic). Correctness is still gated by M8.8 self-hosting validation.

---

### Layer 2: planvm seed loading (format validity)

**Tool:** `planvm` binary in Docker (xocore-tech/PLAN `x/plan`).
**Runs:** CI only (planvm is x86_64-only; cannot run on Apple Silicon via Docker).
**Gate:** `make test-ci` (CI), or `PLANVM=planvm pytest tests/` (native x86_64)

`seed_loads(seed_bytes)` invokes `planvm <seed_file>` and checks that the seed
is accepted without a format/parse crash. It distinguishes:

- Format error (bad seed → planvm exits with error marker in stderr) → **False**
- Runtime failure or timeout (seed loaded; cog failed or waiting for I/O) → **True**
- Signal / crash → **False**

A `True` result means: the seed format is correct and planvm parsed it without
error. It does **not** mean the compiled function produces correct results.

**Coverage:**
- `tests/planvm/test_seed_planvm.py` — 7 seed format tests
- `tests/prelude/test_core_*.py` — all prelude definitions (56 tests)
- `tests/compiler/test_selfhost.py` — compiler seed loading + Path A (5 tests)

---

### Layer 3: planvm evaluation (functional correctness)

**Tool:** `planvm` binary — `eval_seed()` runs planvm, checks exit code = result Nat.
**Runs:** CI only (planvm is x86_64-only).
**Gate:** `PLANVM=planvm pytest tests/planvm/test_eval_planvm.py`

planvm behavior: forces the seed value, casts result to Nat, exits with it as the
process exit code. For pure Nat values 0–255, the exit code directly gives the
evaluated result.

`eval_seed(seed_bytes)` invokes planvm and returns the exit code as the result.
Tests compile small Gallowglass programs, emit seeds, and assert planvm produces
the correct Nat.

**Coverage (21 tests):**
- Nat literals (0, 42, 255)
- Lambda application (identity, const, nested)
- Pattern matching (nat match, constructor match)
- If/then/else
- Arithmetic (Core.PLAN.inc)
- Recursion (fix-based)
- Effect handlers (pure/run, handle return/op/resume, do-bind)
- Constructor field extraction (Option, Result)

**Limitation:** Exit codes are 0–255 (8-bit). Values > 255 require a WriteOp
wrapper seed to write the result to stdout. Current tests use small Nats.

---

## Running Tests

```bash
# Layer 1: Python harness (always available, no planvm)
make test                    # harness + bootstrap + demos
python3 -m pytest tests/ -q  # all tests (planvm-gated skip automatically)

# Layer 2+3: planvm validation (CI or native x86_64 only)
# On CI (GitHub Actions ubuntu x86_64):
PLANVM=planvm python3 -m pytest tests/ -v --tb=short

# Note: planvm cannot run on Apple Silicon via Docker (x86_64 assembly
# requires native hardware, not QEMU/Rosetta emulation).

# Individual targets:
make test-planvm-docker      # seed format validation
make test-eval-docker        # evaluation correctness
make test-prelude-docker     # prelude seed validation
make test-selfhost-docker    # self-hosting + Path A
make test-compiler-docker    # all compiler tests
```

---

## Test File Organization

```
tests/
  TESTS.md                     ← this file
  __init__.py
  sanity/
    __init__.py
    test_plan.py               ← harness opcode/semantics tests (21 tests)
    test_seed.py               ← seed format round-trip tests
  bootstrap/
    __init__.py
    test_lexer.py              ← lexer token output
    test_parser.py             ← AST construction
    test_scope.py              ← name resolution
    test_typecheck.py          ← restricted HM
    test_codegen.py            ← codegen PLAN value output (44 tests)
    test_bootstrap.py          ← integration: source → seed
    test_programs.py           ← Fibonacci, Ackermann, Sudan (20 tests)
    test_typeclasses.py        ← M11 typeclasses (27 tests)
    test_coverage_gaps.py      ← edge cases, superclass constraints
    test_modules.py            ← M12 multi-module build (18 tests)
    test_data_csv.py           ← M12.5 Data.Csv E2E effects (9 tests)
  planvm/
    __init__.py
    test_seed_planvm.py        ← Layer 2: seed loading (7 tests; skips locally)
    test_eval_planvm.py        ← Layer 3: evaluation correctness (21 tests; skips locally)
  prelude/
    __init__.py
    test_core_combinators.py   ← 5 definitions
    test_core_bool.py          ← 8 definitions
    test_core_nat.py           ← 11 definitions
    test_core_option.py        ← 7 definitions
    test_core_list.py          ← 11 definitions
    test_core_text.py          ← 44 harness (bplan jets) + 15 planvm seed tests
  compiler/
    __init__.py
    test_utils.py              ← M8.1 utilities; 45 tests + planvm seeds
    test_lexer.py              ← M8.2 GLS lexer tests
    test_scope.py              ← M8.4 GLS scope tests
    test_emit.py               ← M8.6 Plan Assembler emitter; 39 active (BPLAN)
    test_driver.py             ← M8.7 driver; 3 active + 3 skipped
    test_selfhost.py           ← M8.8 self-hosting; 17 active + 5 planvm-gated
    test_m11.py                ← M11.5 GLS DeclClass/DeclInst (20 tests)
    test_m12_effects.py        ← M12.2/M12.4/M13.4 GLS effects + DeclUse + open-CPS (30 tests)
  demos/
    __init__.py
    test_calculator.py         ← Calculator demo: compile + arithmetic eval (9 tests)
    test_csv_table.py          ← CSV table demo: E2E data pipeline eval (10 tests)
```

---

## Known Issue: planvm SIGILL on CI (2026-04-07)

The planvm binary built via `nix develop --command make all` crashes with `Illegal
instruction (core dumped)` on GitHub Actions runners. All ~89 planvm-gated tests
skip silently. The CI now has a "Verify planvm runs" step that fails the job early
if the binary crashes, rather than reporting a misleading green result.

See `DECISIONS.md` § "planvm SIGILL on GitHub Actions runners" for details and
upstream fix plan (xocore-tech/PLAN issue).

---

## Known Gap: Evaluation vs Loading

| What we test | What we don't test |
|---|---|
| Seed format is valid (Layer 2) | Large Nat results (> 255) on planvm |
| Computation produces correct result for small Nats (Layer 3) | Harness ↔ planvm agreement for all edge cases |
| Python harness semantics (Layer 1) | planvm-specific behavior (GC, persistence) |

**Layer 3 closes the primary evaluation gap.** Previously, a bug producing the
wrong answer (e.g., `nat_eq 3 3 = 0` instead of `1`) would pass if the seed format
remained valid. Now, 21 eval tests verify actual computation on planvm.

**Remaining gap:** Values > 255 cannot be tested via exit code. A WriteOp-based
eval wrapper (compile value, convert to decimal, write to stdout) would extend
coverage to arbitrary Nats. This is tracked for future work.

**Mitigation:** M8.8 self-hosting (Path A) validates byte-identical compiler output
between Python bootstrap and planvm execution. This is a comprehensive functional
equivalence check for the full compiler workload.
