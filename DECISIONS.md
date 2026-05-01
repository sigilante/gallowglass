# Gallowglass Design Decisions

This document records the rationale behind non-obvious design choices. When something in the codebase seems surprising or you want to understand *why* before changing it, look here first.

Entries are grouped by concern. Within each group, decisions are ordered from most foundational to most specific.

---

## VM Target

### Why PLAN (xocore-tech/PLAN) rather than WASM, JVM, or a custom VM?

PLAN was designed as a compile target for functional languages. Its properties align with Gallowglass's requirements in ways no existing VM does:

- Graph-reduction execution model (not stack-based) matches our rejection of concatenative semantics
- Merkle-DAG heap with content-addressed pins gives us structural sharing, O(1) equality, and stable identities for free
- Four constructors (Pin, Law, App, Nat) and five opcodes is a small, formally specified target the LLM can reason about completely
- Jet matching by content hash rather than by subject position means jets are portable across serialization and network transit
- Pure by construction — effects are a language-level concern, not a VM concern
- Language-agnostic by design — we are exactly the intended use case

WASM was considered but rejected: it imposes semantic compromises (stack machine, no native content-addressing, no effect enforcement at the VM level) that would require constant workarounds.

### Why use `Elim` (opcode 2) for pattern matching rather than `Hd`/`Sz`/`CaseN`/`Ix`?

> **Renaming note (2026-04-30):** in xocore-tech/PLAN's older 5-opcode ABI this primitive was called `Case_` and lived at opcode 3. The canonical Reaver ABI renames it to `Elim` and renumbers it to opcode 2 (the only non-{Pin,Law} opcode). The argument shape and semantics are unchanged: 6-arity dispatch on the constructor of the scrutinee. References to `Case_` (opcode 3) elsewhere in this document predate the migration; treat them as referring to the same operation.

The current codegen uses the Elim opcode directly for all pattern matching. This is the simplest mapping from Gallowglass match expressions to PLAN, but Sol (PLAN author) confirmed it is "extremely heavy."

The preferred convention is:
- `Hd` and `Sz` to identify which constructor branch was taken (lighter inspection)
- `CaseN` jets to switch on N constructor tags
- `Ix` to extract individual fields by index

This approach is more efficient because Elim constructs a 6-arity dispatch closure per match site, whereas the `Hd`/`Sz`/`CaseN`/`Ix` approach targets existing jets without constructing dispatch closures.

**Why not now?** The Elim encoding is correct and produces valid PLAN. Migrating to `Hd`/`Sz`/`CaseN`/`Ix` is a correctness-preserving optimization that requires significant changes to both the bootstrap codegen and the GLS self-hosting compiler. It is tracked as a post-1.0 item to be done alongside the jet registry and optimizer work, when the full set of `CaseN` jets is stable.

### Why a purpose-built Rust VM in addition to xocore?

The Rust VM provides dual-VM CI: running the same program on both VMs and detecting divergence is the primary correctness mechanism for jet verification. It also becomes the primary runtime post-1.0, designed with snapshot retention and the debugger's needs in mind from the start.

The Rust VM is deferred until after the self-hosting compiler. Building it first would mean building against speculative usage patterns. Building it after means building against real programs with real jet candidates.

### Why BLAKE3-256 as the hash algorithm?

BLAKE3 is faster than SHA-256, parallelizable, Merkle-tree-structured internally (consistent with the DAG heap philosophy), has first-class Rust support, has Haskell bindings for xocore interop, and is public domain licensed. PLAN's spec deliberately leaves the hash algorithm as an implementation detail. BLAKE3-256 is Gallowglass's canonical choice, documented explicitly so all implementations agree.

The hash input canonicalization — how a PLAN value is serialized to bytes before hashing — must match xocore's implementation exactly for PinIds to be portable between VMs. This is a first-class CI test.

---

## Upstream PLAN authority

### Why Reaver's Haskell sources are the canonical base truth (2026-04-30)

Per Sol (PLAN author): **the canonical specification of the Plan Assembler text format
and the PLAN runtime semantics is the Haskell implementation in `vendor/reaver/`,
specifically:**

- `vendor/reaver/src/hs/PlanAssembler.hs` — Plan Assembler text format (parser,
  `expand1`, `lawExp`, `compileExpr`, `loadAssembly`, `processForm`).
- `vendor/reaver/src/hs/Plan.hs` — PLAN runtime semantics (eval, exec, opcode
  dispatch, BPLAN/RPLAN named-op dispatch).

Other documents in `vendor/reaver/` — `doc/plan-spec.txt`, `doc/reaver.md`,
`note/arch/*.md` — are **explanatory or aspirational**. They were committed at various
points and may be LLM-generated from notes; they do not bind the implementation.
When derived documentation disagrees with the `.hs` files, the `.hs` files win.

**Stability tiers (per Sol):**

| Layer | Stability | Implication |
|---|---|---|
| **PLAN proper** (4 ctors, opcodes 0–2 = Pin/Law/Elim) | Frozen | Build against this freely. |
| **Plan Asm text format** | Frozen | `spec/07-seed-format.md §13` is durable once derived correctly. |
| **BPLAN** (`op 66` named ops in `Plan.hs`) | Drift expected | Audit on each `vendor.lock` bump. |
| **RPLAN** (`op 82` named ops) | **Tentative, not frozen** (per Sol, 2026-04-30) | Higher churn risk than BPLAN; assume any RPLAN op may rename or change shape. The canary discipline in `bootstrap/bplan_deps.py` should extend to RPLAN-using code (Phase G). |

Gallowglass's `bootstrap/bplan_deps.py` enumerates the BPLAN names+arities we depend on
and is sanity-tested against `vendor/reaver/src/hs/Plan.hs`. This is the canary for vendor bumps.

### Why a `vendor.lock` rather than a submodule? (2026-04-30)

`vendor/` was historically gitignored and is populated via `tools/vendor.sh` from
`vendor.lock` at the repo root. The lock file records each upstream's pinned SHA and
clone URL.

This was chosen over git submodules because:
- `vendor/` was already gitignored when the dependency was introduced; submodules would
  have required reorganizing established workflow.
- Bumping a single SHA in `vendor.lock` is a smaller, more readable diff than a
  submodule pointer move.
- `tools/vendor.sh verify` runs in CI and locally to catch pin drift; this is
  effectively what submodule consistency checks would buy us, with less ceremony.

If the vendor count grows or the tool starts to need richer semantics, revisit and
move to submodules.

### Why our `spec/04`, `spec/07` are derived docs, not authoritative

