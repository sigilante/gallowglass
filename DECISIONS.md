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

### Why a purpose-built Rust VM in addition to xocore?

The Rust VM provides dual-VM CI: running the same program on both VMs and detecting divergence is the primary correctness mechanism for jet verification. It also becomes the primary runtime post-1.0, designed with snapshot retention and the debugger's needs in mind from the start.

The Rust VM is deferred until after the self-hosting compiler. Building it first would mean building against speculative usage patterns. Building it after means building against real programs with real jet candidates.

### Why BLAKE3-256 as the hash algorithm?

BLAKE3 is faster than SHA-256, parallelizable, Merkle-tree-structured internally (consistent with the DAG heap philosophy), has first-class Rust support, has Haskell bindings for xocore interop, and is public domain licensed. PLAN's spec deliberately leaves the hash algorithm as an implementation detail. BLAKE3-256 is Gallowglass's canonical choice, documented explicitly so all implementations agree.

The hash input canonicalization — how a PLAN value is serialized to bytes before hashing — must match xocore's implementation exactly for PinIds to be portable between VMs. This is a first-class CI test.

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

`Abort` propagates to the cog supervisor, not to any user handler. It is structurally unhandleable. This is the Python/StopIteration problem avoided by construction.

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

The bootstrap compiler's scope is bounded by what the restricted dialect requires. Implicit typeclass resolution alone would require significant constraint-solving machinery in Sire. By requiring explicit dictionary passing in the self-hosting compiler source, the bootstrap compiler reduces to basic name resolution and arity checking for typeclass usage. The restrictions are relaxed once self-hosting is achieved.

### Why Sire for the bootstrap compiler?

Sire is PLAN's own assembly language — it is available wherever PLAN is available, requires no external toolchain, and is designed for exactly this bootstrap purpose (writing pills). The bootstrap compiler is scaffolding; it never needs to be maintained long-term. The self-hosting compiler replaces it.

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
