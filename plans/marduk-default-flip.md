# Session prompt: finish the Marduk default flip

## Goal

Make the Marduk backend the default evaluator for gallowglass tests/CI. Default flip is currently a one-line change in `dev/harness/eval.py` (`"legacy"` → `"marduk"`), but flipping it today causes `tests/prelude/test_core_option.py::test_debug_some` and a handful of similar text-recursive tests to fail. Resolve the underlying issue, flip the default, run benchmarks, report.

## What's working already

The codegen, harness, and test-modernization work landed in PR #100 (commit `6b69d1d`). Read that PR's description first. Specifically:

- Codegen emits BPLAN-named `Pin`/`Law`/`Elim` (no direct `<1>`/`<2>` opcode pins). Both backends consume the same emit shape.
- `dev/harness/marduk.py` provides `bevaluate` / `register_jets` / `register_prelude_jets` mirroring the legacy bplan API.
- `dev/harness/eval.py` is the backend selector. `GALLOWGLASS_BACKEND=marduk` runs everything through Marduk today.
- Tests use Marduk-aligned accessor names (`.head` / `.tail` / `.item` / `.args` / `.type`); legacy `P`/`L`/`A` carry these as aliases.
- `benchmarks/run.py --backend=marduk` works; results in `benchmarks/baseline_marduk.json`.

## The actual blocker

`marduk.runtime.jets.register_jet(law, fn)` registers a native implementation against `id(law.box)`. The intent: when the evaluator reaches a saturated registered Law, dispatch native instead of walking the body.

`dev/harness/marduk.convert(legacy_val)` converts gallowglass codegen output into Marduk Vals. To make jet registration work across multiple `bevaluate` calls (i.e., to make registered jets reachable from converted graphs), `convert` would need a module-global identity cache so the same legacy Val maps to the same Marduk Val every time.

A first attempt at the global cache (commit before `6b69d1d`) **broke correctness**: Marduk's evaluator mutates `Val.box` in place (`Val.update`, the cyclic-update discipline that makes letrec / Y / fix work). Cached converted Vals become stale once any prior evaluation mutates them — second use of a cached Val sees post-mutation contents, not the original. Reverted to per-call cache; native prelude jets sit dormant.

So the tension is:

- **In-place mutation** is required by the spec's cyclic-update rule (`Eo` after `o#Xoo`), which is how Marduk supports letrec without a separate thunk allocation.
- **Identity-keyed registration** assumes Vals don't change between registration and dispatch.
- **Cross-call reuse of converted Vals** is what makes jets effective — without it, you'd register a jet, evaluate, get a fresh Val on the next call, and the jet wouldn't fire.

Pick one of these to give up.

## Option A — content-addressed registration

Replace `id(law.box)` with a content hash of the Law: `(name, arity, body_canonical)`. Construction-time canonicalization computes a hash; registration stores against the hash; dispatch hashes the saturated Law and looks up.

Pros: works regardless of Val mutation; survives re-conversion. Conceptually matches Reaver's pin discipline (Reaver content-addresses).

Cons: every Law-saturation pays a hash. For non-jetted Laws (the common case) this is overhead with no win. Probably need a fast structural skim — maybe hash only the Law's `name` nat + `arity` and let collisions register multiple jet candidates? Need to think about robustness.

Files to look at: `vendor/marduk/packages/marduk/marduk/runtime/jets.py` for the registry, `marduk/runtime/core.py:X` for the dispatch site, `marduk/asm/prelude.py` for how Marduk itself constructs wrapper Laws (good test material for hash collisions).

## Option B — eliminate in-place mutation

Restructure Marduk's evaluator so `Val.update` no longer mutates. Vals become immutable; cyclic structures use a separate thunk indirection that gets pointer-rewritten without mutating the cell's data slot.

This is the CEK-machine refactor I sketched in commit `0e5deda`'s discussion. Core change: `Val.box` becomes truly immutable; the in-place rewrite happens via a separate `Thunk` cell type that holds a pointer. Saturation step replaces the Thunk's contents (which is just a redirect), not the Val it currently points at.