Earlier sessions of this project authored `spec/04-plan-encoding.md` and
`spec/07-seed-format.md §13` as if they were independent specifications. They are not —
they are guides to reading the `.hs` files. Sol has confirmed the spec/07 §13 text
format description was likely LLM-extracted from PlanAssembler.hs at an earlier point.

Claims in our specs about upstream behavior must cite a specific line range in
`vendor/reaver/src/hs/<file>.hs` so future readers can verify mechanically.
When `vendor.lock` is bumped, re-run the round-trip tests in `tests/reaver/`; if any
pass, our derived docs are still accurate.

### Why Phase G (RPLAN self-host) is a separate arc from the migration (2026-04-30)

The Reaver migration arc (Phases 0/A/B+C/D/E/F, PRs #47–#53) made Reaver a viable
runtime for gallowglass-emitted programs by retargeting the codegen and emitter
to the canonical 3-opcode + BPLAN-named ABI. **It did not retarget
`Compiler.main`'s I/O shape.** That's a separate concern with its own scope:

- The migration is about *what shape gallowglass output takes* on the wire.
- Phase G is about *how `Compiler.main` interacts with the host process*: stdin
  reading, stdout writing, exit handling. RPLAN's `Input` / `Output` / etc.
  named ops (`op 82` in `vendor/reaver/src/hs/Plan.hs`) are the substrate.

Splitting the arcs has two payoffs:
1. The migration's review surface stayed narrow — every PR was a focused codegen
   or harness change. Mixing in an I/O re-shape would have doubled the diff and
   the bisect surface.
2. Phase G's risk profile is meaningfully larger (performance under pure-PLAN
   evaluation; sensitivity to RPLAN ABI churn). Containing it lets the migration
   ship as 0.99999-beta independently.

The full Phase G scope and acceptance criteria live in `ROADMAP.md §"Phase G —
RPLAN self-host validation on Reaver"`. Future sessions picking up the work
should start there.

### Why XPLAN compatibility is being abandoned (2026-04-30)

xocore-tech/PLAN's xplan runtime supports an older 5-opcode ISA (Pin/MkLaw/Inc/Case_/Force
at opcodes 0–4). The canonical PLAN ABI per Sol has only 3 opcodes (Pin/Law/Elim at 0–2),
with Inc, Force, and the BPLAN intrinsics dispatched by name+arity through `(#pin "B")`.
Sol confirms XPLAN keeps the legacy 5-opcode dispatch for compatibility with the existing
Sire-based toolchain and is not the forward-going target.

Gallowglass is migrating to the canonical 3-opcode ABI with BPLAN-named primitives. The
old `tests/planvm/` suite (xocore seed-loading gate) is being archived rather than
maintained; xocore is no longer a Gallowglass deployment target.

### The canonical 3-opcode ABI (target for Phase B/C, 2026-04-30)

**The target ABI**, sourced from `vendor/reaver/src/hs/Plan.hs` at
`vendor.lock` SHA `f72fe24`:

| Opcode | Name | Arity | Plan.hs anchor |
|---|---|---|---|
| 0 | Pin  | 1 | `op 66 ["Pin", i]` (line 333) — also reachable via `<0> v = pin v` |
| 1 | Law  | 3 | `op 66 ["Law", a, m, b]` (line 334) — note the runtime stores `a+1`, exposing surface arity `a` |
| 2 | Elim | 6 | `op 66 ["Elim", p, l, a, z, m, o]` (line 335) — formerly `Case_` at xocore opcode 3 |

Inc, Force, arithmetic, and introspection are all BPLAN named primitives —
Pin'd Laws of the right name and arity that the runtime dispatches in the
`op 66` cases. They are not opcode pins.

The full target encoding for each xocore opcode:

| Old (xocore) | New (Reaver canonical) |
|---|---|
| `<0>` (Pin)    | `<0>` — unchanged opcode pin, OR reference to BPLAN `Pin` Law (arity 1) |
| `<1>` (MkLaw)  | `<1>` — unchanged opcode pin, OR reference to BPLAN `Law` Law (arity 3) |
| `<2>` (Inc)    | reference to BPLAN `Inc` Pin'd Law (arity 1) |
| `<3>` (Case_)  | `<2>` (Elim) — opcode pin renumbered |
| `<4>` (Force)  | reference to BPLAN `Force` Pin'd Law (arity 1) |

For Pin and Law (opcodes 0 and 1), gallowglass codegen will go via the BPLAN
named-Law shape rather than the opcode pin shape. Reasoning: Reaver's
runtime dispatches `op 66` named ops today but does not yet implement
`exec (P _ _ (N o))` for `o ∈ {0,1,2}` — the canonical opcode-pin path will
land upstream eventually, but the BPLAN-named path works now and is what
`vendor/reaver/src/plan/boot.plan` uses for its own bootstrap. See
`bootstrap/bplan_deps.py` for the registered names+arities.

**Pattern matching uses the renumbered Elim opcode** (canonical 2, formerly
xocore 3). The semantics are identical — the C-rules in
`vendor/reaver/doc/plan-spec.txt` match xocore's Case_ exactly: pin → `p i`,
law → `l a m b`, app → `a f x`, nat 0 → `z`, nat n>0 → `m (n-1)`.

**Canonical wire form for Elim** (Sol clarified, 2026-04-30): the new ABI's
unified calling convention requires **all pinned nats to have arity 1** — so
`<0>`, `<1>`, `<2>` etc. all saturate with exactly one argument, which
must itself be a saturated App carrying the actual op identity at its head.
The canonical Elim form is therefore:

    (<0> (2 p l a z m o))

— `<0>` saturates with one arg (the App `(2 p l a z m o)`). The runtime
evaluates the inner App, recognises N(2) at the head with 6 args saturating
opcode 2, and dispatches Elim. **Not** `(<2> p l a z m o)` — under the
new CC, `<2>` is also arity 1 and would saturate after one arg.

The point of the unified CC is that XPLAN / BPLAN / Reaver all share the
same evaluation rule (and the wrappers are easy to recognise and optimise
even in a minimal implementation); only the actual syscall behaviour
differs across runtimes.

**What gallowglass emits today.** Reaver's runtime does not yet implement
the canonical `(<0> (...))` dispatch — its `Plan.hs` only has `op 66`
(BPLAN) and `op 82` (RPLAN) cases. So our `bootstrap/emit_pla.py` emits
the bare symbol `Elim`, which Reaver resolves via `boot.plan`'s
`(bplan (Elim p l a z m o))` binding to a BPLAN-named primitive. This is
a transitional shape: it works today, but does not match the canonical
wire form Sol described. When Reaver implements the unified CC upstream,
`emit_pla.py`'s Elim translation should be revisited (one-line change).

