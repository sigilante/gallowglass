# Phase I — Toward 1.0.0-rc3

**Status:** in progress. Successor to Phase H.
**Roadmap reference:** `ROADMAP.md § "1.0"` (acceptance criteria).

## What rc3 needs (from the three deliverables in the post-Phase-H plan)

1. **Drop Python from the build path.** The Reaver-hosted self-host must be
   able to produce a fresh `Compiler.plan` from `Compiler.gls` without
   invoking the Python bootstrap.  Python becomes a verification tool, not a
   build dependency.
2. **Language-coverage parity.**  Every feature the bootstrap supports must
   either pass byte-identity through the self-host or have a tracked,
   accepted gap.
3. **Full Gallowglass surface.**  The features listed in
   `bootstrap/BOOTSTRAP.md §2.2` ("Excluded — deferred to self-hosting
   compiler") must compile via the self-host (or be explicitly deferred to
   1.0.x with a roadmap entry).

## rc3-1 — Python-less build path  ✅

Landed.  Concretely:

* `compiler/dist/Compiler.plan` (1.23 MB) — Plan Asm text seed; the
  trust-rooted artifact produced by the Python bootstrap and verified
  byte-identical via the Phase H compile-self gate.
* `compiler/dist/MANIFEST.json` — BLAKE3 of both the seed and the
  `Compiler.gls` source it was built from.
* `tools/build-self-host.sh` — copies seed + boot.plan into a scratch dir
  and invokes Reaver's `plan-assembler` on any Gallowglass source.  No
  Python.
* `tests/sanity/test_seed_compiler_plan.py` — four assertions catching seed
  drift at unit-test time (no Reaver needed).
* `.github/workflows/ci.yml::self_host_build` — CI job that runs two smoke
  builds (trivial Nat literal + small recursive program) through the build
  script with no Python in the build path.

Re-bootstrap procedure (when `Compiler.gls` legitimately changes):

```
# 1. Regenerate the seed via Python.
python3 -c "
  import sys; sys.setrecursionlimit(200000)
  from bootstrap.lexer import lex
  from bootstrap.parser import parse
  from bootstrap.scope import resolve
  from bootstrap.codegen import compile_program
  from bootstrap.emit_pla import emit_program
  src = open('compiler/src/Compiler.gls').read()
  prog = parse(lex(src, 'Compiler.gls'), 'Compiler.gls')
  resolved, _ = resolve(prog, 'Compiler', {}, 'Compiler.gls')
  open('compiler/dist/Compiler.plan', 'w').write(
    emit_program(compile_program(resolved, 'Compiler')))"

# 2. Verify byte-identity (Python ≡ self-host).
GALLOWGLASS_RUN_COMPILE_SELF=1 \
  python3 -m pytest tests/reaver/test_selfhost.py::TestPhaseHFixedPoint -v

# 3. Update MANIFEST.json (BLAKE3 of new seed + source).
# 4. Commit.
```

## rc3-2 — Coverage-parity fixtures  ✅

Landed.  `tests/reaver/test_selfhost.py` now has the following Phase I
coverage fixtures:

| Fixture | Pass? | Bootstrap milestone | Self-host gap |
|---|---|---|---|
| `test_fix_lambda_anonymous_recursion` | ✅ | M9.1 (`fix`) | — |
| `test_or_pattern_constructor` | xfail | M15.4 (or-patterns) | pure-nullary type + wild routes through wild_app_handler (Task H limit); fix needs ctab `has_field_sib` flag |
| `test_or_pattern_nat` | xfail | M15.4 | self-host's nat or-pattern arm-flattening differs |
| `test_list_literal_empty` | ✅ | M15.3 (`[]`/`::`) | — |
| `test_list_literal_three` | xfail | M15.3 | multi-element list desugar in self-host differs |
| `test_list_cons_pattern` | ✅ | M15.3 | — |
| `test_guard_pattern` | xfail | M15.5 (guards) | self-host guard-desugar (`| pat | guard → body` → `if guard then body else …`) not byte-identical |
| `test_record_construct` | xfail | M15.1 (records) | self-host has no record codegen — emits `PNat 0` for type decl, constructor, and use |
| `test_record_pattern` | xfail | M15.1 | same |
| `test_typeclass_simple` | xfail | M11 (typeclasses) | self-host does not desugar `class`/`instance` into dictionary-passing |
| `test_do_notation_simple` | xfail | M10 (effect handlers) | self-host's CPS codegen for `eff`/`handle`/`do` does not match bootstrap byte-for-byte |

