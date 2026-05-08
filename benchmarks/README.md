# Gallowglass benchmarks

Time the bootstrap compile and runtime evaluate phases for a small
suite of demo programs. The intent is **before/after measurement
across backends** — capture a baseline now (legacy Python harness),
re-run after the Marduk migration, diff the JSON to see what moved.

## Running

```bash
python3 benchmarks/run.py                    # legacy backend (default)
python3 benchmarks/run.py --runs=5           # tighter min via more samples
python3 benchmarks/run.py --backend=marduk   # post-migration
python3 benchmarks/run.py --out=foo.json     # alternate output path
```

Output goes to `benchmarks/baseline_<backend>.json` by default; the
table-form summary also prints to stdout.

## Methodology

For each workload the script times:

* **compile** — `bootstrap.build.build_with_prelude(...)`. Loads all
  eight Core prelude modules + the demo, runs lex / parse / scope /
  codegen, returns the compiled `dict`.
* **evaluate** — fully forces the named value through the backend's
  evaluator, returns its `Nat` result.

Each phase runs N times (default 3); the JSON records `min`, `median`,
and `max`. Headline metric is `min` — least affected by GC pauses,
laptop background load, etc. Sample reproducibility is the goal, not
microbenchmark precision.

## Comparing backends

```bash
python3 benchmarks/run.py --backend=legacy --out=before.json
# ... migration commits land ...
python3 benchmarks/run.py --backend=marduk --out=after.json
python3 -c "
import json
b = json.load(open('before.json'))['workloads']
a = json.load(open('after.json'))['workloads']
for w in b:
    bc, be = b[w]['compile_seconds']['min'], b[w]['eval_seconds']['min']
    ac, ae = a[w]['compile_seconds']['min'], a[w]['eval_seconds']['min']
    print(f'{w:40} compile {bc:.4f}→{ac:.4f} ({ac/bc:.2f}x), '
          f'eval {be:.4f}→{ae:.4f} ({ae/be:.2f}x)')
"
```

A ratio of `1.00x` means equivalent; `<1` is faster, `>1` is slower.
The Marduk migration is **not expected to be faster** — Marduk is
correctness-first and trades some performance for the spec-faithful
core. The threshold for concern is roughly 5–10x slower on any single
workload; smaller gaps are fine for now and can be closed later via
jets.

## Workloads

The current set is small and demo-focused. Each is a Gallowglass
program that compiles + evaluates to a `Nat`:

| Workload                          | Value | What it exercises                                |
|-----------------------------------|-------|--------------------------------------------------|
| `calculator.example1`             | 14    | tiny ADT (`Expr`), `match`                       |
| `calculator.example2`             | 4     | same, with subtraction                           |
| `calculator.example3`             | 21    | nested, deeper match                             |
| `csv_table.row_count_result`      | 3     | `length` over a list of lists                    |
| `csv_table.top_score_result`      | 95    | column-max via `foldl`                           |

Add new workloads to the `WORKLOADS` table in `run.py`. Keep them
small enough to run in seconds — this is a regression detector, not
a performance suite.