---

## Type System

### Why algebraic effects with row typing rather than monads or Haskell-style typeclasses?

Monads (Haskell's approach) make effects invisible in the type at the call site — you can't tell from a function's type what effects it performs without reading its implementation or documentation. For LLM generation, this means the model can't locally verify effect compatibility. Row typing makes effects locally visible in every signature: `{IO, Exn IOError | r} ReturnType`. The LLM reading or writing this signature sees the complete effect footprint immediately.

Dependent types were considered but rejected. A type that is a proof obligation requires the LLM to simultaneously generate a proof term during code generation. We want the type system to assist generation, not make each step harder.

H-M was considered but rejected: effects are structurally invisible in H-M, requiring monads or effect libraries that leak architectural decisions everywhere.

Reference: Koka-style handlers with Rust-trait-style visible bounds in signatures.

### Why Koka-style handlers rather than Frank-style?

Koka handlers have explicit `resume` continuations — you can see exactly where and how control returns. Frank handlers are cleaner operationally but the implicit continuation makes the handler boundary less crisp. For jet mapping, a Koka-style handler is a named, bounded scope with an explicit signature — exactly the structure needed for a jet contract. For LLM generation, explicit `resume` reduces a significant source of generation uncertainty.

The `once` modifier for shallow (single-shot) handling replaces the Koka deep/shallow distinction, reducing cognitive surface area while preserving the jet-mapping clarity.

### Why is `Abort` outside the effect system entirely?

`Abort` signals that the program's own invariants are violated. `Exn` signals that an expected failure condition occurred. These are semantically distinct: an exception says "something went wrong that the caller might handle," while a contract violation says "the program has reached a state that should be statically impossible." Conflating them (as Python does with `StopIteration`) causes interference — a handler could accidentally swallow a contract violation, and the effect row fails to distinguish correctness guarantees from failure modes.

`Abort` propagates to the VM's virtualization supervisor, not to any user handler. It is structurally unhandleable. This is the Python/StopIteration problem avoided by construction.

### Why nominal records rather than structural?

Structural records create typeclass coherence complications — two records with identical fields but different names would be the same type, making instance resolution ambiguous. With content-addressed coherence, nominal records give different PinIds to different named types even if their fields are identical. Unambiguous. Row-typed extensible records are deferred until the need is demonstrated.

### Why typeclasses with explicit dictionary elaboration in Glass IR?

Typeclasses give clean call sites for human programmers (and LLMs generating source). Explicit dictionaries in Glass IR give full visibility for LLM analysis — the dictionary is a value the LLM can see, name, and reason about. Both views are valid Gallowglass; Glass IR is the elaborated form of source.

Coherence via content-addressing rather than module ownership eliminates the orphan instance problem structurally: an instance's PinId is unique in the transitive closure of the program's dependency DAG.

---

## Effect and Contract System

### Why three tiers of contract discharge rather than a single SMT solver?

Z3 and CVC5 are large external dependencies with nondeterministic behavior across versions. A contract that discharges on one machine may not discharge on another. The three-tier approach solves this:

- Tier 0 (syntactic): always terminates, zero dependencies, covers trivially true/false cases
- Tier 1 (built-in decision procedures): linear arithmetic over Nat/Int, propositional logic, list length properties — covers the vast majority of practical contracts
- Tier 2 (runtime checks): everything else degrades gracefully to a runtime assertion
- Tier 3 (optional SMT backend): pluggable, Z3 or CVC5, for when static proof is needed

The critical property: **SMT discharge is an optimization, not a correctness requirement.** A program where all contracts are `Deferred` is correct — it just has runtime checks. This means SMT backend instability can never introduce bugs, only performance regressions in the form of unnecessary runtime checks.

### Why is `DeferralReason` a first-class type?

`Deferred(NoSolver)` and `Deferred(NonLinear)` have different implications for reasoning. The first means "this would discharge with an SMT backend." The second means "this involves nonlinear arithmetic which may not discharge even with a solver." An LLM analyzing a program needs to know which it is. A bare `Deferred` is epistemically less useful.

### Why must contracts be statable from the mathematical specification alone?

A contract that could only be written by someone who read the implementation adds no verification value. The contract and implementation could both be wrong in the same way and the contract would still pass. Contracts must express independent mathematical properties — functor laws, algebraic invariants, structural properties — that are checkable by a different mechanism than the implementation. See the tautology detector heuristic in the compiler.

---

## Syntax

### Why `=` as the spec/implementation separator?

Every construct in Gallowglass that has both a specification and an implementation uses `=` as the boundary. Type signatures and contract clauses are above `=`. The implementation body is below `=`. This is the most load-bearing structural separator in the language. It enforces the spec-implementation distinction visually and syntactically.

### Why is `\` reserved but unassigned?

`\` was the natural ASCII alternative for `λ` (Haskell convention). We use `fn` instead (Rust convention, more readable, less collision risk with `/`). Rather than leaving `\` as an unknown, it is explicitly reserved — the compiler rejects it with "reserved symbol, not yet assigned" — so it can be assigned later without a breaking change and without confusing anyone who encounters it.

### Why Unicode canonical forms with ASCII alternatives normalized at the lexer?

LLMs see and generate the canonical Unicode form. Humans typing in editors without Unicode input support can use ASCII alternatives. Normalizing at the lexer means all subsequent passes (parser, type checker, code generator) work only with canonical Unicode. Glass IR always shows canonical Unicode. The LLM's training surface is uniform.

### Why `fn` for lambda rather than `\`?

`\` creates a visual collision risk with `/` (integer division). `fn` is Rust convention and widely understood. More importantly, `fn` is more discoverable for programmers coming from Rust or Swift. `\` was always arbitrary (inherited from Haskell); `fn` is semantically descriptive.

### Why `//` for integer division rather than `div`?

Python convention for integer division is `//`, which is widely understood. The backtick-infix style (`x \`div\` y`) is less clean visually. `//` is short, unambiguous (true division uses `÷` / `/`), and doesn't collide with anything else.

---

## Data Representation

### Why the structural pair `(byte_length, content_nat)` for Text and Bytes?

The Nock `+unit` insight: structural pairing gives a representational distinction between `b""` (the pair `(0, 0)`) and `b"\x00"` (the pair `(1, 0)`) — both have content nat 0, but they're distinct because their length nats differ. Without this, trailing zero bytes are indistinguishable from absence (the C string problem). The pair is the minimal PLAN structure needed to represent this correctly.

### Why `byte_length` rather than `codepoint_count` in the Text pair?

Byte length is necessary for practical runtime performance — it determines the width of the underlying nat, drives efficient memory allocation, and is needed for UTF-8 boundary checks. Codepoint count and grapheme count are derivable and cacheable in the pin. The pair carries byte length; derived counts are computed on demand and cached.

### Why is `length` on Text defined as grapheme count by default?

Grapheme clusters are what users perceive as characters. Indexing by code point produces nonsensical results for emoji, combining characters, and multi-codepoint sequences. Most languages default to code point indexing, which is wrong for user-facing text. Gallowglass defaults to the correct thing (grapheme count) and provides explicit alternatives for byte and code point access.

---

## Numeric Tower

### Why no lawful `Eq` or `Add` instance for IEEE 754 floats?

`NaN ≠ NaN` breaks `Eq` reflexivity. `(a + b) + c ≠ a + (b + c)` breaks `Add` associativity. A language built around lawful typeclasses cannot provide instances that lie about their laws. `Float64` gets `ApproxEq` with `Tolerance { abstol, reltol }` — both components required, no single epsilon. The hardware float types are explicitly labeled as approximate. Code that needs to compare or add floats must be explicit about the approximation.

### Why `Tolerance { abstol, reltol }` rather than a single epsilon?

A single absolute epsilon treats `|0.001 - 0.002|` the same as `|1000.001 - 1000.002|`. For values near zero, the absolute floor dominates; for values far from zero, the relative tolerance dominates. The standard engineering formula `|a - b| ≤ abstol + reltol * max(|a|, |b|)` covers both regimes correctly. This is how numpy's `allclose` works. Neither component is optional.

### Why Posit as a first-class type even though hardware support doesn't exist yet?

RISC-V has a posit extension proposal. The overlap between "domains that care about numerical correctness" and "domains running RISC-V" (embedded, edge compute, safety-critical) is real and growing. More importantly, posits have lawful `Eq` (NaR = NaR, no signed zero anomaly) which IEEE 754 types cannot have. The type-theoretic story is cleaner. When hardware arrives, the jet fires automatically — the language doesn't change, only the VM's jet registry gets a new entry.

### Why `Abort` on fixed-width integer overflow rather than wrapping?

Wrapping silently produces wrong values. Saturation silently clamps. Both are hidden failures. `Abort` on overflow is visible — the type signature shows it, the failure is immediate and auditable. Wrapping and saturation are available as explicit operations (`wrap_add`, `sat_add`) for code that genuinely needs them. The LLM generating code with fixed-width integers sees the overflow risk in the type signature and must consciously choose a policy.

---

## Module System and Identity

### Why content-addressed identity (PinIds) rather than name-based identity?

Names are not identity. Two definitions with identical compiled content have the same PinId regardless of what they're named. This eliminates dependency version conflicts, the diamond dependency problem, orphan instance problems, and rename-induced breakage. Zooko's trilemma is acknowledged: names are human-readable labels pointing to content-addressed identities. The two layers are explicitly separate and never confused.

### Why is the module dependency graph required to be acyclic?

PLAN pins are acyclic by construction. A module's PinId includes the PinIds of all definitions in the module, which include PinIds of all dependencies. A circular module dependency would require a PinId to depend on itself — structurally impossible in a Merkle-DAG. The acyclicity requirement is not an arbitrary rule; it falls out of the content-addressing model.

### Why explicit instance imports rather than implicit?

In Haskell, importing a module imports its instances silently. This means you can be affected by instances you didn't know you were importing. Explicit instance imports (`use Foo { instances }` or named instances) make the typeclass instance graph visible. For LLM code generation, local visibility of what instances are in scope is a correctness aid, not a burden.

---

## Glass IR and Debugger

### Why is Glass IR a view over PLAN rather than an independent artifact?

If Glass IR were an independent artifact produced by the compiler, its correctness would require separate verification. As a view (a pretty-printer from PLAN + compiler metadata), its correctness is the round-trip property: a Glass IR fragment parses back to the same PLAN output. This is continuously verified in CI. The view can never lie about the program's behavior because it has no independent existence.

### Why are all semantic Glass IR annotations in valid Gallowglass syntax?

If semantic content lives in comments, an LLM reasoning about Glass IR must treat comments as load-bearing. Comments are outside the semantic model by definition. `Proof a` types, `Trace a`, `Pending e a`, `ReductionRule` — all are first-class Gallowglass types. Glass IR fragments are valid Gallowglass source files. The round-trip property would be impossible otherwise.

### Why effect boundaries as natural snapshot points?

Every time an effect fires, the computation pauses, the continuation is reified, and control passes to the handler. That reification is already a complete snapshot of the computation state — the PLAN heap at that moment. The runtime is already capturing this. Effect-boundary snapshots are free: no additional overhead, and they correspond to semantically significant state transitions that the LLM already understands from type signatures.

### Why separate `VMDiagnostic` from semantic `Snapshot`?

The JetAudit approach (recording which jets fired inside the snapshot) introduces implicit side effects at the semantic level — the snapshot's content would differ depending on which jets fired, violating the observational transparency of jets (%wild semantics). `VMDiagnostic` is a VM-level observation that lives alongside the computation without being part of it. Jets are transparent at the semantic layer; their diagnostic information is available at the observational layer.

---

## Bootstrap Compiler

### Why a restricted dialect rather than full Gallowglass for the self-hosting compiler source?

The bootstrap compiler's scope is bounded by what the restricted dialect requires. Implicit typeclass resolution alone would require significant constraint-solving machinery in the bootstrap. By requiring explicit dictionary passing in the self-hosting compiler source, the bootstrap compiler reduces to basic name resolution and arity checking for typeclass usage. The restrictions are relaxed once self-hosting is achieved.

### Why Python for the bootstrap compiler, not Sire?

The original design called for a bootstrap compiler in Sire (PLAN's macro/assembly language). Python was used instead because:

- Python is faster to write and iterate on, and the bootstrap is thrown away after self-hosting anyway.
- The bootstrap compiler needed to produce valid PLAN seeds directly — Python's arbitrary-precision integers and bytewise I/O make this straightforward.
- Sire requires PLAN to be installed and running to execute, adding a circular dependency during early development. Python has no such dependency.
- The Gallowglass self-hosting compiler (Compiler.gls) replaces the Python bootstrap entirely. There is no long-term maintenance burden.

The Sire outlines were superseded before any Sire code was executed and were
removed from the tree in AUDIT.md C3; they remain accessible via git history.
See `bootstrap/BOOTSTRAP.md` for the complete rationale.

### Why validate with Fibonacci and list operations before attempting the self-hosting compiler?

The validation milestones test incrementally more complex features: basic I/O, `fix` and pattern matching on `Nat`, recursive types (which exercise SCC handling), and effect handling. Each milestone is a necessary prerequisite for the self-hosting compiler. Attempting to compile the self-hosting compiler without these milestones passing would produce failures that are difficult to diagnose.

---

## Jet System

### Why stateless (%wild) jet hints rather than stateful (%fast) hints?

Stateful hints (Urbit's %fast approach) mutate interpreter state, making jet firing a side effect. This means jet firing is observable, cached results carry implicit jet-version information, and the semantic model is polluted. Stateless hints (Plunder's %wild direction) make jets transparent at the semantic level: the program's observable behavior is identical whether or not a jet fires. Jet correctness is a pre-deployment property verified by CI, not a post-hoc runtime audit. If a jet is incorrect, the registry version containing it should never have been deployed.

### Why canary evaluation rather than full verification in production?

Full pre-deployment verification is the primary gate. But jet mismatches will occur in production eventually — exhaustive verification of jet correctness over infinite domains is undecidable in general. Canary evaluation (running a small percentage of jetted computations also interpretively and comparing results) provides continuous production verification without full overhead. The `Quarantine` policy means a discovered mismatch is a recoverable event — the jet is disabled for the session, the program continues correctly, and the divergence is logged for human review.

### Why is the jet registry versioned?

A jet is identified by the PinId of the law it accelerates. If the law changes, its PinId changes, and the jet registration must be renewed. But the jet implementation can also change (corrected) without the law changing. Versioning the registry separately from the law's PinId records this history. `jet_version` is monotonically increasing; `corrected` records when bugs were fixed.

### Why does jet matching logic live in the optimizer (written in PLAN), not in the runtime?

Implementing jet matching in the runtime (C/assembly) means every new runtime must independently re-implement the matching logic, creating divergence risk between implementations and making portable jet registries difficult. By implementing the optimizer — including its jet-matching component — in PLAN itself, all runtimes share the same matching logic and the same jet registry format. The optimizer is a PLAN value; its behavior is formally specified by the laws it contains, not by what VM is running it.

This requires a bootstrap: the optimizer cannot use its own jet matching while it is being built. The solution is BPLAN (see below).

---

## Platform Layering (BPLAN / XPLAN / JPLAN)

*These distinctions were clarified in design discussions with the PLAN authors and are not yet reflected in the PLAN specification itself.*

### Why BPLAN as a separate bootstrap environment?

BPLAN (Bootstrap PLAN) is an extended version of PLAN where jets are available as primitive operations — i.e., calling a law that has a jet registered for its hash immediately dispatches to the native implementation without interpretation. This is the environment in which the Gallowglass toolchain is built.

The problem BPLAN solves: the optimizer and jet-matching logic need to be written in PLAN (so all runtimes share them), but the optimizer itself requires jets to run at reasonable speed during development. BPLAN threads this needle: during boot, jets are available as primitives; once the optimizer is built, it can be used to verify jet correctness from axioms and to bootstrap pure-PLAN environments via virtualization.

For Gallowglass: the bootstrap compiler's output (Phase 1) runs in BPLAN. All `external mod` declarations (`external mod Core.Nat { add : ... }`) are BPLAN primitive operations. The `External` effect marks these boundaries. This is why `External` is a separate effect from `IO` — it marks a PLAN/BPLAN boundary, not a platform I/O boundary.

### Why XPLAN and JPLAN rather than baking platform APIs into the runtime?

XPLAN (PLAN + amd64-linux syscalls) and JPLAN (PLAN + JavaScript browser APIs) are effect extensions that expose platform-native capabilities as PLAN-visible primitives. In Gallowglass terms, they are additional effect rows: `{Linux.IO | r}` for XPLAN code, `{Browser.IO | r}` for JPLAN code.

The alternative — baking these into the runtime (as Urbit does with Vere's Behn, Ames, timestamps, etc.) — creates a fat runtime that is platform-specific, hard to port, and independent for each new platform. With XPLAN/JPLAN:

- Platform drivers are implemented as PLAN values (not C code), with only the syscall boundary being platform-specific.
- All runtimes share the same device driver code above the syscall layer.
- Compatibility across platforms is expressed as virtualization: XPLAN semantics can be implemented by a PLAN interpreter that handles the platform-specific primitives, enabling cross-platform code to run via virtualization rather than recompilation.
- Porting to a new platform (WASM, RISC-V bare metal, etc.) requires implementing only the thin PLAN + syscall layer, not re-implementing all device drivers.

For Gallowglass: the effect row `{IO | r}` in a function signature should be read as "performs I/O that will be elaborated at link time to either XPLAN or JPLAN primitives, depending on the target." The open row variable `r` enables effect-polymorphic code that can run on any platform extension without modification.

### Why is virtualization the compatibility mechanism rather than a common ABI?

A common ABI requires coordination across all implementations and must be backward-compatible indefinitely. Virtualization (implementing XPLAN semantics as a PLAN handler) allows each platform to evolve independently while providing formal compatibility: an XPLAN program's observable behavior, when run under a PLAN interpreter that implements XPLAN semantics, is identical to running it on native XPLAN. The compatibility contract is a Gallowglass theorem, not an ABI contract.

---

## Bootstrap Compiler

### Why did the bootstrap codegen initially not bind the predecessor in wildcard match arms? (Milestone 7.5)

*Fixed in Milestone 7.5. This entry is retained as rationale for the phased approach.*

The bootstrap's initial `_build_nat_dispatch` compiled `match n { 0 → e₀ | _ → e₁ }`
using `const2(e₁)` for the succ branch — ignoring the predecessor entirely.
This was intentional for the first pass (Milestones 1–7): Combinators, Bool,
and nullary enum dispatch don't need the predecessor.

Implementing predecessor binding correctly requires three coordinated changes:

1. **PatVar detection**: when the wildcard arm is `| k → body`, the succ function
   must be a 1-arg lifted law, not `const2`.
2. **Environment extension**: the body must be compiled in a fresh `pred_env`
   (arity=1) where `N(1)` is the predecessor, not the same de Bruijn index as
   an outer lambda parameter.
3. **Lambda lifting**: if the body uses both the predecessor and outer captured
   variables, the succ law must carry captures as extra leading parameters.

Deferring to Milestone 7.5 ensured the fix had concrete usage patterns from the
Core prelude as test cases (Core.Nat.pred, Core.Nat.add, etc.).

The same fix also addressed multi-constructor field extraction: `_compile_adt_dispatch`
now uses Case_ (opcode 3) App handler `(fun=tag, arg=field)` to extract fields,
enabling `| Some x → f x` in the restricted dialect.

### Two recurring bootstrap codegen bugs: single-arm wildcard drop and mixed-arity dispatch (M8.6)

*Fixed in M8.6. This entry documents the pattern so future agents recognise it immediately.*

**Bug 1 — `_compile_single_arm_field_bind` drops `wild_arm`.**

When a constructor match has exactly one non-wildcard arm:
```gallowglass
match v { | PNat _ → 1 | _ → 0 }
```
`_compile_con_match` routes to `_compile_single_arm_field_bind` (single-arm path). The
original code passed `wild_arm=None` to `_compile_adt_dispatch`, silently losing the
wildcard body. The Case_ App handler matched ALL constructors (they are all PLAN Apps) and
returned 1 regardless. So `planval_is_nat(PApp ...)` returned 1 instead of 0.

**Fix:** pass `wild_arm` from `_compile_con_match` to `_compile_single_arm_field_bind` and
thence to `_compile_adt_dispatch`.

**Invariant to maintain:** Any call site that invokes `_compile_single_arm_field_bind` (which
is only ever called from `_compile_con_match`) must pass the enclosing `wild_arm`. Do not
add call sites that omit it.

**Bug 0 — GLS `decl_is_*` predicates return 1 for all constructors (M11.5 fix).**

In `Compiler.gls`, predicate functions like `decl_is_let`, `decl_is_type`, etc. were written as:
```gallowglass
let decl_is_let : Decl → Nat
  = λ d → match d { | DLet _ _ → 1 | _ → 0 }
```
This is the wildcard-arm-drop pattern that triggers `_compile_single_arm_field_bind`. Since all
Decl constructors have arity 2, the extraction always returns 1 (the non-wildcard body) for
any App-constructor input. The predicates were effectively `λ _ → 1`.

The existing cg_pass1/pass2 accidentally worked because their fallback behavior (calling
`cg_register_type` or `cg_register_ext_items` with `Nil` cdefs/items) is a no-op. cg_pass3
was silently broken (compiled DType/DExt as DLets with name=0), but this was undetected
because the M8.8 Path B self-hosting test only exercises `emit_program`, not `compile_program`.

**Fix (M11.5):** All 5 `decl_is_*` predicates now use exhaustive 5-arm matches (same
pattern as `sr_collect_globals` and `sr_resolve_decls`). Each arm names its constructor
explicitly; no wildcard arm is used.

**Bug 2 — Binary path in `_build_field_arm_law` ignores unary arms.**

The binary path (`max_arity == 2`) handles constructors where the outer_fun from Case_ App
is `A(tag, field1)`. For unary constructors (arity=1, e.g. PNat), outer_fun is a bare Nat
(`tag`), not an App. The inner Case_ fires the Nat z/m dispatch, not the App handler.
Before the fix, z_body was `body_nat(0, handler_env.arity)` = `P(0)` — a wrong fallback.

For the unary arm with tag=0 (PNat): outer_fun=0 → inner Case_ z fires. The fix compiles
the unary arm body in `handler_env` with `field = N(arg_idx)` (the outer_arg) and uses it
as z_body.

Unary arms with tag>0 (like PPin=3) are handled by the `unary_arms_gt0` block: a
lambda-lifted `m_succ_law` (arity = handler_env.arity + 1) is built and partially applied
with N(1)..N(handler_env.arity) from the handler env, leaving one slot for the predecessor.
Inside the succ law, `N(arg_idx)` correctly refers to `outer_arg` because the partial
application passes it through. A `_build_tag_chain` succ chain fires the
arm body when the predecessor equals (tag − 1). **Fixed and tested (M8.8 revisit).**

**Constraint:** The binary path handles PlanVal-style types with any combination of
unary (arity=1) and binary (arity=2) constructors. Tags must be contiguous starting at 0
(enforced by tag assignment in scope resolution).

### GLS self-hosting compiler: binary handler and precompiled dispatch fixes (M8.8 revisit)

*These are the direct GLS mirrors of Python Bug 2 above. Fixed in M8.8 revisit.*

**Bug A — `cg_build_tag_chain` fails for non-zero first tag.**

`cg_build_tag_chain` builds a Case_ Nat dispatch chain from pre-compiled
`(tag, PlanVal)` pairs. The original single-entry branch assumed tag=0, so a call with
pairs = `[(2, pval)]` produced a chain that never fired at tag=2.

**Fix:** Added a single-entry non-zero-first-tag branch (at lines ~2940–2963 in Compiler.gls).
When tag0 ≠ 0, it creates `shifted = [(tag0-1, pval)]` and recurses, then wraps the inner
dispatch in `PPin (PLaw 0 (MkPair 1 inner))` — the succ law — to produce the correct chain
that fires after `tag0` applications of m.

**Bug B — `cg_build_binary_handler_body` ignored unary constructor arms.**

The binary path (`max_arity=2`) built an inner law for binary constructor dispatch but passed
constant-0 as the z and m bodies for the outer Case_ on outer_fun. When the scrutinee was
a unary constructor, outer_fun is a bare Nat (tag), not an App — so outer Case_ fired z
(tag=0) or m (tag>0), both returning 0 instead of the correct field value.

**Fix:** Added `cg_build_unary_z_body` (lines ~3142–3167) and `cg_build_unary_m_body`
(lines ~3169–3228) helpers:
- `cg_build_unary_z_body`: compiles the body for the unary arm with tag=0, binding its
  field to `arg_idx` (= outer_arg from the outer Case_ App). Used as z_body in the
  outer `cg_build_reflect_dispatch` call.
- `cg_build_unary_m_body`: for unary arms with tag>0, builds a lambda-lifted succ law
  (arity = n_cap+1) that dispatches on the predecessor via `cg_build_tag_chain`.
  Uses `cg_apply_params` to pass captured outer-env locals through as partial application.

`cg_build_binary_handler_body` (line ~3315) now calls `cg_build_reflect_dispatch` with the
helpers' results instead of `cg_build_reflect_app` (which hardcoded z=0, m=const2(0)).

**Impact:** Without the fix, any GLS match over a mixed-arity type produces incorrect code
when compiled by the GLS self-hosting compiler. The most prominent affected type is `PlanVal`
itself (PNat/PPin unary, PApp/PLaw binary).

**Testing:** `tests/compiler/test_selfhost.py::TestMixedArityBehavioral` verifies the Python
bootstrap (same fix) handles all Tree constructor cases. `TestMixedArityEmit` verifies
emit_program serializes mixed-arity IR without errors. Full GLS codegen correctness requires
planvm (Path A, deferred).

**Root cause shared by both bugs:** The bootstrap codegen was designed incrementally.
The prelude only uses types where (a) matches are exhaustive over 2 arms, (b) all
field-bearing arms have the same arity. PlanVal is the first 4-constructor mixed-arity
type, exposing both gaps simultaneously.

A related bug found during Milestone 7.5: **nat globals inside law bodies must
use the quote form `A(N(0), N(k))` rather than being pinned.** Pinning `True=1`
as `P(1)` causes Case_ dispatch to route to the Pin branch (returning the inner
nat via `id`) rather than the Nat-succ branch. The quote form evaluates via
`kal`'s special case `(0 x) = x` and produces a bare nat, which Case_ dispatches
correctly.

### Why does `Core.PLAN` external mod compile to real opcode pins?

All other `external mod` declarations produce opaque sentinel pins (acceptable
by planvm as seeds but not callable). `Core.PLAN` is special: its five operations
map one-to-one to planvm opcodes 0–4, so the codegen emits `P(N(opcode))` directly.
This makes `Core.PLAN.inc` callable in the Python harness and in planvm, enabling
arithmetic (add, mul) in the Core prelude without waiting for the self-hosting
compiler.

The mapping is hardcoded in `Compiler._CORE_PLAN_OPCODES`. Extending it to other
opcode-backed operations (e.g. `Core.PLAN.force`) follows the same pattern.

### planvm extended opcode calling convention (M8.0 finding)

planvm's `jet.primtab` (plan.s:984) defines 21 pinned-nat dispatchers. Pin 15
(`P(N(15))`) is the universal **PrimOp gateway**: `(<15> (opcode args...))` where
`opcode` is the prim.tab number (e.g. 35=add, 36=sub, 37=mul, 40=eq, 44=lt,
47=div, 48=mod). All pins with index > 4 have arity 1 per `arity.primtab` —
they receive a single ADT argument `(tag field1 field2 ...)` built as nested
App nodes.

Other notable dispatchers:
- Pin 4 (`<4>`): TraceOp — `(<4> (0 msg val))` traces msg and returns val
- Pin 5 (`<5>`): SyscallOp — raw Linux syscalls
- Pin 9 (`<9>`): WriteOp — `(<9> (0 fd nat sz offset))` writes bytes to fd
- Pin 10 (`<10>`): ReadOp — `(<10> (0 fd nat sz offset))` reads bytes from fd

The `repl` function (plan.s:1541) forces the seed value, applies it to an
argVec (CLI arguments converted to string nats via `push_strnat`), casts the
result to a nat, and exits with it as the process exit code.

### Why build arithmetic from opcodes 0–4 first?

The self-hosting compiler needs nat arithmetic (add, sub, eq, lt, div, mod) at
runtime. Two paths exist:

1. **Extended ops via Pin 15**: `(<15> (35 x y))` = add. Efficient O(1) but
   requires constructing the ADT argument correctly and trusting jet dispatch.
2. **Pure recursive from opcodes 0–4**: add = recursive inc, sub = recursive
   Case_ on succ, eq/lt = mutual Case_ on both args. O(n) per operation.

We choose path 2 initially because: (a) the prelude already implements add/mul
this way, (b) it's testable in the Python harness without planvm, (c) compiler
inputs are small (~1000 lines, max nat ~10000), and (d) it avoids coupling to
undocumented jet conventions during bootstrap. Extended ops are an optimization
for after self-hosting is confirmed.

### Why is the self-hosting compiler a single `.gls` file?

The restricted dialect has no cross-module imports — each `.gls` file compiles
independently. The compiler needs types and utilities shared across all phases
(Lexer, Parser, Scope, Codegen, Emit). Rather than duplicating definitions or
building an import system just to discard it, all phases live in one monolithic
file: `compiler/src/Compiler.gls`. Estimated ~800–1500 lines. The logical
phases are sections within this file, not separate modules.

### Why is the compiler core a pure function?

The compiler pipeline (lex → parse → scope → codegen → emit) is a pure
transformation from source bytes to seed bytes. Making `main : Bytes → Bytes`
keeps the core testable in the Python harness without planvm. The I/O wrapper
(reading stdin, writing stdout) is a thin shell around the pure core, handled
separately depending on the planvm I/O mechanism (CLI arg input / exit-code
output, or WriteOp/ReadOp for byte streams).

### Why are hex literals used for byte-encoding constants in the compiler source?

Gallowglass hex literals (`0xFF`, `0x6669`, etc.) are clearer than decimal
for byte-level values where the encoding matters. The lexer and compiler
source make heavy use of UTF-8 byte values and LE-packed keyword nats — a
decimal like `26217` communicates nothing, while `0x6669` immediately shows
`'i'=0x66`, `'f'=0x69` = the keyword `"if"`.

The hex notation is available in the bootstrap lexer (see `lexer.py`
`_scan_nat_literal`) and is preserved in the self-hosting compiler's own
lexer (`lex_scan_nat_hex_go`).

**LE-packed keyword nats in hex:** The byte sequence for a keyword is packed
little-endian (first byte at LSB). In a hex literal this means the byte pairs
read right-to-left give the string in order:
`0x6669` → bytes LSB-first: `0x69`=`'f'`, `0x66`=`'i'`... wait, that gives
`"fi"` not `"if"`. The correct reading: LSB = `0x69` = `'i'`, next byte = `0x66` = `'f'`
→ `"if"` ✓. Equivalently, the hex literal reads as the string reversed when
split into byte pairs. `"else"` → `0x65736C65` (pairs right-to-left: 65=e, 6C=l, 73=s, 65=e).

### Why three parse-result design decisions for M8.3? (Parser)

**1. Concrete pair types per parse function, not a generic `ParseResult a`.**
The restricted dialect has no parametric polymorphism at the value level.
Each parse function returns `Pair ConcreteType (List Token)` using the
concrete result type. No wrapper type is needed — the pair structure is
self-explanatory and avoids unnecessary constructor wrapping/unwrapping.

**2. Minimal error recovery: consume and continue.**
On an unexpected token, the parser emits an error node (`EVar 0` for
expressions, a sentinel `DLet` for declarations) and consumes the offending
token. This keeps the parse loop alive to catch further errors in one pass,
without implementing backtracking or synchronization sets. Full error recovery
is post-1.0.

**3. Type annotations are parsed by skipping to `=`.**
`let f : Nat → Nat = body` — after parsing the binding name, if the next
token is `TkColon`, the parser advances past tokens until it sees `TkEqual`,
then parses the body expression. Type annotations are discarded; the
self-hosting compiler trusts well-typed input for M8. The type checker is
added in M9 after self-hosting is confirmed.

### Why target Plan Assembler output instead of binary seed format? (M8.6 pivot, 2026-03-25)

Sol (PLAN author) confirmed that binary seeds are being deprecated in favour of
**Plan Assembler** — a human-readable textual serialization of the same PLAN DAG
structure that also supports multiple files and macros. The JS VM (in development)
and all future runtimes will target Plan Assembler.

**Impact on M8.6:** The original seed emitter was the most mechanically complex
remaining phase — a bit-packed binary format requiring bignat encoding, a
fragment bitstream, and a header with five u64 fields (see `spec/07-seed-format.md`).
The Plan Assembler equivalent is text concatenation, which is drastically simpler
to implement in restricted Gallowglass.

**Impact on M8.7/M8.8:** The driver reads stdin, writes stdout; the self-hosting
validation compares textual `.pla` output rather than binary seed bytes. The
comparison is now a string equality rather than a byte-for-byte binary diff.

**Impact on the runtime target:** Sol recommends targeting the **Reaver** repo
(`sol-plunder/reaver`) rather than `planvm-amd64`. The M8.0 I/O investigation
should look at Reaver's execution model. The JS VM (browser/Node.js) is also
incoming and will support Plan Assembly.

**Format specification obtained (2026-03-27):** The Plan Assembler format was
extracted from `vendor/reaver/src/hs/PlanAssembler.hs` and `vendor/reaver/doc/reaver.md`.
Full grammar documented in `spec/07-seed-format.md` §13. M8.6 implementation
is in `compiler/src/Compiler.gls` Section 25.

**No change to M8.1–M8.5:** All phases through codegen produce `PlanVal` trees,
which are format-agnostic. The pivot only affects the serialization layer (M8.6).

### Why a BPLAN harness instead of modifying the pure PLAN evaluator?

The pure Python harness (`dev/harness/plan.py`) implements opcodes 0–4 only. Functions
like `add`, `mul`, `bit_or`, and `shift_left` are compiled to recursive PLAN Laws that
are O(n) or O(n²) in the operand values. This caps what the harness can test: any output
requiring `bytes_concat` over more than a single byte hits `add(content, ~12K)` → ~100K
Python frames, exceeding `sys.setrecursionlimit(50000)`.

The BPLAN harness (`dev/harness/bplan.py`) adds a **jet registry**: a dict mapping
`id(L_object) → J` that is populated once at startup by `register_jets(compiled_dict)`.
The BPLAN evaluator (`bevaluate`) is a complete re-implementation of the PLAN evaluator
that checks the jet registry in `_bexec` — if `P(law)` is being applied and `id(law)`
is in the registry, it evaluates arguments and calls the Python implementation directly
instead of interpreting the Law body.

**Why by identity, not by hash or name:** The bootstrap codegen embeds global function
references directly as `P(law_value)` in Law bodies at compile time. The same Python
`L` object is shared across all referencing Laws. Python identity (`id(L)`) is the
cheapest and most reliable way to recognize a specific compiled function without
modifying the compiled output or computing content hashes.

**Why a separate file, not modifying plan.py:** The pure PLAN evaluator is the
authoritative reference implementation. Adding jet dispatch to it would couple the
reference implementation to the bootstrap test infrastructure, making it harder to
reason about purity. `bplan.py` imports from `plan.py` and overrides only what is
necessary for jet dispatch.

**Risk:** Name-based jet identity means a jet that accidentally has a different L object
(e.g., from recompiling) would silently fall back to pure PLAN (slow but correct).
Jets never affect correctness; only speed. M8.8 self-hosting validation provides the
definitive correctness gate independent of the harness.

**Jets registered (21):** `add`, `sub`, `mul`, `div_nat`, `mod_nat`, `pow2`, `bit_or`,
`bit_and`, `shift_left`, `shift_right`, `nat_eq`, `nat_lt`, `lte`, `gte`, `max_nat`,
`min_nat`, `nat_byte_len`, `bytes_length`, `bytes_content`, `bytes_concat`,
`bytes_singleton`. These cover all O(n) recursive arithmetic and bytes operations that
previously blocked ~24 tests. With all jets active, `test_emit.py` runs 38/39 tests
(1 intentional skip).

### Why does CI only validate seed loading, not evaluation? (Partially resolved)

The Docker CI environment has `planvm` (the cog runner) but not a PLAN REPL
evaluator. `planvm <seed>` runs a seed as an interactive cog — it has no
"apply this function to these arguments and check the result" mode.

Reaver (`sol-plunder/reaver`) is the planned CLI eval solution.

**M8.8 Path B partially closes this gap:** The BPLAN harness now evaluates GLS
`emit_program` on the full Compiler.gls compiled module and produces verifiable
Plan Assembler output. This proves semantic correctness for the emit pipeline
without a REPL. The remaining gap is `main` (the full pipeline: lex→parse→scope→
codegen→emit), which exceeds Python's recursion limit and requires planvm Path A
to validate end-to-end.

**M8.8 Path A will fully close this gap:** When `main` is wrapped as a planvm cog
(reads stdin, writes stdout), running `compiler.seed` on `Compiler.gls` and
comparing the output to Path B provides byte-identical functional equivalence proof
for the complete compiler pipeline. This is the alpha release gate.

## CI / planvm

### planvm SIGILL on GitHub Actions runners (2026-04-07)

**Status:** Upstream issue — needs xocore-tech/PLAN fix.

The `plan-vm` CI job clones xocore-tech/PLAN, builds via `nix develop --command
make all`, and installs the resulting `x/plan` binary as `planvm`. The binary
compiles successfully but crashes immediately with `Illegal instruction (core
dumped)` on the GitHub Actions `ubuntu-latest` runner.

The build uses `-msse4.2` explicitly, but the assembly files (`planvm-amd64.s`,
`planvm-amd64data.s`) or the Nix toolchain may emit instructions beyond what the
runner's CPU supports (e.g. AVX, POPCNT via `-march=native` in the Nix shell).

**Impact:** All `@requires_planvm` tests (~89) skip silently. The CI job reports
890 passed / 101 skipped / 0 failed — green, but misleading. The planvm-gated
tests are the ones that validate seed format acceptance and evaluated correctness
on the real VM.

**Upstream fix (xocore-tech/PLAN issue):** The Makefile or Nix devshell should
support building for generic x86-64 (`-march=x86-64`) so the binary runs on any
amd64 host, not just the build machine's microarchitecture. Alternatively,
publishing a pre-built release binary for generic x86-64 would work.

**Local workaround (not yet attempted):** Build with the runner's native GCC
(`make all`) instead of `nix develop --command make all`. This avoids the Nix
toolchain's potential `-march=native` default but loses reproducibility.
