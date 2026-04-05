# Test Strategy

**Last updated:** M12.1 — cross-module typeclass instances (739 tests)

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

**Jets:** `add`, `sub`, `mul`, `div_nat`, `mod_nat`, `pow2`, `bit_or`, `bit_and`,
`shift_left`, `shift_right`, `nat_eq`, `nat_lt`, `lte`, `gte`, `max_nat`, `min_nat`.

**Coverage:**
- `tests/compiler/test_emit.py` — all 39 M8.6 emitter tests (was: 15 active, 24 skipped)

**Relation to real BPLAN:** The jet registry is indexed by Python object identity
(`id(L_object)`) rather than content hash. This is equivalent to BPLAN semantics
for testing purposes because jet functions are provably correct (simple Python
arithmetic). Correctness is still gated by M8.8 self-hosting validation.

---

### Layer 2: planvm seed loading (format validity)

**Tool:** `planvm` binary in Docker (xocore-tech/PLAN `x/plan`).
**Runs:** In Docker CI only; requires `make docker-build` first.
**Gate:** `make test-ci`

`seed_loads(seed_bytes)` invokes `planvm <seed_file>` and checks that the seed
is accepted without a format/parse crash. It distinguishes:

- Format error (bad seed → planvm exits with error marker in stderr) → **False**
- Runtime failure or timeout (seed loaded; cog failed or waiting for I/O) → **True**
- Signal / crash → **False**

A `True` result means: the seed format is correct and planvm parsed it without
error. It does **not** mean the compiled function produces correct results.

**Coverage:**
- `tests/planvm/test_seed_planvm.py` — 7 seed format tests
- `tests/prelude/test_core_*.py` — all 36 prelude definitions

---

### Layer 3: planvm evaluation (functional correctness) ← NOT YET IMPLEMENTED

**Tool:** Reaver CLI (`sol-plunder/reaver`) — not yet available.
**Planned gate:** `make test-eval`

This layer will apply compiled seeds to arguments and assert outputs:
```
assert planvm_eval(Core.Nat.add, [2, 3]) == 5
assert planvm_eval(Core.List.foldl, [add_fn, 0, [1,2,3]]) == 6
```

Until Reaver provides a CLI eval mode, this layer is replaced by:

**Functional equivalence via self-hosting (Milestone 8.8):** If the Gallowglass
compiler compiles itself and produces byte-identical seeds to the Python compiler
over the same inputs, the planvm execution is functionally correct for the full
compiler workload. This is a much stronger test than seed loading.

---

## Running Tests

```bash
# Layer 1: Python harness (always available)
make test

# Layer 2: planvm seed loading (requires Docker)
make test-ci           # full CI: harness + planvm seed loading
make test-planvm-docker  # planvm seed loading only
make test-prelude-docker # prelude seed loading only

# Run a specific test file
python tests/bootstrap/test_codegen.py
python tests/prelude/test_core_nat.py  # skips if planvm not available
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
  planvm/
    __init__.py
    test_seed_planvm.py        ← planvm seed loading (7 tests; skips locally)
  prelude/
    __init__.py
    test_core_combinators.py   ← 5 definitions
    test_core_bool.py          ← 6 definitions
    test_core_nat.py           ← 7 definitions (pred, is_zero, nat_eq, nat_lt, add, mul)
    test_core_option.py        ← 7 definitions
    test_core_list.py          ← 11 definitions
  compiler/                    ← Milestone 8
    __init__.py
    test_utils.py              ← M8.1 utilities (nat/list/byte ops); 45 tests + planvm seeds
    test_emit.py               ← M8.6 Plan Assembler emitter; 39 active (BPLAN harness)
    test_driver.py             ← M8.7 driver (main : Bytes → Bytes); 3 active + 3 skipped
    test_selfhost.py           ← M8.8 self-hosting; 17 active + 2 planvm-gated
    test_m11.py                ← M11.5 GLS DeclClass/DeclInst support; 20 tests
```

---

## Known Gap: Evaluation vs Loading

| What we test | What we don't test |
|---|---|
| Seed format is valid | Computation produces correct result |
| planvm accepts the seed | `pred 3 = 2` in planvm |
| Python harness semantics | Harness ↔ planvm agreement |

The harness has been validated empirically (it produced the same results as
expected from PLAN semantics), and CI confirms seeds load. But a bug that
produces the wrong answer for, say, `nat_eq` would pass all current tests if
the seed format remains valid.

**Mitigation:** Milestone 8 codegen tests compare PLAN values produced by
the Gallowglass compiler against Python bootstrap output for each prelude
definition. Byte-identical output = semantic equivalence under the same runtime.