Pros: clean separation of concerns; identity-keyed jets just work; Vals become hashable by structure; nothing else in the runtime needs to know.

Cons: real surgery on `core.py`. Every accessor (`head`, `tail`, `item`, etc.) needs to follow Thunk indirection. Spec rules (`E`, `X`, `B`, `L`, `R`) need re-derivation against the new shape. Test corpus needs to verify letrec / Y / fix still terminate.

Files to look at: `marduk/runtime/core.py` end-to-end. The spec at `vendor/reaver/doc/plan-spec.txt` is the correctness oracle (rules `Ho1 = o#Xoo; Eo` and `Line(1 v b) = Ev; Iie#Rnev; L(i+1)neb` are where the cyclic update happens — the `#` operator).

## My judgment, not binding

(A) is smaller and gets the flip done. (B) is the right end state for the runtime even apart from this fix — pointer-rewriting via Thunk indirection makes the Val class simpler and the spec correspondence cleaner. If the ergonomic of `(A)` proves brittle (collisions, spurious dispatches), (B) becomes inevitable.

Try (A) first. If it falls over within a few hours of work, abandon and do (B).

## Reproduce the failure

```bash
GALLOWGLASS_BACKEND=marduk python3 -m pytest \
    tests/prelude/test_core_option.py::TestCoreOptionHarness::test_debug_some -v
```

Expected: `AssertionError: False is not true` — the result has wrong byte structure, indicating either jets didn't fire (slow but should still produce correct output) or jets fired with stale state. Earlier in the session: hung; now: returns wrong shape after my no-op `register_prelude_jets`. Either way the user-visible test fails.

The dormant native jets are at `dev/harness/marduk.py::_PRELUDE_JETS_MARDUK`. They're correctly written and pass against direct invocation; they just can't be reliably registered.

## "Done" looks like

1. Choose A or B and implement.
2. Re-enable `register_prelude_jets` in `dev/harness/marduk.py`.
3. `GALLOWGLASS_BACKEND=marduk python3 -m pytest tests/bootstrap tests/prelude tests/compiler -q` is green (1299 passing, same skip/xfail counts as legacy).
4. Flip default in `dev/harness/eval.py:_BACKEND_NAME = ... "legacy"` → `... "marduk"`.
5. `python3 -m pytest tests/...` (no env var) green.
6. `python3 benchmarks/run.py --backend=marduk --runs=5`; commit `benchmarks/baseline_marduk.json` refresh.
7. Update PR #100 description with the reconciliation approach + new benchmark deltas.
8. Optional: retire `dev/harness/bplan.py` and `dev/harness/plan.py` once a couple weeks of CI runs prove Marduk-default is stable. Per the user's earlier instruction, **do not delete legacy in this session** — leave the seam working with `GALLOWGLASS_BACKEND=legacy` as the escape hatch.

## What not to do

- Don't reintroduce the global `_CONVERT_CACHE` without one of (A) or (B). It's correct in isolation but unsafe under Marduk's evaluator semantics.
- Don't try to bridge legacy bplan jets through `_wrap_legacy_jet` from `dev/harness/marduk.py`. That path was investigated and rejected — legacy jet bodies access `.fun` / `.arg` / call legacy `bevaluate`, which loops on Marduk Vals.
- Don't modify gallowglass codegen to emit different shapes for different backends. Single emit, two evaluators, same ABI is the architectural commitment.

## Pointers to context

- PR #100: <https://github.com/sigilante/gallowglass/pull/100>
- Relevant Marduk commit: `0e5deda` (Val.type caching) — the perf discussion mentions the mutation issue.
- Test that surfaces it: `tests/prelude/test_core_option.py::test_debug_some` (text-recursive case).
- Spec: `vendor/reaver/doc/plan-spec.txt` (rules `Ho1` and `Line` use the `#` cyclic-update).
- Original cyclic-update note from Sol: `vendor/death-to-the-corporation-old-plan.py` (270-line PoC; the `Val.update` discipline traces back here).
