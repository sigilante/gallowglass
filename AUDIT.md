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

- [ ] **A4. No byte-identity gate on self-host emission.**
      `tests/compiler/test_selfhost.py` checks structure but doesn't compare
      `emit_program` output to a stored golden. With Path A skipped pending
      Phase G, byte-identity is the only correctness criterion for self-host
      and nothing enforces it. Stash a hash of the prelude `Compiler.gls`
      Plan-Asm output; fail loudly when it drifts.

## Sharp edges

- [ ] **B1. `dev/harness/plan.py:493` — depth guard returns the partial value
      instead of raising.** `if _depth > 10000: return val` silently produces
      an `A` node where callers expect a `Nat`. The four `TestDeepRecursion`
      tests catch `RecursionError` to skip; that handler now never fires.
      Raise `RecursionError("PLAN evaluator depth exceeded")`.

- [ ] **B2. `dev/harness/pin_store.py:37-41` — TOCTOU on save.**
      Existence-check → `open(wb)` → write, no atomic rename. Latent today
      (no `make -j`), active the moment CI parallelizes. Write to `.tmp` then
      `os.replace()`.

- [ ] **B3. 11 of 14 `CodegenError` sites lack `Loc`.** Notably
      `unknown global` (1270), `unknown constructor` (1910), `empty match`
      (2459), `arity > 2 not yet supported` (2294). CLAUDE.md asserts
      user-facing diagnostics print `file:line:col:`; these violate the
      contract. `feedback_for_gallowglass.md` complaint #5 calls this out.
      Plumb `expr.loc` into each raise.

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

- [ ] **B8. Real-world friction not surfaced in BOOTSTRAP.md.**
      `feedback_for_gallowglass.md` (2026-04-29) lists six concrete defects.
      The two open ones — Bool-constructor match in recursive functions
      losing `self_ref_name`, and `if/then/else` eager desugaring — are still
      latent and a stranger has no way to know. Add a "Known sharp edges"
      section to BOOTSTRAP.md mirroring the published "Bootstrap Codegen
      Pitfalls" treatment.

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

- [ ] **C3. `bootstrap/archive/sire/`.** `grep -r 'archive.sire' .` returns
      no consumers. Delete; git history preserves it.

- [ ] **C4. Codegen dead-code wrappers.** `_nat_match_top` and
      `_nat_match_body` (codegen.py:1875-1883) are 3-line passthroughs to
      `_build_nat_dispatch`. `build_ladder` (line 1683) is a dead inner
      function whose return value is never used (the comment at 1669 says
      so). Delete all three; call `_build_nat_dispatch` directly.

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
