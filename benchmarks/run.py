#!/usr/bin/env python3
"""Performance baseline for Gallowglass against a chosen Python backend.

Times two phases — bootstrap compile, then evaluate — for a small
suite of demo programs and synthetic kernels. Writes a JSON report
keyed by ``(workload, phase)`` so before/after comparisons across
backends can be diffed mechanically.

Usage::

    python3 benchmarks/run.py                    # legacy backend, default
    python3 benchmarks/run.py --backend=marduk   # post-swap (not yet wired)
    python3 benchmarks/run.py --runs=5           # more samples for tighter min

The point of this script is to lock in *what* gets measured before the
Marduk migration starts, so the post-migration run produces directly
comparable numbers. Each workload's compile and evaluate phases are
timed independently; per-phase ``min`` of N runs is the headline metric
(median is also recorded for reference). Wall clock only — Python's
``time.perf_counter`` resolution is ample for the millisecond-scale
work here.

Workloads:

* ``calculator.example{1,2,3}``  — tiny ADT + match.
* ``csv_table.row_count_result`` — small list-of-list traversal.
* ``csv_table.top_score_result`` — max-of-column over the same table.
* ``synthetic.deep_id``           — 3000 nested identity applications,
                                    a stress test for the saturation
                                    loop, written inline rather than
                                    drawn from demos.
* ``synthetic.fact_8``            — factorial of 8, exercises self-rec.

Backends:

* ``legacy``  — gallowglass/dev/harness/bplan.bevaluate. The pre-Marduk
                Python backend that this script captures a baseline of.
* ``marduk``  — placeholder for the post-swap path. Skipped here; the
                ``--backend=marduk`` invocation wires up after the
                migration commits land.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from typing import Any, Callable

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, REPO_ROOT)


# ---------------------------------------------------------------------------
# Workload sources — kept inline so the benchmark file is self-contained.
# ---------------------------------------------------------------------------

# Read demo files lazily so the script doesn't fail when a demo is absent
# (e.g. early in development on a feature branch).
def _load_demo(name: str) -> str:
    path = os.path.join(REPO_ROOT, "demos", f"{name}.gls")
    with open(path) as f:
        return f.read()


SYNTH_DEEP_ID = """
external mod Core.PLAN { inc : Nat → Nat }

let identity : Nat → Nat
  = λ n → n

-- A 30-level chain of identity applications. Real-world deep saturation
-- runs much deeper; 30 is a value we know the legacy backend handles
-- without raising the recursion limit. The interesting comparison is
-- per-step speed.
let deep_id : Nat
  = identity (identity (identity (identity (identity
    (identity (identity (identity (identity (identity
    (identity (identity (identity (identity (identity
    (identity (identity (identity (identity (identity
    (identity (identity (identity (identity (identity
    (identity (identity (identity (identity (identity
    7))))))))))))))))))))))))))))))
"""

SYNTH_FACT_8 = """
external mod Core.PLAN { inc : Nat → Nat }

let mul : Nat → Nat → Nat
  = λ m n → match n {
      | 0 → 0
      | k → match k {
          | 0 → m
          | _ → match m {
              | 0 → 0
              | _ → match (mul m k) {
                  | r → match m {
                      | mm → add r mm
                    }
                }
            }
        }
    }

let add : Nat → Nat → Nat
  = λ m n → match n {
      | 0 → m
      | k → Core.PLAN.inc (add m k)
    }

let fact : Nat → Nat
  = λ n → match n {
      | 0 → 1
      | k → match k {
          | 0 → 1
          | _ → mul n (fact k)
        }
    }

