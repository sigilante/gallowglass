# Audit and Remediation Tracker

A red-team audit of Gallowglass was conducted on 2026-05-01 across five lenses
(behavior, durability, overengineering, abstraction, doc transparency). This
document captures the findings and tracks remediation across working sessions.

Findings are listed in priority order. Tick a box when the work lands; link
the PR or commit. Add new findings under "Discovered later" rather than
renumbering — these IDs are referenced in commit messages.

## Headline

The architecture is honest and the gospel principles are kept (Show/Debug
split, effect rows explicit, BLAKE3-256 chosen deliberately, Reaver as
upstream truth). The active problems are: transitional debt from the
xocore→Reaver migration, a war-diary `bootstrap/codegen.py` whose function
names trace bug-discovery order rather than algorithm shape, and doc rot a
stranger hits in the first ten minutes. One previously-unreported codegen
blocker exists.

## Blockers

- [x] **A1. `bootstrap/codegen.py:2027` — outer locals dropped in mixed
      nullary/field dispatch.** When a type has ≥2 explicitly-named nullary
      constructors *and* ≥1 field-bearing constructor, secondary nullary
      arms were compiled with `pred_env = Env(globals=env.globals,
      arity=1)`, dropping `env.locals` and `self_ref_name`. Fixed by
      mirroring `make_succ_law`'s capture pattern: collect free vars across
      `remaining_nullary` bodies plus `wild_body` (and check self-ref
      usage), build a lifted law of arity `n_cap + 1`, partial-apply at
      the outer env's perspective. Pitfall now documented in CLAUDE.md
      "Bootstrap Codegen Pitfalls"; six regression tests `test_a1_*` in
      `tests/bootstrap/test_codegen.py` pin the fix shape (secondary-tag
      arm uses outer param; tag-0 arm; field arm; secondary-tag uses
      self-ref + outer; three-nullary chain; top-level no-regression).
      (PR: fix/codegen-mixed-nullary-locals)

- [x] **A2. CLAUDE.md "Build and Test" snippet does not run.** Used `module`
      where the parser keyword is `mod`, and referenced `sys.argv[1]` without
      importing `sys` or accepting an arg. Replaced with a working snippet
      that mirrors the form tests use. (PR: docs/audit-truth-up)

- [x] **A3. BLAKE3-256 silently falls back to SHA-256.** `bootstrap/pin.py:12`
      warns and continues if `blake3` isn't installed, despite CLAUDE.md
      asserting "no exceptions." No `requirements.txt` declared the dep. Added
      `requirements.txt`, softened the CLAUDE.md invariant to point at the
      install instruction, kept the soft fallback so contributors get a loud
      warning rather than an opaque ImportError. (PR: docs/audit-truth-up)

- [x] **A4. No byte-identity gate on self-host emission.** Added
      `TestSelfhostGolden` in `tests/compiler/test_selfhost.py` plus three
      checked-in golden files under `tests/compiler/golden/`: `snippet`
      (82 bytes — smallest drift signal), `curated` (1217 bytes —
      PNat/PApp/PLaw/PPin shapes), `mixed` (2903 bytes — mixed-arity
      Leaf/Node/Wrap, the codepath that hosted F11 and A1). Each test
      bytewise compares the live Path B output to the golden and on
      mismatch reports the divergence offset with surrounding context.
      Regenerate with `UPDATE_GOLDEN=1 python3 -m pytest
      tests/compiler/test_selfhost.py -k TestSelfhostGolden` and inspect
      `git diff tests/compiler/golden/` before committing. The full
      Compiler.gls Path B run takes minutes and is deferred to Phase G;
      these three small fixtures cover the same emit-layer logic in
      <0.1 s. (PR: test/selfhost-golden-snapshot)

## Sharp edges

- [x] **B1. `dev/harness/plan.py:493` — depth guard returns the partial value
      instead of raising.** Both `evaluate()` (plan.py) and `bevaluate()`
      (bplan.py — same defect, same shape) now raise `RecursionError` past
      named limits (`EVALUATE_DEPTH_LIMIT = 10000`,
      `BEVALUATE_DEPTH_LIMIT = 100000`) instead of silently returning the
      partial value. Contract pinned by
      `TestDeepRecursion.test_evaluate_depth_guard_raises` in
      `tests/bootstrap/test_coverage_gaps.py`. The four pre-existing
      stress tests already had `except RecursionError: pytest.skip(...)`
      arms that never fired before; they now correctly skip when Python's
      own recursion limit trips first. (PR: fix/harness-depth-guard-and-pin-store-toctou)