Plus all existing Phase H/G fixtures continue to pass: **30 passed, 1
skipped (compile-self gate, env-gated), 9 xfailed.**

## rc3-3 — Full Gallowglass surface  (in progress)

Each xfailed fixture above represents a distinct self-host codegen gap.
Closing all of them is multi-week work — each is its own port of the
bootstrap's logic into `Compiler.gls`.  Realistic 1.0 scoping options:

### Option A — close all gaps before rc3

Estimated effort (each ~3-5 days of focused work, in priority order):

1. **`has_field_sib` flag in ctab** (smallest, ~1 day) — unblocks
   or-patterns on pure-nullary types AND any future no-field-arms+wild
   match without leaking wild_app_handler.  Extending the ctab value
   from `Pair Nat Nat` to `Pair Nat (Pair Nat Nat)` touches ~88
   signature occurrences but is mechanical.
2. **Multi-element list literal desugar** — port the bootstrap's
   `[a, b, c]` → `Cons a (Cons b (Cons c Nil))` shape into the self-host
   parser.
3. **Guard desugar** — port `_desugar_guarded_match` into the self-host
   scope/post-parse pass.
4. **Records** — biggest individual feature: field projection, update,
   pattern, anonymous record constructor.  Port `bootstrap/codegen.py`'s
   record handling into `Compiler.gls`.
5. **Typeclasses** — `DeclClass` and `DeclInst` codegen.  Substantial.
6. **Effect handlers / do-notation** — port CPS transform.  Touches
   `cg_compile_handle`, `cg_compile_do`, `pure`/`run` desugaring.

Total: 3-4 weeks of focused effort.

### Option B — defer non-essentials to 1.0.x, ship rc3 with documented gaps

Mark each gap explicitly in `ROADMAP.md` under "1.0.x follow-ups", ship
rc3 with the current xfails as known limitations, gate 1.0 final on
closing them.

The case for Option B: Phase H's compile-self gate is the *hard*
correctness property.  Phase I gaps are language-coverage features where
the bootstrap path already works — users authoring Gallowglass programs
that use these features can still compile them via the bootstrap.
Self-host parity is needed eventually but not for rc3 to be a meaningful
release artifact.

The case for Option A: a release labeled "1.0.0-rc3" implies parity with
the bootstrap.  Shipping with known coverage gaps is misleading.

**Recommendation:** Option B for rc3, with Option A's tasks scheduled as
rc4/1.0-final blockers.  rc3 demonstrates the Python-less build path
plus a mapped gap landscape; rc4 closes the gaps.

## Open questions for rc3 / 1.0

* **Should `compiler/dist/Compiler.plan` be committed in git** (1.23 MB
  text), fetched via vendor.lock, or generated on demand?  Current
  approach: committed.  Trade-off: PR diffs bloat on every Compiler.gls
  change.  Alternative: commit only `MANIFEST.json` and fetch the .plan
  via a release artifact.
* **Trust root.**  The seed `Compiler.plan` came from Python.  Long-term,
  we want it to come from "a previous version of itself".  Capturing
  this provenance properly (e.g. in `MANIFEST.json::produced_by`) lets
  future re-bootstraps either re-trace through Python (verifiable) or
  chain forward through old self-host versions (also verifiable, just
  via a different path).
