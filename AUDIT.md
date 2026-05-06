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
        `loc=None` keyword. `_compile_adt_dispatch`,
        `_compile_single_arm_field_bind`, and `_build_field_arm_law` chain
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

- [x] **B4. Source-order vs tag-order semantic mismatch.** Took option
      (c) from the audit: redundancy is now a `TypecheckError` instead
      of a `warnings.warn`. The typechecker rejects ambiguous code
      before the codegen ever sees it, eliminating the disagreement.
      The error message is explicit about the cause and the fix:
      "redundant match arm at index N: <pat> is subsumed by an earlier
      pattern. Reorder so the catch-all pattern comes last (the
      bootstrap codegen dispatches by constructor tag, not source
      order — see AUDIT.md B4)." Verified that no existing code
      triggered the warning before the upgrade (full suite under
      `-W error::UserWarning` passed). Five new contract tests in
      `TestE2ERedundancyIsError` cover the wildcard-first shape, the
      after-full-coverage shape, the duplicate-constructor shape, the
      error-message contract, and the wildcard-last no-regression.
      (PR: fix/b4-redundancy-warning)

- [x] **B5. Documented invariants without enforcement.** Took option (a)
      and the annotation simultaneously. Two
      `@pytest.mark.xfail(strict=True)` regression gates added in
      `tests/bootstrap/test_typecheck.py`:
      `test_b5_abort_in_effect_row_is_rejected` and
      `test_b5_missing_external_is_rejected`. Each test body asserts the
      *correct-future* behaviour (`TypecheckError` with the relevant
      fragment); today both correctly xfail because the type checker
      silently accepts. When enforcement lands they flip to `XPASS`,
      `strict=True` fails the suite, and someone removes the markers —
      at which point B5 is fully closed. CLAUDE.md effect-system bullets
      and `spec/05-type-system.md` §4.4 (Abort) and §14.11 (E0011) now
      each carry an "Implementation status" note pointing at the gate
      tests. Suite: 1302 passed, 117 skipped, **2 xfailed** — the
      xfail count is the new tally to watch. (PR:
      test/b5-invariant-xfail-gates)

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

- [x] **C1. Legacy emit path — rename and bound.** Renamed
      `bootstrap/emit.py` → `bootstrap/emit_seed.py` and rewrote the
      module docstring to lead with "LEGACY BINARY SEED FORMAT" plus
      a pointer to `emit_pla.py` as the production Reaver path. The
      docstring enumerates the three remaining consumers (test
      round-trips, `build_prelude.py`'s pin manifests, the demo
      invocation in CLAUDE.md "Build and Test"). Updated 23 importers
      across the tree (`from bootstrap.emit import …` →
      `from bootstrap.emit_seed import …`) plus six doc references
      (CLAUDE.md, README.md, BOOTSTRAP.md, COMPILER.md, the language
      guide, and AUDIT.md itself). Also fixed the
      `EXPECTED_PYTHON_MODULES` list in
      `test_bootstrap_python_modules_present`.
      (PR: cleanup/c1-rename-emit-seed)

- [x] **C2. `tests/planvm/` and `requires_planvm` decorator.** Collapsed.
      `tests/planvm/test_eval_planvm.py` (336 lines) was deleted entirely
      — its tests were all unconditionally skipped via
      `requires_planvm`, contributing zero signal. `test_seed_planvm.py`
      shrank from 266 lines to a slim shim exporting the names existing
      importers (across `tests/{prelude,compiler}/...`) depend on:
      `requires_planvm`, `seed_loads`, `PLANVM`, `planvm_available`,
      `compile_to_seed`. `requires_planvm` is now an unconditional
      `unittest.skip(reason)` pointing at `tests/reaver/`; `seed_loads`
      and `planvm_available` are `False` stubs (their callers always
      skip first); `compile_to_seed` is preserved as an opaque legacy
      helper. Net delta: 28 fewer skipped tests in CI output (145 → 117).
      (PR: cleanup/c2-planvm-shim)

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

