# Demos

Small Gallowglass programs that exercise the bootstrap compiler. Each demo is a
single `.gls` file plus a Python test in `tests/demos/` that compiles, evaluates
in the Python harness, and asserts expected output.

| Demo | Lines | Exercises |
|---|---|---|
| `calculator.gls` | ~85 | algebraic data types, structural recursion, Option |
| `csv_table.gls`  | ~157 | nested ADTs, list folds, Option, indexed access |

## Running a single demo

```bash
python3 -m pytest tests/demos/test_csv_table.py -v
```

## Running every demo

```bash
make test-demos
```

## What works in the bootstrap dialect

The bootstrap compiler accepts a strict subset of Gallowglass. Below is the
list of patterns that are safe to use in demo code today. The full restricted-
dialect specification is in `bootstrap/BOOTSTRAP.md` §2.

| Pattern | Status | Notes |
|---|---|---|
| `let f : T = λ aa → body` | ✅ | Top-level and local; recursive |
| `match` on Nat literals | ✅ | `\| 0 → ... \| 1 → ... \| _ → ...` |
| `match` on nullary or single-constructor types | ✅ | |
| `match` on multi-constructor sum types (same arity) | ✅ | e.g. `\| Ok x → ... \| Err y → ...` |
| `match` on multi-constructor sum types (mixed arity) | ⚠️ | Use the tagged-record idiom (§2.4.1 of `BOOTSTRAP.md`) |
| `if c then a else b` | ✅ | Both branches are deferred until c evaluates |
| Wildcard succ arm `\| _ → ...` | ✅ | Captures outer locals and self-ref correctly |
| PatVar succ arm `\| _kk → ...` | ✅ | Same as PatWild plus binds predecessor |
| `fix λ self args → body` | ✅ | |
| `eff` declarations + `handle` + do-notation | ✅ | CPS-compiled |
| `pure v` | ✅ | Terminates a do-chain |
| `external mod Core.PLAN { ... }` | ✅ | VM boundary; only `pin`, `mk_law`, `inc`, `reflect`, `force` are real opcodes |
| Tuples `(a, b)` | ✅ | Binary only |
| Mutual recursion | ✅ | Lexicographic SCC ordering |
| Single-letter snake_case identifiers | ❌ | Treated as type variables; use 2+ chars (`aa`, `ff`) |
| `use Mod` from a demo | ❌ | M12 supports it, but the demo harness compiles each demo with `module_env={}` (planned: F4) |

## Recursion-limit guidance

The Python harness evaluator is recursive; deep PLAN evaluation can hit Python's
default recursion limit. As a rule of thumb:

- Default Python limit is 1000. Almost any demo will exceed this.
- Demos touching list operations over more than ~100 cells need `sys.setrecursionlimit(100_000)` or higher.
- Demos with three or more nested folds over moderate-sized lists may need `200_000`.
- The real fix is jets in `dev/harness/bplan.py` for `length`, `map`, `foldl`, `foldr`, `append`, `concat_list` — planned (F5).

`tests/demos/test_calculator.py` and `tests/demos/test_csv_table.py` show the
typical pattern: bump `sys.setrecursionlimit` before evaluating, and don't
worry about it.

## What demos cannot yet do

- **Use the prelude.** Every demo today re-defines `length`, `map`, `foldl`,
  `foldr`, `append`, etc. inline. M12's module system supports cross-module
  imports, but the demo harness has not been wired through `build_modules`
  (planned: F4).
- **String I/O.** Text/Bytes are constructible but no I/O effect is exposed
  to user code in the harness yet.
- **Read from stdin.** Demos take their inputs from hardcoded `let` bindings.

## Reading existing demos

`Compiler.gls` (the self-hosting compiler) is the largest worked example of
the bootstrap dialect — ~3000 lines covering every restricted-dialect pattern.
When in doubt about how to write a feature, search there first.
