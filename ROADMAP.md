# Gallowglass Roadmap

**Last updated:** 2026-04-06
**Current status:** Alpha — M8 complete (Path B). M9.1–9.4 complete. M10.1–10.7 complete. M11.1–11.5 complete. M12 + M12.1 complete. M12.2 (GLS DEff/EHandle/EDo), M12.3 (superclass constraints), M12.4 (GLS DeclUse), M12.5 (Data.Csv E2E) complete. 890 tests passing.

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

### M14.6 — Cross-module prelude refactor

Prelude modules use `use` imports (M12) instead of inlining dependencies. Proper
dependency chain: Combinators → Nat → Bool → Option → List → Text.

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

### ✅ M15.7 — GLS compiler parity (partial: 7a–7d complete, 7e–7f deferred)

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
- **M15.7e** String interpolation: deferred (byte-level lexer modification).
- **M15.7f** Records: deferred (requires field-name-to-type lookup table threading).

17 tests in `tests/compiler/test_m15.py`.

**Deferred past 1.0:** macros/quotation, contract solver tiers, module export
enforcement, package declarations.

---

## M16 — Pin-based module loading

Goal: modules are pins in the persistent DAG, not inlined definitions. Programs
reference upstream dependencies by BLAKE3 hash; the VM lazily materializes pin
content on demand. This is the PLAN-native analogue of Nock 12 scry-based
namespace loading (as used in Hoon's Shrine).

### M16.1 — Pin manifest format

Define the **pin manifest**: a map from fully-qualified name → PinId (BLAKE3-256
hash). The prelude build produces a manifest alongside its seeds. Format aligns
with `SPEC.md §8.4` package manifest (`depends { Gallowglass.Core at pin#... }`).

### M16.2 — Compiler pin-reference emission

When the compiler encounters a `use`-imported name whose PinId is known from a
manifest, emit `P(hash)` instead of inlining the law body. The emitted seed
contains hash references to upstream pins, not copies of their content.

### M16.3 — Prelude as pinned DAG

Compile the full prelude (`Core.Combinators` through `Core.Result`) and publish
each definition as an independent pin. Produce a prelude manifest. Verify that
user programs compiled against the manifest produce valid seeds with pin
references that the VM resolves correctly.

### M16.4 — Lazy pin resolution in harness

Extend the Python dev harness to support lazy pin lookup: when evaluation forces
a `P(hash)` whose content is not yet loaded, fetch it from a pin store (local
directory of seed files). This enables local testing of pin-based seeds without
requiring the full VM infrastructure.

### M16.5 — CI validation

CI job that compiles a test program against the pinned prelude manifest, emits a
seed with pin references, loads it in planvm, and verifies correct execution.
Demonstrates the full lazy-load cycle: compile → emit pin refs → VM fetches pins
on demand → correct result.

**Why now (pre-1.0):** Without pin-based loading, every seed bundles its entire
transitive dependency closure. This is acceptable for bootstrap but makes seed
sizes grow combinatorially as the prelude expands. Pin-based loading is also a
prerequisite for the package system (`SPEC.md §8.4`) and for any multi-cog
deployment where cogs share a common prelude in the persistent store.

---

## 1.0

All of the above complete. Acceptance criteria:

- Full Gallowglass surface syntax (`spec/06-surface-syntax.md`) compiles correctly
- Core prelude (`prelude/src/Core/`) fully implemented and split across modules
- Effect handlers, typeclasses, and mutual recursion all working and self-hosted
- The `Data.Csv` example from `spec/06-surface-syntax.md §15` compiles and runs
- Prelude published as pinned DAG; user programs reference pins, not inlined defs (M16)
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
Once the direct side-effects + virtualization API stabilizes, wrap `Compiler.main` to
read source from the VM's I/O channel and write Plan Assembler to the output channel.
Run the compiled compiler on its own source via the VM and assert byte-identical output.
This is the definitive planvm-executed self-hosting gate.

---

## What is NOT on this roadmap

- **Dependent types**: explicitly out of scope. See `DECISIONS.md §"Why algebraic
  effects with row typing rather than dependent types?"`.
- **Garbage collector**: PLAN's heap is a persistent Merkle-DAG; there is no
  allocation/collection cycle in the traditional sense.
- **FFI beyond External**: the `External` effect and `external mod` cover the VM
  boundary. A traditional C FFI is not planned.