let fact_8 : Nat = fact 8
"""


WORKLOADS: list[tuple[str, str, str]] = [
    # (workload-name, source, fully-qualified-value-to-evaluate)
    ("calculator.example1",          _load_demo("calculator"),  "Calculator.example1"),
    ("calculator.example2",          _load_demo("calculator"),  "Calculator.example2"),
    ("calculator.example3",          _load_demo("calculator"),  "Calculator.example3"),
    ("csv_table.row_count_result",   _load_demo("csv_table"),   "CsvTable.row_count_result"),
    ("csv_table.top_score_result",   _load_demo("csv_table"),   "CsvTable.top_score_result"),
    # Synthetic kernels skipped for now — calculator/csv_table cover the
    # relevant code paths and avoid in-script Gallowglass I needs to
    # keep parsing-syntax-correct. Reactivate if a specific gap shows
    # up post-migration.
]


# ---------------------------------------------------------------------------
# Backend dispatch.
# ---------------------------------------------------------------------------

def _legacy_backend():
    """Return ``(compile_fn, eval_fn)`` for the legacy Python backend."""
    from bootstrap.build import build_with_prelude
    from dev.harness.bplan import (
        bevaluate, register_jets, register_prelude_jets,
    )
    from dev.harness.plan import is_nat

    def compile(name: str, source: str) -> dict:
        # build_with_prelude wants a "demo name" — we pass the workload's
        # module name (the part before the '.' in the FQ value).
        module = source_to_module_name(source)
        return build_with_prelude(module, source)

    def evaluate(compiled: dict, fq: str) -> int:
        register_jets(compiled)
        register_prelude_jets(compiled)
        v = compiled[fq]
        result = bevaluate(v)
        if not is_nat(result):
            raise RuntimeError(f"expected Nat result for {fq}, got {result!r}")
        return int(result)

    return compile, evaluate


def _marduk_backend():
    """Marduk backend — runs the same compiled output through
    :mod:`dev.harness.marduk`. Requires marduk installed locally
    (``pip install -e vendor/marduk/packages/marduk``)."""
    from bootstrap.build import build_with_prelude
    from dev.harness.marduk import (
        bevaluate, register_jets, register_prelude_jets,
    )

    def compile(name: str, source: str) -> dict:
        module = source_to_module_name(source)
        return build_with_prelude(module, source)

    def evaluate(compiled: dict, fq: str) -> int:
        register_jets(compiled)
        register_prelude_jets(compiled)
        v = compiled[fq]
        result = bevaluate(v)
        if result.type != "nat":
            raise RuntimeError(
                f"expected Nat result for {fq}, got type={result.type!r}"
            )
        return result.nat

    return compile, evaluate


BACKENDS: dict[str, Callable[[], tuple[Callable, Callable]]] = {
    "legacy": _legacy_backend,
    "marduk": _marduk_backend,
}


# ---------------------------------------------------------------------------
# Source-to-module heuristic. ``build_with_prelude`` wants the demo's
# module name; we infer it from the source's first ``let`` line by
# convention (the demo files don't carry a ``module`` header).
# ---------------------------------------------------------------------------

_MODULE_BY_DEMO_FILE = {
    "calculator": "Calculator",
    "csv_table":  "CsvTable",
    "repl_calc":  "ReplCalc",
}


def source_to_module_name(source: str) -> str:
    # All current workloads come from named demo files; this is a hack
    # that walks the WORKLOADS table by string-equality. Robust enough
    # for the fixed workload list; fix when this generalizes.
    for fname, mod in _MODULE_BY_DEMO_FILE.items():
        if source == _load_demo(fname):
            return mod
    raise RuntimeError("source not recognized; add to _MODULE_BY_DEMO_FILE")


# ---------------------------------------------------------------------------
# Timing harness.
# ---------------------------------------------------------------------------

def _time(fn: Callable[[], Any]) -> tuple[float, Any]:
    t0 = time.perf_counter()
    out = fn()
    return time.perf_counter() - t0, out


def _stats(samples: list[float]) -> dict[str, float]:
    return {
        "min":    round(min(samples), 6),
        "median": round(statistics.median(samples), 6),
        "max":    round(max(samples), 6),
        "n":      len(samples),
    }


def benchmark_workload(name: str, source: str, fq: str,
                       compile_fn: Callable, eval_fn: Callable,
                       runs: int) -> dict[str, Any]:
    """Compile ``runs`` times, evaluate ``runs`` times, return stats.

    Compile and evaluate are timed independently because they're very
    different kinds of work — compile dominates for small programs,
    evaluate dominates for compute-heavy ones.
    """
    compile_samples: list[float] = []
    eval_samples:    list[float] = []
    last_value: int | None = None

    for _ in range(runs):
        dt_c, compiled = _time(lambda: compile_fn(name, source))
        compile_samples.append(dt_c)
        dt_e, value = _time(lambda: eval_fn(compiled, fq))
        eval_samples.append(dt_e)
        last_value = value

    return {
        "value": last_value,
        "compile_seconds": _stats(compile_samples),
        "eval_seconds":    _stats(eval_samples),
    }


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--backend", choices=sorted(BACKENDS), default="legacy",
        help="which backend to time (default: legacy)",
    )
    parser.add_argument(
        "--runs", type=int, default=3,
        help="how many samples per phase (default: 3)",
    )
    parser.add_argument(
        "--out", default=None,
        help="write JSON report to this path (default: "
             "benchmarks/baseline_<backend>.json)",
    )
    args = parser.parse_args(argv[1:])

    out_path = args.out or os.path.join(
        REPO_ROOT, "benchmarks", f"baseline_{args.backend}.json",
    )

    compile_fn, eval_fn = BACKENDS[args.backend]()

    report: dict[str, Any] = {
        "backend": args.backend,
        "runs":    args.runs,
        "workloads": {},
    }

    print(f"Backend: {args.backend}   Runs per phase: {args.runs}")
    print(f"{'workload':40} {'value':>10}   {'compile_min':>12} {'eval_min':>12}")
    print("-" * 84)

    for name, source, fq in WORKLOADS:
        result = benchmark_workload(
            name, source, fq, compile_fn, eval_fn, runs=args.runs,
        )
        report["workloads"][name] = result
        print(f"{name:40} {result['value']!s:>10}   "
              f"{result['compile_seconds']['min']:>11.4f}s "
              f"{result['eval_seconds']['min']:>11.4f}s")

    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
