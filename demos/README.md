# Demos

Small Gallowglass programs that exercise the bootstrap compiler. Each demo is a
single `.gls` file plus a Python test in `tests/demos/` that compiles, evaluates
in the Python harness, and asserts expected output.

| Demo | Lines | Exercises |
|---|---|---|
| `calculator.gls` | ~85 | algebraic data types, structural recursion, Option |
| `csv_table.gls`  | ~110 | cross-module prelude `use`, Option, indexed access |
| `repl_calc.gls`  | ~280 | end-to-end Reaver process — `Reaver.RPLAN` stdio, `Reaver.BPLAN` jetted arithmetic, recursive REPL loop, lex/parse/eval of arithmetic with precedence and parens |

## Running a single demo

```bash
python3 -m pytest tests/demos/test_csv_table.py -v
```

## Running every demo

```bash
make test-demos
```

## Inspecting Glass IR for a demo

```bash
make demo-glass-ir ARGS=demos/csv_table.gls
```

Renders the demo's top-level declarations as Glass IR text on stdout, with
the full Core prelude type environment available so type annotations resolve.
Pass an explicit module name as the second positional arg if the file's
basename doesn't camel-case cleanly:

```bash
make demo-glass-ir ARGS="demos/foo.gls SomeModule"
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
| `use Mod` from a demo | ✅ | Compile via `bootstrap.build.build_with_prelude(name, src)` — see `tests/demos/test_prelude_use.py` |

## Recursion-limit guidance

The Python harness evaluator is recursive; deep PLAN evaluation can hit Python's
default recursion limit. As a rule of thumb:

- Default Python limit is 1000. Almost any demo will exceed this.
- Demos touching list operations over more than ~100 cells should call
  `dev.harness.bplan.register_prelude_jets(compiled)` to dispatch list ops
  to native Python implementations. With jets, the recursion-limit pressure
  is bounded by algorithmic depth instead of allocation depth.
- Without jets, demos with three or more nested folds may need
  `sys.setrecursionlimit(200_000)`.
- Jetted prelude ops: `Core.List.{map, foldl, foldr, filter, length, append, concat_list}`.

`tests/demos/test_calculator.py` and `tests/demos/test_csv_table.py` show the
typical pattern: bump `sys.setrecursionlimit` before evaluating, and don't
worry about it.

## Running a demo as a Reaver process

`repl_calc.gls` runs as a real Reaver process — it reads stdin, writes
stdout, and loops until EOF. The end-to-end test in
`tests/demos/test_repl_calc.py` shows the pattern; manually:

```bash
python3 -c "
import sys; sys.path.insert(0, '.'); sys.setrecursionlimit(50000)
from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program
from bootstrap.emit_pla import emit_program
src = open('demos/repl_calc.gls').read()
prog = parse(lex(src, 'repl_calc.gls'), 'repl_calc.gls')
resolved, _ = resolve(prog, 'Main', {}, 'repl_calc.gls')
print(emit_program(compile_program(resolved, 'Main')))
" > /tmp/demo/demo.plan
cp vendor/reaver/src/plan/boot.plan /tmp/demo/
echo "1+2*3" | (cd vendor/reaver && nix develop --command \
    cabal run -v0 plan-assembler -- /tmp/demo demo Main_main 0)
```

For programs that don't need a loop, see `tests/reaver/test_smoke.py`
for the simpler `Trace` driver pattern.

## What demos cannot yet do

- **Real-world I/O beyond stdin/stdout.** `Reaver.RPLAN` exposes
  `read_file`, `print`, `stamp`, `now`, `warn`, but most demos haven't
  exercised these. The bindings work; sample code is welcome.
- **Persistent state across REPL turns.** `repl_calc.gls` evaluates each
  line independently. Threading state would require either a do-notation
  effect handler or an accumulator threaded through the recursive loop.

## Using the prelude in a new demo

The bootstrap module system can compile a demo alongside the full Core
prelude, so you can drop the inlined `length` / `map` / `foldl` boilerplate
that older demos carry.

```python
# tests/demos/test_my_demo.py
from bootstrap.build import build_with_prelude
from dev.harness.plan import evaluate

src = open('demos/my_demo.gls').read()
compiled = build_with_prelude('MyDemo', src)
result = evaluate(compiled['MyDemo.result'])
```

In the demo source:

```gallowglass
use Core.List unqualified { List, Nil, Cons, foldl, map }
use Core.Nat  unqualified { add }

let total : Nat
  = foldl add 0 (Cons 10 (Cons 20 (Cons 30 Nil)))
```

Constructors must be explicitly named in the `unqualified { ... }` list —
they are not pulled in automatically with the type.

## Reading existing demos

`Compiler.gls` (the self-hosting compiler) is the largest worked example of
the bootstrap dialect — ~3000 lines covering every restricted-dialect pattern.
When in doubt about how to write a feature, search there first.