- [x] **C5. `glass_ir.py` conflates two concerns under one name.** Split.
      The debug renderer (`render_value`, `render`, `render_entry`,
      `decode_name`) moved to a new `bootstrap/glass_ir_debug.py` and was
      renamed to honour the gospel's `Show` vs `Debug` distinction:
      `render_value` → `debug_dump_plan_value`, `render` →
      `debug_dump_all`, `render_entry` → `debug_dump_entry`. `decode_name`
      kept its name (it's a utility, not a render). `glass_ir.py` no
      longer imports from `dev.harness.plan` and exports only the
      spec-conforming AST renderer (`render_fragment`, `render_decl`,
      `render_module`, etc.). Updated the single user of the debug
      renderer (`tests/bootstrap/test_codegen.py`) with a
      backward-compatible import alias so the existing `test_render_*`
      tests keep their names. (PR: cleanup/c5-glass-ir-split)

- [x] **C6. Codegen function names trace bug-discovery order.** Renamed
      the dispatch family in `bootstrap/codegen.py` to match the
      vocabulary the GLS self-host already uses (Compiler.gls names them
      `cg_build_unary_z_body`, `cg_build_binary_handler_body`, etc.):
      | old name | new name |
      |---|---|
      | `_compile_con_match_case3` | `_compile_adt_dispatch` |
      | `_compile_con_body_extraction` | `_compile_single_arm_field_bind` |
      | `_build_app_handler` | `_build_field_arm_law` |
      | `_build_precompiled_nat_dispatch` | `_build_tag_chain` |
      | `_make_op2_dispatch_reflect` | `_build_elim_app_dispatch` |
      Pure rename: every call site was internal. Doc references in
      CLAUDE.md (Bootstrap Codegen Pitfalls), DECISIONS.md, BOOTSTRAP.md,
      ROADMAP.md, CODEGEN_PLAN.md, and the test-file comments were
      swept in the same pass so the war-diary still names live
      symbols. The larger Maranget decision-tree refactor is deferred —
      regression tests pin the current shape well enough that the
      rewrite's risk currently exceeds its value. (PR:
      refactor/c6-codegen-rename-dispatch)

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