- [x] **B2. `dev/harness/pin_store.py:37-41` — TOCTOU on save.**
      `PinStore.save()` now writes to a per-process unique tmp path
      (`{path}.tmp.{pid}.{8-hex}`) and `os.replace()`s it into place
      atomically, with best-effort cleanup of the tmp on any failure
      (including `KeyboardInterrupt`). Three new tests in
      `TestPinStoreAtomicWrite` (`tests/sanity/test_pin_store.py`) pin
      the guarantees: no tmp leakage on success; no tmp *or* partial file
      on mid-write failure; idempotent re-save leaves a clean tree.
      (PR: fix/harness-depth-guard-and-pin-store-toctou)

- [x] **B3. 11 of 14 `CodegenError` sites lack `Loc`.** All 14 raise
      sites in `bootstrap/codegen.py` now plumb `loc`. Three already had
      it (1118/1148/1160 in `_compile_var`); the remaining eleven were
      threaded as follows:
      - Sites with `expr` directly in scope (`unsupported expression`,
        `interpolated strings`, `cannot determine instance type`,
        `no instance`, `fix requires at least one parameter`):
        passed `getattr(expr, 'loc', None)` directly.
      - `_compile_global_ref` gained an optional `loc=None` parameter,
        propagated from `_compile_constrained_app`.
      - `_compile_match` captures `expr.loc` and threads it through
        `_compile_{nat,con,tuple,fallback}_match` via a new
        `loc=None` keyword. `_compile_con_match_case3`,
        `_compile_con_body_extraction`, and `_build_app_handler` chain
        the same kwarg, surfacing the match's loc on `unknown
        constructor`, `only 2-tuples supported`, `empty match`, and
        `arity > 2 not yet supported`. Pattern-specific sites prefer
        `pat.loc` and fall back to the match-level `loc`.
      - `_lookup_op_tag` gained `loc=None`; the call site at the
        handle-arm loop passes `arm.loc` (HandlerOp has `loc`).
      Two new end-to-end contract tests under `TestCodegenErrorLocation`
      in `tests/bootstrap/test_coverage_gaps.py` assert
      `<file>:<line>:<col>: error:` prefixes for `only 2-tuples` and
      `unknown effect operation`. The other former bare-message sites
      (`unknown constructor`, `empty match`, `unknown global`) are
      intercepted earlier by the scope resolver and not user-reachable
      from valid surface syntax; loc plumbing on those paths still
      helps if compiler-internal asserts ever fire. Verified by
      eyeballing a real diagnostic:
      `demo.gls:2:5: error: codegen: only 2-tuples supported in
      bootstrap, got 3-tuple`. (PR: fix/codegen-loc-diagnostics)

- [ ] **B4. Source-order vs tag-order semantic mismatch.** The redundancy
      checker (`typecheck.py:809`) uses source order; the codegen sorts arms
      by tag (`codegen.py:1918`). A wildcard-first match warns "subsequent
      arms redundant" but the runtime executes them. Upgrade redundancy to an
      error, or rephrase the message to clarify the codegen still dispatches
      by tag.

- [ ] **B5. Documented invariants without enforcement.** "Abort never appears
      in an effect row" and the `External` requirement are CLAUDE.md
      invariants and `spec/05-type-system.md:1191` defines E0011, but
      `typecheck.py` enforces neither. Comment at `typecheck.py:449` admits
      the deferral. Either add `@xfail(strict=True)` enforcement gates, or
      annotate the spec with "not yet enforced."

- [x] **B6. Three-way drift on test counts.** CLAUDE.md said "1210 tests
      passing" and "1258 passing, 117 skipped" in the same file; the live
      suite was `1282 passed, 145 skipped`. Replaced inline counts with a
      pointer to `pytest -q` for the current count. (PR: docs/audit-truth-up)

- [x] **B7. `SPEC.md:877` lists prelude as "36 definitions."** Reality: 112
      across 8 modules. Stale at M7; never updated through M14.6. Corrected.
      (PR: docs/audit-truth-up)

- [x] **B8. Real-world friction not surfaced in BOOTSTRAP.md.** Audited
      `feedback_for_gallowglass.md` against current behaviour and updated
      BOOTSTRAP.md accordingly:
      - Issues #1a (Bool-constructor match in recursive function),
        #1b (if/then/else eager desugaring), #2 (wildcard pred binding),
        #3 (mixed-arity footgun), and #7 (eff/handle returning
        constructors) are all *already* covered in §§2.4.1–2.4.4 and
        verified fixed by direct reproduction (`go _k T` in a recursive
        T-arm and `if c then go n else 99` both compile and evaluate
        correctly).
      - Issue #5 (codegen errors point at law not source) was closed by
        B3 and is invariant'd in §5 of BOOTSTRAP.md.
      - Issues #4 (demos can't `use` the prelude) and #6
        (recursion-limit guidance) were the two unwritten-lore
        complaints. Added a new §2.4.5 "Known sharp edges that still
        bite" with: a calibrated workload-vs-`sys.setrecursionlimit`
        table, an explanation of the `EVALUATE_DEPTH_LIMIT` /
        `BEVALUATE_DEPTH_LIMIT` raises (B1) including the "fix is the
        same: bump both, or rewrite tail-iteratively, or wait for
        jets" guidance, a pointer to `_PRELUDE_JETS`, and a note
        explaining why demos redefine utilities inline plus where to
        copy them from (`Compiler.gls` lines 25–205). The previous
        §2.4.5 ("Reading existing code") moved to §2.4.6.
      (PR: docs/bootstrap-known-sharp-edges)

## Excrescences to trim

- [ ] **C1. Legacy emit path — rename and bound.** `bootstrap/emit.py`
      (86 lines) is the xocore-era binary seed emitter; the Reaver pipeline
      goes through `emit_pla.py`. Tests still import `emit.py`, so it isn't
      dead — but the symmetric naming hides the asymmetry of role. Rename to
      `emit_seed.py`; module docstring should lead with "Legacy binary
      format. Test-only. Not the production output path."

- [ ] **C2. `tests/planvm/` and `requires_planvm` decorator.** ~110
      unconditionally skipped tests. CLAUDE.md preserves "the decorator and
      infrastructure" for "historical imports," but the cost outweighs the
      value. Collapse `requires_planvm` to a 3-line shim that always skips
      with a deprecation message; archive (or delete) the test bodies.

- [x] **C3. `bootstrap/archive/sire/`.** Deleted. `grep -r 'archive.sire'`
      returned no Python consumers; the four doc references (CLAUDE.md
      structure block, SPEC.md §1 footnote, DECISIONS.md §"Why Python for
      the bootstrap compiler", bootstrap/BOOTSTRAP.md §1.1) were updated
      to point at git history instead. The previous
      `test_sire_stubs_archived` was inverted to
      `test_no_sire_stubs_in_tree`, guarding against accidental
      re-introduction. (PR: cleanup/c3-c4-archive-and-dead-codegen)

- [x] **C4. Codegen dead-code wrappers.** Deleted. `build_ladder` was a
      ~35-line nested function inside `_compile_nat_match` whose return
      value was discarded (the surrounding comment admitted it).
      `_nat_match_top` and `_nat_match_body` were three-line passthroughs
      that sorted by tag then called `_build_nat_dispatch` — collapsed into
      a direct call from `_compile_nat_match`. Net delta: -53 lines. Suite
      unchanged at 1297 passed / 145 skipped — the dead path was indeed
      dead. (PR: cleanup/c3-c4-archive-and-dead-codegen)

- [ ] **C5. `glass_ir.py` conflates two concerns under one name.**
      `render_value` debug-dumps a raw PLAN value; the AST renderer emits the
      spec-defined Glass IR fragment. CLAUDE.md gospel: "Show is for users,
      Debug is for developers — never conflate them"; this file conflates
      them. Move debug rendering to `glass_ir_debug.py`; rename `render_value`
      to `debug_dump_plan_value`; `glass_ir.py` exports only the spec
      renderer.

- [ ] **C6. Codegen function names trace bug-discovery order.** Adopt the
      cleaner GLS self-host vocabulary (Compiler.gls uses
      `cg_build_unary_z_body`, `cg_build_binary_handler_body`, etc.):
      | current | proposed |
      |---|---|
      | `_compile_con_match_case3` | `_compile_adt_dispatch` |
      | `_compile_con_body_extraction` | `_compile_single_arm_field_bind` |
      | `_build_app_handler` | `_build_field_arm_law` |
      | `_build_precompiled_nat_dispatch` | `_build_tag_chain` |
      | `_make_op2_dispatch_reflect` | `_build_elim_app_dispatch` |
      Defer the larger Maranget decision-tree refactor — regression tests
      pin the current shape well enough that the rewrite's risk currently
      exceeds its value.

## Long view (deferred, do not start without revisiting)

- Maranget decision-tree codegen rewrite. Would collapse the dispatch
  family above into a single algorithm. Risk currently > value.
- Harness work-queue refactor of `evaluate`/`apply` to bound stack depth.
  Real fix is jets; revisit after `length`/`map`/`foldl`/`foldr`/`append`
  are jetted.
- `Hd`/`Sz`/`CaseN`/`Ix` Elim optimization. DECISIONS.md classifies as
  post-1.0.

## Discovered later

(Add new findings here — keep IDs unique. Reference from commits and PRs
so future sessions can pick up where the current one stopped.)

## What is good (preserve, do not refactor away)

- Effect-row discipline: explicit at the type level; prelude keeps `Show`
  and `Debug` distinct in name and instance set.
- `emit_pla.py`'s explicit `depth` parameter. De Bruijn context is visible
  in every recursive call.
- DECISIONS.md as a discipline. Almost every non-obvious choice is
  documented.
- The `Env` dataclass + `bind_param` / `bind_param_typed` discipline in
  codegen.
- The `vendor.lock` + `tools/vendor.sh verify` + `bplan_deps.py` canary
  chain.
- The harness/seed split for testing without a planvm binary.
