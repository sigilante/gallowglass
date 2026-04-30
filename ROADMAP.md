# Gallowglass Roadmap

**Last updated:** 2026-04-30
**Current status:** v0.99999-beta released. Reaver migration complete (Phases 0/A/B+C/D/E/F merged via #47–#53). Self-host validation on Reaver (Phase G) is the next coherent arc. 1261 tests passing.

This document is the delivery plan: what ships in what order and why. The *what* of each feature is in `SPEC.md` and the `spec/` documents. The *why* of ordering decisions is in `DECISIONS.md`.

---

## ✅ Alpha

**Alpha is declared.** The original M8.8 Path A gate (cog wrapper + planvm byte-identical
round-trip) has been superseded by the PLAN spec update: the cog/driver model no longer
exists. It has been replaced by direct side-effects with virtualization for sandboxed
pure execution. There is no stable cog wrapping API to target.

The alpha acceptance criteria, as met:

- M8.8 Path B: GLS `emit_program` processes full `Compiler.gls` and produces correct
  Plan Assembler output via the Python harness ✅
- planvm seed loading: `Compiler.main` seed is a valid seed ✅ (planvm-gated CI)
- M9 restricted dialect improvements (fix, tuples, mutual recursion, typechecks) ✅
- M10 effect handlers (CPS codegen, pure, do-notation, namespacing) ✅
- Integration test battery (Fibonacci, Ackermann, Sudan) ✅
- 667 tests passing ✅

**Deferred to post-alpha (pending upstream stabilization):**

M8.8 Path A equivalent — running the compiler via the VM's side-effect I/O model to
validate planvm byte-identical output — is deferred until the direct side-effects +
virtualization API stabilizes (see `IO.md`). This is tracked as a post-1.0 CI gate,
not an alpha blocker.

Everything below is post-alpha.

---

## ✅ M9 — Restricted dialect improvements

Goal: make the restricted dialect useful for real programs before adding
architectural complexity.

### ✅ M9.1 — `fix` expressions

`fix λ self args → body` surface syntax. First param maps to law self-ref (N(0));
law arity = len(user_params). `_compile_fix` in `bootstrap/codegen.py`.

### ✅ M9.2 — Tuples

`(a, b)` encodes as `A(tag_0, a, b)` using quote form for tag 0 in law bodies.
`PatTuple` dispatches via `_compile_con_match_case3` with ConInfo(tag=0, arity=2).

### ✅ M9.3 — Mutual recursion (SCC compilation)

Tarjan SCC detection in `compile()` pass 3. Multi-element SCCs use shared-pin row
encoding (`spec/02-mutual-recursion.md`). Canonical SCC ordering: lexicographic.

### ✅ M9.4 — Type checker extensions

`ExprFix` now correctly infers the fix type `T` (not the lambda type `T→T`).
`_check_decls` groups DeclLets into SCCs (Tarjan) and processes in topological order,
deferring generalization for multi-member SCCs. 79 tests passing.

**Ordering note:** M9.4 is a prerequisite for M10 (effect rows in the type checker
before checking handlers). All other M9 items are independent.

---

## M10 — Effect handlers

The defining language feature. `handle` expressions with explicit `resume`
continuations (Koka-style). Handlers compile using direct-style CPS: the
continuation `k` in a handler arm is a partially applied PLAN law.

### ✅ M10.1 — Effect row types in type checker

`TRow(effects: dict, tail: TMeta)` and `TComp(row, ty)` added. Full row unification
(flatten-and-distribute algorithm). `DeclEff` registers each op as
`∀ params r. A → {E args | r} B`. `ExprHandle` checked against spec §5.1:
computation type `{E, R} α`, return arm `x : α → β`, op arms `arg, k → β`, result `{R} β`.
Tests: `tests/bootstrap/test_typecheck.py` (89 pass — 10 new).

### ✅ M10.2 — Codegen: CPS transform for effect handlers

`eff` ops compile to 3-arg CPS laws. `handle comp { arms }` assembles as
`A(A(comp_val, dispatch_fn), return_fn)`. Do-notation `x ← rhs in body` compiles
to a CPS bind via nested lambda-lifted laws. Outer local captures are lambda-lifted
into both the dispatch law and the inner continuation law.
Tests: `tests/bootstrap/test_codegen.py` (63 pass — 10 new).

### ✅ M10.3 — `pure` for do-notation

`pure v` registered as builtin CPS law `L(3, "pure", bapp(N(3), N(1)))`.
`pure v` = `A(pure_law, v)` — a 2-arg partial application that calls `k v`.
Enables do chains to terminate with a pure computed value.

### ✅ M10.4 — State-threading handler validation

Multi-op do chains with captured free variables compile and evaluate correctly.
Tests confirmed nested lambda lifting across `ss ← get_st () in pp ← put_st ss in pure ss`.

### ✅ M10.5 — Per-effect tag namespacing

`_resolve_handler_arm` resolves each `HandlerOp.op_name` to its FQ form so the codegen uses
per-effect tag numbering. Two effects sharing a short op name produce a scope ambiguity error
rather than silent mis-dispatch.

**M10.6 — Integration test battery (`tests/bootstrap/test_programs.py`):**
Fibonacci (self-recursive let + fix), Ackermann, Sudan function. 20 tests.
All programs compile via Python bootstrap and evaluate against expected Nat
outputs using the PLAN reference evaluator. ✅

**M10.7 — GLS compiler: `EFix` + `TkFix` support:**
Added `TkFix` token, `EFix Nat (Pair (List Nat) Expr)` AST constructor,
keyword lexer recognition of `fix`, `parse_fix_body_pe`, `cg_compile_fix`.
Updated `expr_tag`, `cg_cf_dispatch`, `cg_hv_dispatch`, `sr_dispatch`,
`cg_compile_complex`, and `parse_expr_dispatch`. M8.8 self-hosting invariant
preserved (667 tests passing). ✅

**Remaining M10 scope:**
- Full surface syntax integration with effect annotations in the prelude
- Runtime: no change — continuations are ordinary PLAN values

**Unblocked by M10:** `IO`, `Exn`, `State`, `Generator` effects. The CSV and
calculator demos become interactive. The full surface syntax of SPEC.md §3.4
becomes compilable.

---

## M11 — Typeclasses

Implicit dictionary synthesis at call sites, instance declaration, coherence.

### ✅ M11.1 — Codegen: DeclClass registration

`_compile_class(decl: DeclClass)` stores `class_fq → [method_names]` in `_class_methods`.
Class declarations produce no PLAN value; they are metadata for dictionary construction.

### ✅ M11.2 — Codegen: DeclInst dictionary construction

`_compile_inst(decl: DeclInst)` compiles each instance method body and emits named PLAN laws:
- `Module.inst_ClassName_TypeKey_method` — individual method law
- `Module.inst_ClassName_TypeKey` — dict shortcut for single-method classes

**Named-law flat encoding** (Option 2): each method is a separate named PLAN law.
Multi-method classes have one named law per method; constraints add one param per method
to constrained function arities.

### ✅ M11.3 — Codegen: constrained DeclLet and call-site dict insertion

`_compile_constrained_let` adds one leading dict parameter per method per constraint.
Inside constrained function bodies, class method vars map to de Bruijn dict params.

`_compile_constrained_app` auto-inserts instance dicts at call sites using heuristic type
inference: `ExprNat` → Nat, `ExprText` → Text, type-annotated let params (from the
enclosing function's type annotation), explicit `ExprAnn` casts.

**Encoding note:** declaration-order interleaving — instances are compiled inline as
they appear in source, so instance method bodies can reference earlier lets and
subsequent lets can use the freshly emitted instance dicts at call sites.

**17 new tests** in `tests/bootstrap/test_typeclasses.py`.

### ✅ M11.4 — Core prelude `Eq`, `Ord`, `Add` instances

`class Eq a { eq : a → a → Bool }`, `class Ord a { lt : a → a → Bool; lte : a → a → Bool }`,
`class Add a { add : a → a → a }` declared in `prelude/src/Core/Nat.gls` and `Core/Bool.gls`.
Scope resolver updated to allow a `DeclLet` to shadow a same-named `BindingClassMethod`
(concrete implementation takes precedence without a duplicate-definition error).
Instances: `Eq Nat`, `Ord Nat` (with `nat_lte`), `Add Nat`; `Eq Bool`.
10 new Python harness evaluation tests in `tests/bootstrap/test_typeclasses.py`.
Planvm seed-loading tests added to `tests/prelude/test_core_nat.py` and `test_core_bool.py`.
`Show` deferred: requires Text manipulation infrastructure not yet in the prelude.

**M11.5 — GLS self-hosting compiler: `DeclClass`/`DeclInst` support:** ✅
Added `TkClass`/`TkInstance` tokens, `DClass`/`DInst` AST constructors, `kw_class`/`kw_instance`
keyword nats, `lex_classify_ident` keyword dispatch, `parse_class_decl`/`parse_inst_decl` parser,
`sr_collect_globals` DInst method name collection, `sr_resolve_decls` DInst member body rewriting,
`cg_compile_inst_members`/`cg_pass3` DInst codegen. Fixed `decl_is_let/type/ext/class/inst`
predicates (wildcard-arm-drop bug: now use exhaustive 5-arm matches). 20 new tests in
`tests/compiler/test_m11.py`. 734 tests passing.

**Remaining M11 scope:**
- Advanced type inference for dict insertion (non-Nat types, polymorphic call sites)

**Unblocked by M11:** `Show`, `Eq`, `Ord`, `Add`, `Serialize` instances. The
standard prelude becomes expressible without explicit dictionary passing.

---

## ✅ M12 — Module system

Multi-file compilation, `use` imports, dependency-ordered build.

`bootstrap/build.py` — `build_modules([(module_name, source_text), ...])`:
- Parses all sources, scans `DeclUse` declarations to build a dependency graph.
- Kahn's topological sort; raises `BuildError` on circular dependency or unknown module.
- Compiles in dependency order, threading resolved `Env` objects forward for
  scope resolution and pre-compiled PLAN values forward for codegen global lookup.
- Source-list order is tiebreaker; callers may provide files in any order.

`bootstrap/codegen.py` — `compile_program(..., pre_compiled=dict)`:
- New optional parameter; pre-populates `Compiler.env.globals` with values from
  upstream modules so cross-module `ExprVar` references resolve correctly.

**Supported `use` forms:**
- `use Mod` — qualified access only (`Mod.name`)
- `use Mod { names }` — specific names bound in scope
- `use Mod unqualified { names }` — names available without `Mod.` prefix

**13 new tests** in `tests/bootstrap/test_modules.py`:
unqualified/qualified/qualified-only imports, transitive three-module builds,
source-order independence, cross-module algebraic types, cycle detection,
unknown module error, single-module smoke test.

**M12.1 — Cross-module typeclass instances** ✅
- `scope.py` `_process_use`: merges `other_env.class_methods` into importing module's env; auto-imports class method bindings when a class is imported (`_import_class_methods`).
- `codegen.py` `_resolve_class_fq`: resolves short class name to defining-module FQ; `_compile_inst`, constrained-let registration, `_compile_constrained_let`, `_compile_constrained_app` all use cross-module fallback (suffix search for `*.inst_ClassName_TypeKey`).
- `build.py`: threads `pre_class_methods` (merged `Env.class_methods` from all upstream envs) to `compile_program`.
- `Core.Bool`: now `use Core.Nat { Eq }` instead of re-declaring `Eq` locally.
- **5 new tests** in `tests/bootstrap/test_modules.py`. 739 tests passing.

**M12.2 — GLS compiler DEff/EHandle/EDo support:** ✅
Added `TkEff`/`TkHandle`/`TkPure`/`TkRun` tokens, `EHandle`/`EDo`/`DEff` AST constructors,
keyword nats (`kw_eff`, `kw_handle`, `kw_pure`, `kw_run`), lexer keyword chain extensions,
parser (`parse_eff_decl`, `parse_handle_expr`, do-bind detection), scope resolver
(`sr_collect_eff_op_names`, `sr_rewrite_handle_arms`), CPS constants (`cps_id_law`,
`cps_null_dispatch`, `cps_compose`, `cps_pure_law`, `cps_run_law`), full CPS effect handler
codegen (`cg_compile_handle`, `cg_compile_do`, `cg_compile_dispatch_fn`, `cg_compile_return_fn`,
`cg_build_handle_dispatch`, `cg_register_eff_ops`, `cg_register_effs`). All `decl_is_*`
predicates updated to 6-arm exhaustive matches. ~720 lines added to `Compiler.gls`.
25 new tests in `tests/compiler/test_m12_effects.py`.

**M12.3 — Superclass constraint flat expansion:** ✅
When a function is constrained by a class with superclass constraints (e.g., `Eq a => Ord a`),
the compiler recursively expands to include superclass method params before the class's own
method params — at both declaration sites and call sites. Added `_class_constraints`,
`_expand_superclass_constraints`, `_expand_one_constraint` to `bootstrap/codegen.py`.
Tests: `test_superclass_flat_expansion`, `test_superclass_multi_level` in `test_coverage_gaps.py`.

**M12.4 — GLS compiler DeclUse support:** ✅
Added `TkUse` token, `kw_use` keyword nat, `DUse` AST constructor (7th Decl variant),
`parse_use_decl`/`parse_use_names` parser, all `decl_is_*` predicates updated to 7-arm
exhaustive matches. DUse is a pass-through in scope/codegen — cross-module resolution
handled by external build driver. ~110 lines added to `Compiler.gls`.

**M12.5 — Data.Csv end-to-end integration tests:** ✅
Minimal Data.Csv-style error handling via effects: `CsvError` type, `Exn` effect with
`raise` op, handle expressions with return/op arms, do-notation chains, pattern matching
on error variants. 9 tests in `tests/bootstrap/test_data_csv.py` — validates the full
effect handler pipeline (type definitions, eff declarations, handle/pure/run, do-bind).

**Deferred:**
- Package declarations (`package { version, depends }`) — reserved keywords only
- Explicit `export { ... }` lists — all bindings implicitly exported
- Module PinId stability — seed format handles content-addressing implicitly

**Unblocked by M12:** The full Core prelude can be split across files. Cross-module
typeclass instances (deferred above). The `mod` declaration syntax from
`spec/06-surface-syntax.md §11`.

---

## M13 — Effects & typeclasses (compiler maturity)

Goal: bring the typeclass and effect system to the level needed for a real
prelude — default methods, polymorphic instances, shallow handlers.

### ✅ M13.1 — Default methods

`_compile_class` stores `ClassMember.default` exprs in `_class_defaults`.
`_compile_inst` pass 2: for methods in the class not provided by the instance,
compiles the default body in an env where already-provided instance methods are
in globals so sibling references resolve. Override takes precedence.
3 new tests in `tests/bootstrap/test_typeclasses.py`. 893 tests passing.

### ✅ M13.2 — Polymorphic instances (`Eq a => Eq (List a)`)

`ConInfo.type_name` maps constructors to parent types. `_typearg_key` handles
`TyApp` (outer constructor only). `_infer_type_key` extended with constructor
application and nullary constructor detection for call-site instance resolution.
`_compile_inst` refactored: constraint dict params, sibling binding, self-ref for
unconstrained only. Constrained instances registered in `_constrained_lets`.
4 tests. 897 passing.

### ✅ M13.3 — Shallow handlers (`once`)

Open-continuation CPS protocol: continuations are 2-arg `(dispatch, value)` instead
of 1-arg `(value)`. The dispatch function applies either `dispatch_current` (deep)
or `dispatch_parent` (shallow/once) to the open continuation. `_FORWARD_K` helper
preserves nested handler layers during forwarding. Virtual resume index +
substitution avoids de Bruijn scope issues. 5 tests (generator pattern, k-called
shallow, deep contrast, mixed arms, nested forwarding). 902 passing.

### M13.4 — GLS compiler parity for M13.1–M13.3 ✅

Self-hosting compiler updated to open-continuation CPS protocol matching bootstrap.

**Changes to `Compiler.gls`:**
- Added `cps_id_open` (L(2,0,N(2))), `cps_compose_open`, `cps_forward_k` constants
- Updated `cps_pure_law` to `k_open(dispatch, value)` protocol
- Updated `cps_run_law` to use `cps_id_open` instead of `cps_id_law`
- `cg_compile_dispatch_fn`: builds `dispatch_fn_base` (self-ref + captures),
  `dispatch_current`, `resume_expr = k_open(dispatch_current)`. Forward body uses
  `_FORWARD_K` to wrap k_open and preserve nested handler layers.
- `cg_build_handle_dispatch`: virtual resume index substitution via
  `subst_virtual_resume` — arm bodies compile with sentinel index 9999999, then
  substitute with actual resume expression (all deep for now).
- `cg_compile_handle`: uses `cps_compose_open` instead of `cps_compose`.
- `cg_compile_do`: inner continuation params reordered to `[caps, k_open_outer,
  dispatch, x]` — partial application `(caps, k_open_outer)` gives 2-arg open
  continuation. Outer body passes `inner_cont_open` directly (not applied with
  dispatch).
- Moved `cg_apply_range` before dispatch codegen (forward-reference fix).
- 5 new GLS compiler tests (CPS constant presence, virtual resume, selfhost regression).

**Deferred:** Default method storage/fallback in GLS `cg_compile_inst_members`
requires DClass AST change + parser update. Tracked for future work. Shallow
handler (`once`) support in GLS requires AST extension for arm once-flag.

**Deferred past 1.0:** multi-param typeclasses, functional dependencies, deriving,
typeclass laws verification, effect polymorphism in constraints.

---

## M14 — Core Prelude

Goal: complete the core library to the level where real programs can be written.

### M14.1 — Complete existing classes

`Ord`: add `compare`, `gt`, `gte`, `min`, `max` (needs M13.1 default methods for
the ones derivable from `lt`). `Eq`: add `neq` default. Move `div_nat`/`mod_nat`
from Text to Nat where they belong.

### M14.2 — Missing core types

`Result a b` (`Ok a | Err b`). `Pair a b` with `fst`, `snd`.

### M14.3 — Collection instances

`Show Option`, `Show List`, `Eq Option`, `Eq List`, `Eq Result`. Needs M13.2
polymorphic instances.

### M14.4 — Missing combinators

`fix` as a standalone combinator, `fst`, `snd`, pipe `|>`, function composition `·`.

### M14.5 — `Debug` class

Spec mandates Show/Debug distinction. Minimal implementation: same output as Show
initially but distinct class identity and instances.

### ✅ M14.6 — Cross-module prelude refactor

All 8 prelude modules use `use` imports and compile together via `build_modules`.
Dependency chain: Combinators → Nat → Bool → Text → Pair → Option → List → Result.
Full-prelude integration test (`test_full_prelude.py`) validates cross-module
compilation and evaluates functions spanning all modules. 14 tests.

**Deferred past 1.0:** `Serialize`, `Functor`/`Monad`/`Applicative` (higher-kinded),
`Int`/`Rational`/`Fixed`, `Bytes`, IO/State/Exn effect modules, `Core.Inspect`,
`Core.Abort`.

---

## M15 — Full surface syntax

Goal: close the gap between the restricted dialect and `spec/06-surface-syntax.md`.
The parser already handles most forms; codegen rejects them.

### ✅ M15.1 — Record types, construction, update, patterns

Records desugar to single-constructor ADTs during scope resolution:
- `DeclRecord` → `DeclType` with one constructor (tag 0), fields become positional args
- `ExprRecord { x = 1, y = 2 }` → constructor application, fields reordered to declaration order
- `ExprRecordUpdate base { x = 3 }` → match + rebuild with overrides
- `PatRecord { x = px }` → `PatCon` with positional sub-patterns, missing fields become `PatWild`
- Field-set → record-type reverse lookup for type inference from field names
- Punning: `{ x }` in both expressions and patterns means `{ x = x }`
- 7 new integration tests in `tests/bootstrap/test_programs.py`.

### ✅ M15.2 — Type aliases

`DeclTypeAlias` parsed, scope-resolved, type-checked. No codegen needed — types are
fully erased. Type aliases produce no runtime code by design.

### ✅ M15.3 — List/Cons expressions and patterns

Desugared during scope resolution to constructor forms, then compiled normally:
- `ExprList [a, b, c]` → `Cons a (Cons b (Cons c Nil))` (nested constructor applications)
- `PatCons h :: t` → `PatCon("Cons", [h, t])` (single-level constructor pattern)
- `PatList []` → `PatCon("Nil", [])` (nullary constructor pattern)

Multi-element `PatList [a, b]` desugars to nested `PatCon` which requires nested match
compilation (not yet supported by the bootstrap match codegen). Single-level patterns
(`[]`, `h :: t`) work now; nested list patterns deferred to nested match compilation.

7 new tests in `tests/bootstrap/test_programs.py`.

### ✅ M15.4 — Or patterns

`PatOr` desugared during scope resolution: each alternative in `p1 | p2 → body`
becomes a separate match arm with the same body. 6 new tests in
`tests/bootstrap/test_programs.py`.

### ✅ M15.5 — Guards in match arms

Desugared during scope resolution: `| pat if guard → body` becomes
`| pat → if guard then body else match __scrut { remaining_arms }`. Scrutinee
bound to a fresh variable to avoid re-evaluation. Guard failure falls through
to a re-match on remaining arms. 5 new tests in `tests/bootstrap/test_programs.py`.

### ✅ M15.6 — String interpolation

Desugared during parsing: `"hello #{x} world"` becomes
`text_concat "hello " (text_concat (show x) " world")`. Lexer fixed to produce
fragment lists for interpolated text; parser sub-parses interpolation expressions
and builds the `text_concat`/`show` chain. Requires `Show` and `text_concat` in
scope at the use site. 3 new tests in `tests/bootstrap/test_programs.py`.

### ✅ M15.7 — GLS compiler parity (complete: 7a–7f)

GLS self-hosting compiler updated to handle surface syntax features:
- **M15.7a** Type aliases: `parse_type_decl_body` detects non-ADT type decls (`type Foo = Bar`,
  `type Nat : builtin`), skips them via `skip_to_decl_boundary`, emits no-op DLet.
  `type Byte = Nat` added to Compiler.gls as validation.
- **M15.7b** List/Cons syntax: `TkLBracket`, `TkRBracket`, `TkColonColon` tokens added.
  `[a, b, c]` in expressions → nested `Cons(a, Cons(b, Cons(c, Nil)))`.
  `[]` in patterns → `ArmCon(Nil, [])`. `h :: t` in patterns → `ArmCon(Cons, [h, t])`.
- **M15.7c** Or-patterns: `arm_con_upper_pe` recursively parses `| Con1 | Con2 → body`,
  returns `List MatchArm` (body duplicated per alternative). `parse_match_arms_pe` uses `append`.
- **M15.7d** Guards: `| Con fields if guard → body` detected in `arm_con_upper_pe`.
  Guard body encoded as `EIf guard body (EVar 0)` sentinel. `parse_match_expr_pe`
  post-processes: `replace_guard_sentinels` replaces `EVar 0` with
  `match __gs { remaining_arms }`, wraps scrutinee in `let __gs = scrut`.
- **M15.7e** String interpolation: `TkInterp` token added to Token type. `lex_scan_interp_go`
  scans text with `#{...}` detection, `find_close_brace` tracks brace depth,
  `has_interp` fast-checks for interpolation before re-scanning.
  `desugar_interp_frag`/`desugar_interp_chain` build `text_concat`/`show` expression chains.
  Text encoding fixed: `TkText` now carries `MkPair tlen tnat` (byte length preserved),
  parser produces `EApp (ENat tlen) (ENat tnat)` for proper PLAN text values.
- **M15.7f** Records: record table (`rt`) pre-scanned from token stream via `collect_record_types`,
  threaded through ~18 parser functions. Record expressions (`{ x = 1, y = 2 }`) desugar via
  `rt_lookup` + `rt_reorder_exprs` to constructor application. Record updates (`base { x = 3 }`)
  desugar via `rt_lookup_subset` to match+rebuild. Record patterns (`| { x = px }`) desugar via
  `rt_reorder_pat_vars` to `ArmCon`. ~17 new helper functions, 9 new tests.

17 tests in `tests/compiler/test_m15.py`.

**Deferred past 1.0:** macros/quotation, contract solver tiers, module export
enforcement, package declarations.

---

## ✅ M16 — Pin-based module loading

Goal: modules are pins in the persistent DAG, not inlined definitions. Programs
reference upstream dependencies by BLAKE3 hash; the VM lazily materializes pin
content on demand. This is the PLAN-native analogue of Nock 12 scry-based
namespace loading (as used in Hoon's Shrine).

### ✅ M16.1 — Pin manifest format + PinId computation

`bootstrap/pin.py`: `compute_pin_id(plan_value)` serializes via `save_seed()`,
hashes with BLAKE3-256 (via `blake3` pip package), returns hex string.
`build_manifest(compiled, module)` maps FQ names → PinId hex. JSON roundtrip
via `save_manifest`/`load_manifest`. 9 tests.

### ✅ M16.2 — Pin-wrapped compilation

`build_modules(sources, pin_wrap=True)` wraps each compiled value in `P(value)`
after codegen. `emit_pinned(compiled, module, out_dir)` emits per-definition
seed files + manifest JSON. 5 tests.

### ✅ M16.3 — Prelude as pinned DAG

`bootstrap/build_prelude.py` compiles all 8 Core modules with `pin_wrap=True`,
produces per-module manifests (`prelude/manifest/Core.Nat.json` etc.) and
combined manifest (`prelude/manifest/prelude.json`). 110 pins across 8 modules.
Deterministic recompilation verified. 7 tests.

### ✅ M16.4 — Lazy pin resolution in harness

`dev/harness/pin_store.py`: `PinStore` class wrapping a directory of seed files.
`save(plan_value)` → PinId, `resolve(pin_id)` → PLAN value with in-memory
caching. 5 tests.

### ✅ M16.5 — CI validation + integration

Integration tests validate the full pin cycle: compile → pin → manifest → store
→ resolve → evaluate. Pin-wrapped and plain builds produce equivalent content.
All manifest PinIds are valid. 3 tests (in `test_pin_prelude.py`).

---

## ✅ M17 — Glass IR emission

Goal: emit Glass IR fragments from the bootstrap compiler per spec/01-glass-ir.md.
Covers the subset achievable without type inference or debugger: FQ names, pin hashes,
explicit dictionary args, fragment structure, SCC groups, round-trip verification.

### ✅ M17.1 — Glass IR renderer

AST-based renderer in `bootstrap/glass_ir.py`: `render_fragment()`, `render_expr()`,
`render_pattern()`, `render_decl()`, `render_module()`. Outputs Glass IR with
Snapshot/Source/Budget header, FQ names, `[pin#hash]` annotations. 20 tests.

### ✅ M17.2 — Pin declarations and dependency rendering

`collect_decl_deps()` walks resolved AST to find cross-module references.
`collect_pin_deps()` maps them to PinIds from manifests. Fragments include
`@![pin#hash] Module.Name` pin declarations. 5 tests.

### ✅ M17.3 — SCC group rendering

`render_scc_group()` emits `@![pin#hash] { ... }` grouped blocks for mutual
recursion. `Compiler.scc_groups` metadata added to codegen. 2 tests.

### ✅ M17.4 — Round-trip verification

`verify_roundtrip()` recompiles resolved AST and compares PLAN output against
original compilation. Bootstrap-level round-trip (AST → compile → compare).
Full Glass IR text round-trip deferred to self-hosting compiler. 4 tests.

### ✅ M17.5 — Prelude Glass IR emission + CI

`bootstrap/build_prelude.py --glass-ir` emits per-definition Glass IR fragments
to `prelude/glass_ir/`. 64 fragments across 8 modules. Round-trip verified for
Core.Combinators and Core.Nat. 7 tests.

---

## ✅ M18 — Type-annotated Glass IR

Goal: every `let` declaration in emitted Glass IR carries its inferred type
signature, making fragments useful to IDEs and LLMs.

### ✅ M18.1 — Standalone type serializer

`pp_type(MonoType) -> str` and `pp_scheme(Scheme) -> str` in `bootstrap/typecheck.py`.
Work on post-generalization types without a TypeChecker instance. Handle function
arrows, type applications, tuples, effect rows, bound variables. 10 tests.

### ✅ M18.2 — Wire TypeEnv into Glass IR renderer

`render_fragment()`, `render_decl()`, `render_module()` accept optional `type_env`
parameter. When present, `let` declarations render `: Type` annotations.
`build_prelude.py` calls `typecheck()` per module during Glass IR emission. 6 tests.

### ✅ M18.3 — Constraint annotations

`Scheme` extended with `constraints` field (previously stripped by `ast_to_scheme`).
`pp_scheme` renders constraints with `⇒` arrow. Glass IR fragments for constrained
definitions show their constraints. 7 tests.

### ✅ M18.4 — Prelude integration + type fixes

`typecheck()` gains `prior_type_env` parameter for cross-module type accumulation.
Bare type names resolved to FQ equivalents in `ast_to_mono`. List tycon resolution
for pattern/expression inference. All 8 prelude modules typecheck (89 type entries).

Prelude type fixes: replaced Bool/Nat type puns (`is_zero(nat_lt ...)`) with
type-correct patterns (`nat_gte`, `if/then/else`). Added `Core.Nat.nat_gte`.
5 tests.

---

## ✅ M19 — Pattern match exhaustiveness checking

Implements Maranget's usefulness algorithm (spec/03-exhaustiveness.md) as a new
module `bootstrap/exhaustiveness.py`. Integrated at typecheck time after pattern
type inference.

### ✅ M19.1 — Constructor registry + exhaustiveness module

`TypeChecker.type_constructors` maps FQ type → [(con_name, arity)]. Populated
during `_register_decl_type` and `_init_builtins` (Bool). `bootstrap/exhaustiveness.py`
implements the full Maranget algorithm: pattern matrix, constructor specialization,
default matrix, sigma completeness. Handles algebraic types, Bool, Nat/Text (infinite),
tuples, nested patterns. 26 tests.

### ✅ M19.2 — Wire into typechecker

`_check_exhaustiveness()` called after match inference in `TypeChecker.infer`.
Non-exhaustive matches raise `TypecheckError`. `typecheck()` gains
`prior_type_constructors` parameter for cross-module support.

### ✅ M19.3 — Redundancy warnings

Redundant arms detected via usefulness predicate on preceding rows. Emitted as
`warnings.warn()` (not errors). 4 redundancy tests.

### ✅ M19.4 — Validation

All 1175 tests pass. No prelude or existing test matches were non-exhaustive.

---

## ✅ M20 — 0.999 syntax: where clauses, operator sections, export lists

Three ergonomic features completing the surface syntax for the 0.999 release.

### ✅ M20.1 — where clauses

`where` added to `KEYWORDS` in the lexer. Parser desugars `expr where { a = e1 ; b = e2 }` into nested `ExprLet` nodes — no scope or codegen changes needed.

### ✅ M20.2 — Operator sections

Parser detects `(op)`, `(op expr)`, and `(expr op)` inside the `(` branch of `_parse_atom_expr` and desugars to lambdas with synthetic parameter names `__sec_a`/`__sec_b`. Left sections use backtracking to distinguish from grouping parens.

### ✅ M20.3 — Export list enforcement

`DeclExport` AST node stores bare names from `export { ... }`. Scope resolver records the export list, then filters `module_exports` after all declarations are collected. Cross-module `use` checks the exporting module's export set and raises `ScopeError` for non-exported names. Without an export declaration, all names are exported (backward compatible).

### ✅ M20.4 — Validation

20 tests: 3 where-parse, 4 where-eval, 5 operator-section-parse, 1 operator-section-eval, 1 export-parse, 6 export-scope (including cross-module positive and negative).

All 1210 tests pass.

---

## 1.0

All of the above complete. Acceptance criteria:

- Full Gallowglass surface syntax (`spec/06-surface-syntax.md`) compiles correctly
- Core prelude (`prelude/src/Core/`) fully implemented and split across modules
- Effect handlers, typeclasses, and mutual recursion all working and self-hosted
- The `Data.Csv` example from `spec/06-surface-syntax.md §15` compiles and runs
- Prelude published as pinned DAG; user programs reference pins, not inlined defs (M16)
- Glass IR emission for prelude with round-trip verification (M17)
- CI passes: Python harness + planvm seed loading + M8.8 Path A equivalent for 1.0 compiler

---

## Post-1.0

These are not on the critical path to 1.0 and are deferred explicitly.

### Rust VM
Dual-VM CI: running programs on both planvm and the Rust VM, detecting divergence.
Primary runtime post-1.0. Designed with snapshot retention and debugger needs from
the start. Built after self-hosting because building it first would mean building
against speculative usage patterns. See `DECISIONS.md §"Why a purpose-built Rust VM?"`.

### Debugger and Glass IR
Glass IR as a live view over running programs, snapshot queries, effect injection.
Full `spec/01-glass-ir.md` implementation. Requires the Rust VM (snapshot retention
is a VM concern). The spec is complete; the implementation waits for the VM.

### Contract system
Pre/post contracts with tiered discharge: syntactic (always), built-in procedures
(linear arithmetic, list length), runtime checks (degraded), SMT backend (optional).
See `DECISIONS.md §"Why three tiers?"`. The contract syntax is in the parser from M9
onward; discharge is initially `Deferred(NoSolver)` for everything. The solver tiers
are added incrementally post-1.0.

### Jet registry and optimizer
The jet-matching optimizer written in PLAN itself, as described in `DECISIONS.md
§"Why does jet matching logic live in the optimizer?"`. Requires a stable set of
jets to optimize against — i.e., a working prelude and several real programs.

### Pattern matching codegen: Hd/Sz/CaseN/Ix convention
The current codegen uses opcode 3 (`Case_`) directly for all pattern matching. Sol
confirmed this is "extremely heavy" — the intended convention is `Hd`/`Sz` for branch
identification, `CaseN` jets for switching, and `Ix` for field extraction. Migrating
the codegen (both Python bootstrap and GLS self-hosting compiler) to this convention
is a post-1.0 correctness-preserving optimization. See `DECISIONS.md §"Why Case_ for
pattern matching now?"`.

### Text/Bytes high-bit length encoding
Current encoding uses a plain `(byte_length, content_nat)` pair. Sol recommends using
a high bit to encode the length for efficiency (avoids a separate length field for
small strings). Migration requires updating the bootstrap emitter, prelude, and any
code that introspects Text/Bytes representation. Deferred until the encoding is
finalized upstream.

### VM I/O integration (M8.8 Path A equivalent for 1.0)
**Superseded by Phase G (below).** Original framing assumed a stable `cog`-wrapping
API in xocore-tech/PLAN; per Sol that approach is gone. The replacement is to wrap
`Compiler.main` against Reaver's RPLAN ABI (`runReplFn` + `Input`/`Output`/etc. named
ops in `vendor/reaver/src/hs/Plan.hs op 82`).

---

## Phase G — RPLAN self-host validation on Reaver

**Status:** scoped, not started. Successor to the original M8.8 Path A gate.
**Acceptance:** gallowglass-compiled `Compiler.main` runs under Reaver, reads its
own source on stdin, writes Plan Assembler on stdout, and the output is
byte-identical to what the bootstrap-compiled `Compiler.main` produces under
the BPLAN harness for the same input.

### Why this is a separate arc

The Reaver migration arc (Phases 0–F, PRs #47–#53) made Reaver a viable runtime
for gallowglass-emitted programs. It did not retarget `Compiler.main`'s I/O
shape — that's still `Bytes → Bytes`, which is harness-evaluable but not
runnable as a Reaver process. Phase G is the I/O-shape change.

### Reaver's RPLAN surface

Reaver's `op 82` (= `strNat("R")`) dispatches the RPLAN named ops, defined in
`vendor/reaver/src/hs/Plan.hs` and surfaced via the `(rplan ...)` macro in
`vendor/reaver/src/plan/boot.plan`:

| Name | Arity | Purpose |
|---|---|---|
| `Input` | 1 | Read bytes from stdin (arg = max length nat) |
| `Output` | 1 | Write bytes to stdout |
| `Warn` | 1 | Write bytes to stderr |
| `ReadFile` | 1 | Read a file by path |
| `Print` | 1 | Pretty-print a PLAN value |
| `Stamp` | 1 | mtime of a path |
| `Now` | 1 | wall-clock |

The Haskell driver loads a `.plan` module, then if a third CLI arg is given,
treats that name as the entry point and applies it to the rest of the args:
`runReplFn args fun = ... evaluate $ force $ (fun %) $ array $ map (N . strNat) $ args`.

### Concrete deliverables

1. **Source-level RPLAN bindings.** Add an `external mod Reaver.RPLAN { ... }`
   module that mirrors the `op 82` named-op set. Bootstrap codegen registers
   each as a BPLAN-style Pin'd Law that delegates to `((P("R")) ("Name" args))`,
   parallel to how `Core.PLAN.*` works for op 66 (see
   `bootstrap/codegen.py::_make_bplan_prim` and `bootstrap/bplan_deps.py`).
2. **Re-shape `Compiler.main`.** Current shape: `Bytes → Bytes`. New shape: a
   procedure that calls `Input` to read source, runs the lex/parse/scope/codegen/
   emit pipeline, and calls `Output` with the Plan Assembler result. The
   pipeline interior stays `Bytes → Bytes`; only the I/O wrapper changes.
3. **`tests/reaver/test_selfhost.py`.** Compile `Compiler.gls` to `compiler.plan`,
   run under Reaver against a fixture source, capture stdout, compare against
   the BPLAN-harness output via `Compiler.emit_program`. Byte-identical or
   the test fails.
4. **CI gate.** Add the self-host test to the existing `reaver` job in
   `.github/workflows/ci.yml` (or a separate job if its runtime is large).

### Risk surface

- **RPLAN is tentative, not frozen** (Sol, 2026-04-30). RPLAN sits a tier
  *above* BPLAN's "drift expected" risk — Sol explicitly flagged it as
  "tentative maturity." Names, arities, and the calling shape may all
  change. Phase G should plan for this:
  - Add `tests/sanity/test_rplan_deps.py` mirroring `test_bplan_deps.py`,
    asserting every RPLAN op gallowglass uses still exists at the right
    arity in `Plan.hs`. CI fires on the next vendor.lock bump that drifts.
  - Keep the `Compiler.main` I/O layer thin (a small wrapper around the
    pure pipeline) so an upstream RPLAN re-shape is bounded to ~50 LoC.
  - Pin `vendor.lock` deliberately when starting Phase G; don't auto-bump
    during the implementation window.
- **Performance.** The compiled compiler running under pure-PLAN evaluation
  may be slow even with BPLAN jets active. May need additional jet
  registrations or alternate paths.
- **Trace output vs `Output` bytes.** Reaver's `Trace` writes to stderr and
  prints via `showVal`, which mangles byte-range nats. The self-host test
  must check `Output`'s stdout bytes literally, not Trace output. (Phase F's
  `tests/reaver/test_smoke.py` ran into this; see PR #53 for the mitigation
  of using values >255 in test fixtures.)
- **Canonical Elim wire form is upstream-pending.** The canonical CC (per
  Sol) is `(<0> (2 p l a z m o))` — all pinned nats arity 1, dispatch on
  inner App head. Reaver's runtime doesn't yet implement this; we currently
  emit a bare `Elim` symbol that resolves via `boot.plan`'s BPLAN binding.
  See `DECISIONS.md §"The canonical 3-opcode ABI"` for the full picture.
  When Reaver lands the canonical CC upstream, `bootstrap/emit_pla.py`'s
  Elim translation needs to update accordingly. This isn't a Phase G
  blocker but is on the same flight path.

### Estimated effort

1–2 weeks. The bulk of the work is the RPLAN bindings and the `Compiler.main`
I/O re-shape. The test infra inherits from Phase F's `tests/reaver/`. No
upstream changes needed — Reaver's RPLAN is stable today.

### Why this closes 1.0

After Phase G lands, the loop is closed: gallowglass compiles its own source,
the resulting compiler runs as a Reaver process, processes input on stdin,
emits Plan Asm on stdout, and produces the same bytes as the harness
implementation. Byte-identical self-hosting against the canonical runtime is
the 1.0 acceptance criterion that's been outstanding since alpha.

---

## What is NOT on this roadmap

- **Dependent types**: explicitly out of scope. See `DECISIONS.md §"Why algebraic
  effects with row typing rather than dependent types?"`.
- **Garbage collector**: PLAN's heap is a persistent Merkle-DAG; there is no
  allocation/collection cycle in the traditional sense.
- **FFI beyond External**: the `External` effect and `external mod` cover the VM
  boundary. A traditional C FFI is not planned.