- [x] **D1. `typecheck()` defaulted `module='Main'`; renderers silently
      dropped types on mismatch.** A caller that passed a different module
      name to `typecheck()` and to `render_module()` / `render_fragment()`
      saw no type annotations in the IR and got no error — `typecheck`'s
      `TypeEnv` keys were `Main.*` while the renderer looked up `Other.*`
      and silently found nothing. The `module` parameter is now required
      (no `'Main'` default), and both renderers raise `ValueError` if the
      supplied `type_env` has no keys for the renderer's module, listing
      observed module prefixes as a hint. `bootstrap/render_demo.py` now
      honours the tightened contract by passing `type_env=None` when the
      demo module's own typecheck failed. Two regression tests in
      `tests/bootstrap/test_glass_ir.py::TestTypeAnnotatedRendering` pin
      the mismatch-raises and match-passes shapes. (PR: #71)

- [x] **D2. No per-subexpression type accumulation; no IDE-facing position
      query.** `typecheck()` returned only top-level `fq → Scheme`; there
      was no `expr_types` map, no "type at position X" hook, no symbol
      table export. Hover-style queries from any tooling surface had to
      either re-run inference with a position probe or invent their own
      walker. Added an opt-in `TypeChecker.expr_types: dict[int, Type] |
      None`; `infer()` now records `id(expr) → ty` when the side-table is
      set, with all metas zonked at the new `typecheck_with_types(...)`
      entry. New `bootstrap/ide.py` module exposes
      `type_at_position(program, expr_types, line, col, filename=None)`
      and the convenience `type_at_offset(source, module, ...)`. Position
      semantics are documented in `ide.py`: `Loc` carries only start
      positions, so the innermost expression is approximated as the
      latest start-`Loc` ≤ cursor on the same file. Eight tests in
      `tests/bootstrap/test_ide.py` cover literals, innermost-wins,
      bounds, filename filter, end-to-end pipeline, and the opt-in
      contract. (PR: #72)

- [x] **D3. Source `Loc` and contract clauses silently dropped at Glass
      IR rendering.** Every AST node carried `Loc(file, line, col)`, and
      `DeclLet.contracts` preserved `pre`/`post`/`inv`/`law` clauses
      (with `Proven` / `Deferred(NoSolver)` / etc. status) — but the
      renderer ignored both. An IDE consuming Glass IR text could not
      map back to source, and contract proof status disappeared from any
      hover view. Now `render_decl(DeclLet)` prepends a `-- @ Loc`
      comment and one `-- {kind} {status} ({pred})` comment per
      contract before the `let` header. Comment form (rather than a
      structured `| pre ...` line) keeps the IR parseable by the
      existing surface parser. Predicate text is recovered from the
      parser's token-list via `_render_pred_tokens` — lossy on
      whitespace but adequate for display. Three tests in
      `tests/bootstrap/test_glass_ir.py::TestLocAndContractRendering`
      pin the loc-precedes-let, contract-rendering, and
      no-contracts-only-loc shapes. (PR: #73)

- [x] **D4. `DeclType` constructor `arg_types` emitted Python AST repr.**
      `render_decl` for type declarations did `' '.join(str(f) for f in
      ctor.arg_types)` — but `arg_types` is a list of AST type nodes
      (`TyVar`, `TyCon`, `TyApp`, `TyArr`, ...), not strings, so `str()`
      produced Python dataclass repr like `TyApp(fun=TyCon(name=...))`
      in the IR. Surfaced the moment the new MCP server's
      `compile_snippet` tool processed any parameterized type. Fixed
      with a new `render_ast_type(ty)` for type-position AST nodes
      (distinct from `typecheck.pp_type`, which renders the
      typechecker's internal monotype representation), used in
      `render_decl` and wrapped via `_wrap_atom` so multi-token args
      stay readable. Falls back to `<type:Name>` for unknown shapes —
      visibly ugly rather than silently emitting Python repr. The
      Gnome's earlier audit pass had flagged this as an unverified
      suspicion; the MCP work confirmed it. Note: the inner type-name
      references inside constructor arg types (`Tree` in `Node a (Tree
      a)`) stay unqualified — `scope.py:501` returns `DeclType` as-is
      without rewriting type-position names. FQ-qualifying constructor
      arg type refs is a separate resolver fix; this PR only stops the
      renderer from emitting Python repr. Two tests under
      `TestRenderTypeDecl` in `tests/bootstrap/test_glass_ir.py` cover
      the parameterized and arrow-arg shapes. (PR: #75)

- [x] **D5. `_build_nat_dispatch` mis-routes named/wildcard arms when
      first arm's tag is positive.** Surfaced bisecting the calculator REPL demo. Minimal
      reproducer (in `tests/reaver/fixtures/repro5_4level.gls` style):
      a function `λ lhs rest → match rest { | Cons h t → match h {
      | TkPlus → match t { | Cons h2 t2 → match h2 { | TkNum n →
      EAdd lhs (EConst n) | _ → … } | … } | … } | … }`. With input
      `(EConst 5) [TkPlus, TkNum 7]` should return
      `EAdd (EConst 5) (EConst 7)`. Actually returns `EConst 5` —
      the constructor expression in the deepest arm evaluates to
      just `lhs`, suggesting `lhs` (an outer slot) doesn't survive
      the 4-level lifting/dispatch chain to the deepest arm body.
      3-level nesting works; 2-level works; 4-level breaks.
      Fix-loop and non-fix lambda forms both reproduce — not
      fix-specific. Reproducers preserved at
      `tests/reaver/fixtures/repro_d5_4level_breaks.gls` and
      `tests/reaver/fixtures/repro5_3level_works.gls`; bisect harness
      at `tests/reaver/_calc_layers.py`.

      **Root cause (resolved):** `_build_nat_dispatch`'s outer level
      (`bootstrap/codegen.py:1935-1957`) used the FIRST arm's body as
      the `op2` zero-case unconditionally, with no handling for when
      `arms_sorted[0].tag > 0`. The all-nullary path of
      `_compile_con_match` routes here for `match h { | TkPlus → A
      | _ → B }` patterns where TkPlus has tag 1 — the dispatch then
      fired arm `A` for scrutinee=0 (empty constructor type — never
      legal at runtime) and arm `B` (the wildcard) for scrutinee=1
      (TkPlus itself). Backwards.

      `_build_tag_chain`, the parallel pre-compiled-pairs version, has
      had `first_tag > 0` handling since F11 (it shifts every tag down
      by 1, uses the wildcard as the new zero, recurses on the
      shifted chain). The fix mirrors that pattern in
      `_build_nat_dispatch` with the additional lambda-lifting machinery
      it needs (captures + self-ref). 1346 tests pass locally;
      end-to-end calc REPL demonstrably evaluates `1+2 → 3`, `5 → 5`
      under Reaver. (PR: fix/d5-nested-match-slot-drop)

- [x] **D6. All-nullary explicit arms + wildcard against a type with
      field-bearing siblings: wildcard does not fire for App scrutinees.**
      Surfaced when promoting the calc REPL to the `demos/` tree.
      `match e { | EErr → … | _ → … }` where `Expr` also has `EConst`,
      `EAdd`, etc.: con_arms is all-nullary so `_compile_con_match`
      routed to `_build_nat_dispatch`, which builds an Elim whose
      app-case is `id_pin` (returns the App unchanged). For an App
      scrutinee — i.e. any non-EErr value — `id_pin` short-circuited
      the wildcard and returned the original value, so `check (EConst
      7)` returned `EConst 7` (interpreted as a Nat = 0) instead of
      the wildcard's body. The same issue masked the calc REPL's
      `parse_term_loop`/`parse_expr_loop` wildcard arm: for input
      `[Num 12, Num 34]` the loop returned `id_pin App`, not
      `MkPair lhs rest`, breaking the outer match.

      **Root cause (resolved):** `_compile_con_match` now detects
      whether the matched type has any field-bearing sibling
      constructor; if so AND a wildcard is present, it routes through
      `_compile_adt_dispatch` so the App branch can fire properly. The
      `_compile_adt_dispatch` `not field_arms` path now builds a
      `_build_wild_app_handler` const-law (lifting captures + self-ref)
      for the App branch instead of using `id_pin`. Regression tests
      `test_wild_app_handler_top_level` and
      `test_wild_app_handler_captures_outer_lambda` in
      `tests/bootstrap/test_codegen.py`. End-to-end calc REPL multi-line
      input is exercised by `tests/reaver/test_repl_calc.py`.

- [x] **D7. Top-level App constants inlined into law bodies, bare Nat
      literals collide with de Bruijn slots.** Same calc-REPL bisect:
      `let render_err : Nat = BPLAN.add (BPLAN.lsh 1 32) …` (a constant
      Nat encoded via BPLAN ops) compiles to an App tree containing
      `N(1)`, `N(32)` literals. When referenced inside a law body via
      `_compile_var`, the Apps were returned as-is and the emitter's
      body-context renderer treats `is_nat(v) and v <= depth` as a
      slot reference (`_1` instead of literal `1`). Result: every body
      reference to a top-level App constant silently captured the
      enclosing law's first parameter. Surfaced as missing `\n`s in
      the REPL's `render_err` output (the marker bit was the literal
      `1<<32`, lost to slot rebinding).

      **Root cause (resolved):** `emit_pla.emit_program` now registers
      `is_law(val) | is_pin(val) | is_app(val)` top-level entries in
      `bind_table`, so cross-binding references emit as the bare
      symbol (`Main_render_err`) and Reaver's `BIND` lookup resolves
      to the evaluated value. Pure-Nat constants (e.g. `let n : Nat =
      42`) still inline — `is_nat(val)` deliberately stays out of the
      gate because `_compile_var`'s `_compile_nat_literal` already
      emits the quote form for them.

- [x] **D8. Nested `let` inside an in-law expression emitted a `(1 rhs
      body)` form Reaver's parser doesn't recognise.** Surfaced during
      the Phase G #2 pre-flight smoke test: bootstrap-compiling
      `compiler/src/Compiler.gls` and running the result under Reaver
      crashed at `Compiler_lex_skip_ws` with `law: unbound: "_3"`.
      14-line reproducer at
      `tests/reaver/fixtures/repro_d8_let_in_arm.gls`.

      `_compile_local_let` emitted in-law lets as `A(A(N(1), rhs),
      body)`, which the body emitter rendered as the bind syntax
      `_d1(rhs) … body`. Reaver's `lawExp` parser
      (`vendor/reaver/src/hs/PlanAssembler.hs`) only accepts that bind
      form at the law's body root — between `(sig)` and the body form,
      not nested inside an expression. When the let was the body of a
      match arm (extremely common — `Compiler.gls` has hundreds of
      occurrences), the bind form landed in Elim's z-slot and Reaver
      tried to resolve `_d1` as a slot reference in the enclosing law.

      **Root cause (resolved):** added a `top_of_law: bool` field to
      `Env`, set True at law-body entry points (`_compile_constrained_-
      let`, `_compile_lam_as_law`, `_compile_lam_lifted`); flipped to
      False at the top of `_compile_expr` for any non-let, non-pin,
      non-ann expression (the chokepoint that ensures arm bodies, app
      args, etc. don't inherit the flag). `_compile_local_let` checks
      it: at law body root → emit `(1 rhs body)` and preserve the flag
      through the let-chain body so multiple chained top lets keep the
      native form; nested → lambda-lift via the same capture-and-
      partial-apply pattern as `_build_field_arm_law` and
      `_make_pred_succ_law`. `_compile_expr_pin` preserves the flag so
      programmer pins don't break the chain. Seven regression tests
      `test_d8_*` in `tests/bootstrap/test_codegen.py` and two
      differential tests in `tests/reaver/test_differential.py` pin
      both sides (top-of-law form unchanged; nested arm-body let
      lambda-lifts and runs to the right value on Reaver). Empty-source
      Compiler.gls now parses cleanly under Reaver — the
      arithmetic-migration concern (Phase G #2 proper) becomes the
      next bottleneck rather than this codegen bug.

D1–D4 above were the IDE-tooling prerequisites that landed
back-to-back in support of the new `bootstrap/mcp_server.py` (PR #74) —
a stdio MCP server exposing `compile_snippet`, `infer_type`,
`explain_effect_row`, and `render_fragment` to LLM consumers, with the
Core prelude built once at startup and threaded as priors into every
per-call snippet build. The server stops at Glass IR + pin hashes; it
does not depend on Reaver. See `bootstrap/mcp_server.py` for the
architecture and `tests/bootstrap/test_mcp_server.py` for tool
contracts. The arrival of an LLM-facing consumer is what made D1–D4
visible: each was a silent gap that didn't matter until something
external started reading IR text.

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
