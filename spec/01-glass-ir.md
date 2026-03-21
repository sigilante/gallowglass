# Glass IR Formal Grammar

**Spec version:** 0.1
**Depends on:** SPEC.md, spec/06-surface-syntax.md

This document is the authoritative specification for Glass IR -- the human- and LLM-readable intermediate representation of compiled Gallowglass. Glass IR is a **view** over PLAN + compiler metadata, not an independent artifact. A Glass IR fragment is a valid Gallowglass source file that round-trips to the same PLAN output.

---

## 1. Overview

### 1.1 What Glass IR Is

Glass IR makes every compiler decision visible. Where Gallowglass source code uses conveniences -- implicit dictionaries, unqualified names, inferred types, unnamed pins -- Glass IR shows the elaborated form. It is the answer to "what did the compiler actually do?"

Glass IR exists for three audiences:

1. **LLMs analyzing compiled code.** Every name is qualified, every dictionary is explicit, every pin is labeled with its content hash. An LLM reading Glass IR can reason about the program without resolving imports, inferring types, or guessing which instance was selected.

2. **The debugger.** Snapshot queries return Glass IR fragments. The debugger never invents its own display format; it renders snapshots as valid Gallowglass that happens to use Glass IR extensions.

3. **CI verification.** The round-trip property (parse a Glass IR fragment, compile it, get the same PLAN output) is a continuously verified invariant. If the compiler's elaboration is wrong, round-tripping will detect it.

### 1.2 What Glass IR Is Not

Glass IR is not a separate compilation target. There is no "Glass IR file format" distinct from Gallowglass source. A `.gls` file that begins with a `FragmentMeta` header is parsed in Glass IR mode; all other `.gls` files are parsed in source mode. The two modes share a single grammar (spec/06-surface-syntax.md), with Glass IR extending it via the constructs defined in this document and in 06-surface-syntax.md section 12.

### 1.3 Key Properties

These properties are maintained by construction. Violation of any property is a compiler bug.

- **All names fully qualified.** No `use` directives. Every reference to a definition outside the current fragment uses its full `Module.Path.name` form.
- **All dictionaries explicit.** Typeclass constraints are elaborated as named `DictType` arguments. The dictionary value is passed explicitly at every call site via `DictArg` syntax (`[dict]`).
- **All pins labeled.** Programmer pins carry their content hash: `@name [pin#hash] = expr`. Compiler-introduced pins use `@![pin#hash] name = expr`.
- **All proof statuses shown.** Every contract clause carries its `ProofStatus` -- `Proven`, `Deferred(reason)`, `Checked`, or `Violated`.
- **All reductions traceable.** The `Trace a` type records the reduction history of a value: which beta, delta, iota, handler, contract, or pin reductions were applied, in order.
- **All pending effects visible.** The `Pending e a` type marks suspended effect computations at handler boundaries.
- **Context budget enforced.** Every fragment declares a token budget. Fragments that exceed the budget are split.

---

## 2. Fragment Structure

A Glass IR fragment is the unit of Glass IR output. It represents a single definition (or a small group of related definitions) together with enough context for an LLM to reason about it.

### 2.1 Layout

A fragment has three sections, in order:

1. **Metadata header** -- comment lines declaring the snapshot identity, source location, and token budget.
2. **Pin declarations** -- the local namespace: compiler pins and programmer pins that the body references.
3. **Body** -- the definition(s) being shown, with full elaboration.

### 2.2 Example Fragment

```gallowglass
-- Snapshot: pin#7a3f91
-- Source: Files.Csv.load:14:5
-- Budget: 4096 tokens

-- Pin declarations: the complete local namespace
@![pin#9c3f81] Collections.List.map
  : ∀ a b. (map_ord : Ord a) → (a → b) → List a → List b

@![pin#2e4b88] Core.Text.text_ord
  : Ord Core.Text.Text

-- Body
let Files.Csv.load [pin#3a7c44]
  : (path : Core.Text.Text) →
    (path_ord : Core.Text.NonEmpty path) →
    {Core.IO.IO, Core.Exn.Exn Files.Csv.CsvError | r}
    (Core.Types.List (Core.Types.List Core.Text.Text))
  | pre  Proven   (Core.Text.byte_length path > 0)
  | post Deferred(NoSolver) (pin#4d7f19 result ≥ 0)
  = λ path path_ord →
      @raw ← Core.IO.read_file path
      pin#9c3f81 [Core.Text.text_ord] Files.Csv.parse_row
        (Core.Text.split "\n" (Core.Text.from_bytes raw))
```

### 2.3 Metadata Header Format

The metadata header consists of structured comments. The parser recognizes them only when the file begins with a `FragmentMeta` header (see section 8 on mode switching).

```
-- Snapshot: pin#<hex>       (required: content hash of the snapshot)
-- Source: <QualName>:<line>:<col>   (required: source location of the focus)
-- Budget: <nat> tokens      (optional: token budget for this fragment)
```

The `Snapshot` line identifies the immutable heap state from which this fragment was rendered. The `Source` line identifies the source definition being shown. The `Budget` line, when present, declares the maximum token count for the fragment body.

---

## 3. Formal PEG Grammar

This grammar extends the surface syntax grammar defined in spec/06-surface-syntax.md. All rules not defined here are inherited from that document. The rules below override or extend the surface grammar when the parser is in Glass IR mode (section 8).

The PEG notation conventions are the same as 06-surface-syntax.md section 1: `'x'` literal, `[x-y]` character class, `x / y` ordered choice, `x*` zero or more, `x+` one or more, `[x]` optional, `!x` negative lookahead, `&x` positive lookahead, `~` any character.

### 3.1 Fragment Top Level

```peg
-- A Glass IR fragment: metadata header, pin declarations, then body declarations
Fragment      ← WS FragmentMeta WS PinSection WS BodySection WS EOF

FragmentMeta  ← '--' WS 'Snapshot:' WS PinLit WS
                '--' WS 'Source:' WS SourceSpan WS
                ('--' WS 'Budget:' WS NatLit WS 'tokens' WS)?

PinSection    ← (PinDecl WS)*

PinDecl       ← CompilerPinDecl
              / ProgrammerPinDecl
              / GroupedPin

BodySection   ← (BodyDecl WS)*

BodyDecl      ← GlassLetDecl
              / PendingDecl
              / TraceDecl
              / TypeDecl
              / InstanceDecl
```

### 3.2 Pin Declarations

```peg
-- Compiler-introduced pin: CSE, lambda lifting, or SCC artifact
-- Distinguished from programmer pins by @! prefix
CompilerPinDecl ← '@!' '[' PinLit ']' WS QualName
                   (WS ':' WS Type)?
                   (WS '=' WS Expr)?

-- Programmer pin with its content hash shown
ProgrammerPinDecl ← '@' SnakeName WS '[' PinLit ']'
                     (WS ':' WS Type)?
                     WS '=' WS Expr

-- Grouped pin block: mutually recursive SCC compiled to a single shared pin
-- All definitions inside share a single PinId (the SCC group pin)
GroupedPin    ← '@!' '[' PinLit ']' WS '{' WS
                (GroupMember WS)+ WS
                '}'

GroupMember   ← TypeDecl
              / GlassLetDecl
```

### 3.3 Body Declarations

```peg
-- Glass IR let declaration: fully qualified name with optional pin hash
GlassLetDecl  ← 'let' WS QualName WS ('[' PinLit ']' WS)?
                 ':' WS Type WS
                 ContractClause*
                 '=' WS Expr
```

The `GlassLetDecl` extends the surface `LetDecl` (06-surface-syntax.md section 8) in two ways: the name is always a `QualName` (fully qualified), and an optional `[PinLit]` annotation follows the name, recording the definition's content hash.

### 3.4 Pin References

```peg
-- Reference to a pin by content hash, used in expression position
-- Replaces a qualified name when the name is unavailable or when
-- the hash is the primary identity (e.g., in postconditions)
PinRef        ← PinLit

-- PinLit is defined in 06-surface-syntax.md §1.7:
-- PinLit ← 'pin#' HexChar+
```

Pin references appear in expression position wherever a `QualName` or `SnakeName` would appear. They resolve to the pin with the given content hash. In the example in section 2.2, `pin#9c3f81` refers to `Collections.List.map` and `pin#4d7f19` refers to a helper whose name is outside the fragment's budget.

### 3.5 Dictionary Arguments

```peg
-- Explicit dictionary application: passes a typeclass dictionary value
-- Appears in argument position (AppArg in 06-surface-syntax.md §6)
DictArg       ← '[' WS Expr WS ']'
```

In source Gallowglass, typeclass dictionaries are resolved implicitly. In Glass IR, they appear explicitly as bracket-delimited arguments. For example, the source call `sort xs` where `sort : Ord a => List a -> List a` becomes `pin#9c3f81 [Core.Text.text_ord] xs` in Glass IR.

### 3.6 Pending Effect Declarations

```peg
-- A suspended effect computation at a handler boundary
-- Shows the effect operation and its reified continuation
PendingDecl   ← 'let' WS QualName WS ':' WS PendingType WS '=' WS PendingLit

PendingType   ← 'Pending' WS AtomType WS AtomType

PendingLit    ← '{' WS
                'effect' WS '=' WS Expr WS ',' WS
                'cont'   WS '=' WS Expr WS
                '}'
```

The first `AtomType` in `PendingType` is the effect type; the second is the return type. The `effect` field records the effect operation that was invoked. The `cont` field records the reified continuation (a partially applied PLAN law).

### 3.7 Trace Declarations

```peg
-- A value annotated with its reduction history
TraceDecl     ← 'let' WS QualName WS ':' WS TraceType WS '=' WS TraceLit

TraceType     ← 'Trace' WS AtomType

TraceLit      ← '{' WS
                'value'  WS '=' WS Expr  WS ',' WS
                'steps'  WS '=' WS '[' WS ReductionList? WS ']' WS ',' WS
                'pin'    WS '=' WS PinLit WS ',' WS
                'source' WS '=' WS SourceSpan WS
                '}'

ReductionList ← Reduction (WS ',' WS Reduction)*
```

### 3.8 Reduction Records

```peg
-- A single reduction step: what the term looked like before and after,
-- and which reduction rule was applied
Reduction     ← '{' WS
                'from' WS '=' WS Expr WS ',' WS
                'to'   WS '=' WS Expr WS ',' WS
                'rule' WS '=' WS ReductionRule WS
                '}'

ReductionRule ← 'Beta'       -- function application (lambda → argument substitution)
              / 'Delta'      -- unfolding a named definition (pin → its content)
              / 'Iota'       -- constructor elimination (match on a known constructor)
              / 'Handler'    -- effect handler dispatch (operation → handler arm)
              / 'Contract'   -- contract check insertion/evaluation
              / 'Pin'        -- pinning (normalize and content-address)
```

### 3.9 Source Spans

```peg
-- Source location reference
-- Short form: Module.Path.name:line:col
-- Long form:  Module.Path.name:startLine:startCol:endLine:endCol
SourceSpan    ← QualName ':' NatLit ':' NatLit (':' NatLit ':' NatLit)?
```

### 3.10 Dictionary Types in Type Signatures

```peg
-- Explicit dictionary parameter in a type signature
-- Replaces the typeclass constraint syntax (Ord a =>) with a named argument
-- Not valid in source programs; appears only in Glass IR
DictType      ← '(' WS SnakeName WS ':' WS PascalName WS AtomType* WS ')'
```

This rule is defined in 06-surface-syntax.md section 5 under `AtomType` but is restricted to Glass IR mode. In source mode, it is a parse error. In Glass IR mode, it appears in type signatures wherever a typeclass constraint has been elaborated.

Example: the source signature `sort : ∀ a. Ord a => List a → List a` becomes the Glass IR signature `sort : ∀ a. (ord_a : Ord a) → List a → List a`.

---

## 4. Elaboration Rules

This section specifies how each surface Gallowglass construct transforms into its Glass IR representation. The elaboration is performed by the compiler during type checking and code generation. Glass IR is a rendering of the result.

### 4.1 Typeclass Constraints to Dictionary Arguments

**Source:**
```gallowglass
let sort : ∀ a. Ord a => List a → List a
```

**Glass IR:**
```gallowglass
let Core.Collections.sort [pin#a1b2c3]
  : ∀ a. (ord_a : Ord a) → List a → List a
```

Rules:
- Each typeclass constraint `C a` in the source becomes a `DictType` argument `(dict_name : C a)` prepended to the function type.
- The dictionary name is derived from the class name and type variable: `ord_a` for `Ord a`, `eq_k` for `Eq k`, `show_elem` for `Show elem`.
- When multiple constraints exist, they appear in the order given in the source.
- Superclass dictionaries are threaded through the primary dictionary. `Ord a` implies `Eq a`; the `Eq` dictionary is extracted from the `Ord` dictionary, not passed separately.

### 4.2 Implicit Dictionaries to Named Parameters

At call sites, implicit dictionary resolution becomes explicit dictionary passing.

**Source:**
```gallowglass
let result = sort xs
```

**Glass IR:**
```gallowglass
let result = pin#a1b2c3 [Core.Nat.nat_ord] xs
```

Rules:
- The resolved function is referenced by its pin hash or fully qualified name.
- The resolved dictionary instance is passed as a `DictArg` (`[expr]`).
- When the dictionary is a composite (e.g., `Ord (List a)` built from `Ord a`), the full construction expression appears inside the brackets.

### 4.3 Use Directives to Fully Qualified Names

**Source:**
```gallowglass
use Core.Text { split, from_bytes }
-- ...
split "\n" raw
```

**Glass IR:**
```gallowglass
Core.Text.split "\n" raw
```

Rules:
- Every identifier from an external module is replaced by its fully qualified `QualName`.
- No `use` directives appear in Glass IR.
- Local definitions within the fragment body retain their fully qualified name (which includes the module path).

### 4.4 Programmer Pins to Labeled Pins

**Source:**
```gallowglass
@fields = Core.Text.split "," text
```

**Glass IR:**
```gallowglass
@fields [pin#8f3c2a] = Core.Text.split "," text
```

Rules:
- The programmer's `@name = expr` form gains a `[pin#hash]` annotation showing the content hash of the pinned value.
- The hash is the BLAKE3-256 hash of the normalized PLAN value.

### 4.5 Compiler-Introduced CSE Pins

When the compiler identifies common subexpressions and factors them into pins, these appear in Glass IR with the `@!` prefix.

**Glass IR (no source equivalent):**
```gallowglass
@![pin#d4e5f6] Core.Internal.shared_computation
  : List Core.Text.Text
  = Core.Text.split "\n" (Core.Text.from_bytes raw)
```

Rules:
- Compiler pins use `@!` to distinguish them from programmer pins (`@`).
- The pin hash and a compiler-assigned qualified name are always present.
- An optional type annotation follows the name.
- Compiler pins appear in the pin declaration section of the fragment, not in the body.

### 4.6 Effect Boundaries to PendingDecl

When the debugger renders a snapshot at an effect boundary, the suspended computation appears as a `PendingDecl`.

**Glass IR:**
```gallowglass
let Core.IO.pending_read [pin#112233]
  : Pending Core.IO.IO Core.Bytes.Bytes
  = { effect = Core.IO.read_file path
    , cont   = pin#445566
    }
```

Rules:
- The `Pending e a` type wraps a suspended effect `e` with return type `a`.
- The `effect` field is the effect operation invocation that caused the suspension.
- The `cont` field is the reified continuation -- typically a pin reference to a partially applied PLAN law.

### 4.7 Type Inference Results to Explicit Annotations

Glass IR always shows explicit type annotations. Source code may omit them when the compiler can infer them.

**Source:**
```gallowglass
let id = λ x → x
```

**Glass IR:**
```gallowglass
let Core.Combinators.id [pin#aabbcc]
  : ∀ a. a → a
  = λ x → x
```

---

## 5. Semantic Types

Glass IR introduces several types that do not appear in surface Gallowglass source. These types are first-class Gallowglass types -- they have valid type signatures, can appear in expressions, and are subject to type checking. They exist to make compiler and runtime state visible without resorting to comments or out-of-band metadata.

### 5.1 Trace a

A value paired with the history of reductions that produced it.

```gallowglass
type Trace a = {
  value  : a,
  steps  : List Reduction,
  pin    : PinId,
  source : SourceSpan
}
```

- `value` is the fully reduced result.
- `steps` is the ordered list of reduction steps applied during evaluation.
- `pin` is the content hash of the definition that was evaluated.
- `source` is the source location of the original expression.

`Trace` values appear in debugger output when trace recording is enabled. They allow an LLM to understand not just what a value is, but how it was computed.

### 5.2 Pending e a

A suspended effect computation waiting for a handler response.

```gallowglass
type Pending e a = {
  effect : e,
  cont   : a → PlanValue
}
```

- `effect` is the effect operation that was invoked.
- `cont` is the reified continuation. When the effect is resolved (by a handler or by debugger injection), the continuation is applied to the result.

`Pending` values appear at effect boundaries -- the natural snapshot points in the cog model. They are the debugger's primary mechanism for effect injection (see section 9).

### 5.3 Proof a

A proof witness for a discharged contract predicate.

```gallowglass
type Proof a = {
  predicate : a,
  status    : ProofStatus,
  evidence  : ProofEvidence
}

type ProofEvidence =
  | Syntactic           -- Tier 0: trivially true by construction
  | Decision Text       -- Tier 1: built-in decision procedure (name)
  | Runtime             -- Tier 2: runtime check passed
  | SMT Text            -- Tier 3: external solver (name)
```

`Proof` values appear in Glass IR when contract status is rendered. They are erased at compile time like all types, but they give the Glass IR reader (human or LLM) the evidence chain for a discharged contract.

### 5.4 ReductionRule

The six kinds of reduction the evaluator can perform.

```gallowglass
type ReductionRule =
  | Beta        -- (λ x → body) arg  →  body[x := arg]
  | Delta       -- unfold a named definition (pin content substitution)
  | Iota        -- constructor elimination (match arm selected)
  | Handler     -- effect handler dispatch (operation matched to handler arm)
  | Contract    -- contract predicate evaluated (check inserted or discharged)
  | Pin         -- value normalized and content-addressed (opcode 4)
```

These correspond directly to the standard reduction rules in lambda calculus extended with algebraic effects:
- **Beta** is function application.
- **Delta** is definition unfolding (the term `delta` follows convention from proof assistants where definitions are "delta-reduced" by replacing a name with its body).
- **Iota** is pattern match elimination (from the Calculus of Inductive Constructions terminology).
- **Handler** is effect operation dispatch to the appropriate handler arm.
- **Contract** is the evaluation of a contract predicate (inserted by the compiler as a Tier 2 runtime check, or shown as already discharged by Tier 0/1/3).
- **Pin** is PLAN's normalize-and-content-address operation (opcode 4).

---

## 6. Round-Trip Property

### 6.1 The Invariant

The defining property of Glass IR:

> For any Glass IR fragment `F`, compiling `F` as a Gallowglass source file in Glass IR mode produces PLAN output identical to the PLAN output from which `F` was rendered.

More precisely, let:
- `render(plan, meta)` be the function that produces a Glass IR fragment from a PLAN value and compiler metadata.
- `compile(text)` be the function that compiles a Gallowglass source file to PLAN.
- `plan_eq(a, b)` be structural equality of PLAN values (pin hashes compared, not pin contents).

The invariant is: for all valid `(plan, meta)` pairs,

```
plan_eq(compile(render(plan, meta)), plan) = True
```

### 6.2 What Round-Tripping Verifies

The round-trip property ensures that Glass IR never lies about the compiled output. Specifically:

- **Pin hashes are correct.** If Glass IR says `pin#9c3f81` is the hash of `Collections.List.map`, then compiling that definition must produce a pin with hash `9c3f81...`.
- **Dictionary elaboration is correct.** If Glass IR shows `[Core.Text.text_ord]` as the dictionary argument, then that is the dictionary the compiler selected -- not some other instance.
- **Type annotations are correct.** If Glass IR shows a type, the type checker agrees.
- **Proof statuses are correct.** If Glass IR says `Proven`, the contract was statically discharged. If it says `Deferred(NoSolver)`, no SMT backend was available.

### 6.3 CI Verification

Round-trip verification runs in CI on every commit. The process:

1. Compile the test corpus to PLAN. Record all `(plan, meta)` pairs.
2. Render each pair to a Glass IR fragment.
3. Compile each fragment in Glass IR mode.
4. Assert structural equality of the original and recompiled PLAN values.

Failure in step 4 is a blocking CI error. No code merges with a broken round-trip.

### 6.4 Scope of the Invariant

The round-trip property covers the **semantic content** of the fragment. It does not cover:

- Whitespace and formatting. The renderer may format differently from the original source.
- Comment content. The metadata header is structured; other comments are not preserved.
- Reduction traces. `Trace` values record evaluation history, which is not part of the compiled PLAN output.
- Pending effects. `Pending` values describe runtime state, not compiled output.

The round-trip applies to `LetDecl`, `TypeDecl`, `InstanceDecl`, `CompilerPinDecl`, `ProgrammerPinDecl`, and `GroupedPin` declarations. `TraceDecl` and `PendingDecl` are debugger annotations that are not compiled.

---

## 7. Context Budget

### 7.1 Purpose

Glass IR fragments are designed to fit in an LLM's context window. A fragment that exceeds the context window is useless -- the LLM cannot read it. The budget system ensures every fragment is small enough to be consumed.

### 7.2 Token Counting

The budget is denominated in tokens. The token count is computed using a reference tokenizer (specified in the compiler configuration). The default budget is 4096 tokens, chosen to fit comfortably in a 8K context window alongside a system prompt and query.

The budget covers the entire fragment: metadata header, pin declarations, and body. It does not count the LLM's system prompt or conversation history -- those are the caller's concern.

### 7.3 Splitting Strategies

When a definition exceeds the budget, the renderer splits it into multiple fragments. The strategies, applied in order of preference:

1. **Pin elision.** Pin declarations that are not directly referenced by the body are omitted. The body uses `pin#hash` references instead of qualified names for the omitted pins. An LLM can request the omitted pin's fragment separately.

2. **Trace truncation.** Reduction traces are truncated to the most recent `N` steps. The `Trace` value's `steps` list is shortened; a comment `-- (42 earlier steps omitted)` is inserted.

3. **Body splitting.** For very large definitions (e.g., a function with many match arms), the body is split at natural boundaries (match arms, let bindings, handler arms). Each split fragment references the others by pin hash.

4. **Type elision.** In extreme cases, type annotations on intermediate let bindings within the body are replaced with `_` (inferred), reducing token count. The round-trip property still holds because the compiler re-infers the same types.

### 7.4 Budget Declaration

The budget is declared in the fragment header:

```
-- Budget: 4096 tokens
```

When omitted, the default budget applies. The renderer never produces a fragment that exceeds its declared budget. If splitting cannot bring the fragment under budget, the renderer emits a diagnostic and produces a stub fragment with a `-- (definition too large; request sub-fragments by pin)` comment.

---

## 8. Glass IR Mode

### 8.1 Mode Activation

The parser operates in one of two modes: **source mode** (default) and **Glass IR mode**. Glass IR mode is activated by either:

1. The presence of a `FragmentMeta` header at the beginning of the file (the `-- Snapshot:` line).
2. An explicit compiler flag (`--glass-ir` or equivalent).

### 8.2 Constructs Valid Only in Glass IR Mode

The following constructs are parse errors in source mode but valid in Glass IR mode. These are defined in 06-surface-syntax.md section 12 and formalized in section 3 of this document.

| Construct | Syntax | Purpose |
|-----------|--------|---------|
| `CompilerPinDecl` | `@![pin#hash] Name` | Compiler-introduced pin |
| `PinRef` | `pin#hash` | Reference to pin by content hash |
| `DictArg` | `[expr]` | Explicit dictionary application |
| `DictType` | `(name : Class a)` | Explicit dictionary type parameter |
| `GroupedPin` | `@![pin#hash] { ... }` | Mutually recursive SCC block |
| `PendingDecl` | `let name : Pending e a = ...` | Suspended effect |
| `TraceDecl` | `let name : Trace a = ...` | Traced value |
| `FragmentMeta` | `-- Snapshot: ...` | Fragment metadata header |

### 8.3 Constructs Invalid in Glass IR Mode

The following constructs are valid in source mode but invalid (parse errors) in Glass IR mode. Their presence indicates an incompletely elaborated program.

| Construct | Reason |
|-----------|--------|
| `use` directives | All names must be fully qualified |
| Bare unqualified external names | Must use `QualName` or `PinRef` |
| Typeclass constraints (`C a =>`) | Must be elaborated as `DictType` arguments |
| Implicit dictionary resolution | Must use explicit `DictArg` at call sites |
| `mod` declarations | Fragments are module-independent |
| `export` declarations | No module-level visibility in fragments |
| `package` declarations | Fragments are package-independent |
| `macro` declarations | Macros are expanded before Glass IR rendering |

### 8.4 Shared Constructs

All other constructs from the surface grammar are valid in both modes. This includes `let`, `type`, `eff`, `class`, `instance`, `match`, `handle`, `if`/`then`/`else`, lambdas, pin expressions, literals, operators, patterns, contracts, and all expression forms.

---

## 9. Debugger Integration

### 9.1 Snapshot to Glass IR

The debugger's query interface (SPEC.md section 11.2) returns Glass IR fragments. When an LLM or developer queries a snapshot, the response is a valid Gallowglass file in Glass IR mode.

The rendering pipeline:

1. **Extract.** The debugger extracts the relevant PLAN subgraph from the snapshot's Merkle-DAG heap.
2. **Annotate.** Compiler metadata (types, proof statuses, source locations) is attached from the `GallowglassMeta` stored in the snapshot.
3. **Render.** The annotated subgraph is rendered as a Glass IR fragment using the rules in section 4.
4. **Budget.** The fragment is checked against the context budget. If it exceeds the budget, splitting strategies (section 7.3) are applied.

### 9.2 Query Types and Their Glass IR Output

Each debugger query produces a specific kind of Glass IR fragment:

- **`query focus`** -- renders the definition at the snapshot's focus node as a `GlassLetDecl` with full type annotation and contract status.
- **`query view`** -- renders an arbitrary node as a `GlassLetDecl` or `CompilerPinDecl`.
- **`query effects`** -- renders all pending effects as a sequence of `PendingDecl` declarations.
- **`query trace`** -- renders a `TraceDecl` with the requested number of reduction steps.
- **`query contracts`** -- renders all contract clauses with their `ProofStatus`, as `ContractClause` annotations on the relevant definitions.
- **`query type_of`** -- renders a minimal fragment with only the type annotation (no body).
- **`query sharing`** -- renders pin declarations with reference counts as comments.
- **`query diff`** -- renders two fragments side by side with change markers (a diff-specific Glass IR extension).
- **`query explain_coverage`** -- renders the exhaustiveness analysis for a match expression, showing covered and uncovered patterns.
- **`query jets_fired`** -- renders pin declarations for jetted laws with jet metadata as comments (from `VMDiagnostic`, separate from the semantic snapshot per DECISIONS.md).

### 9.3 Effect Injection via Glass IR

The debugger can inject mock results for pending effects. The injection is expressed as a Glass IR fragment that the debugger "compiles" into the snapshot:

```gallowglass
-- Snapshot: pin#7a3f91
-- Source: Core.IO.pending_read:1:1
-- Budget: 512 tokens

let Core.IO.pending_read [pin#112233]
  : Pending Core.IO.IO Core.Bytes.Bytes
  = { effect = Core.IO.read_file "/tmp/test.csv"
    , cont   = pin#445566
    }

-- Inject: provide synthetic result for the pending effect
let Core.IO.injected_result
  : Core.Bytes.Bytes
  = b"name,age\nalice,30\nbob,25\n"
```

The debugger applies the continuation (`pin#445566`) to the injected result, producing a new snapshot. This is the primary mechanism for testing without real FFI (SPEC.md section 11.4).

---

## 10. Examples

### 10.1 Simple Pure Function with Dictionary Elaboration

**Source:**
```gallowglass
use Core.Types { List }
use Core.Eq    { Eq }

let nub : ∀ a. Eq a => List a → List a
  = λ xs → ...
```

**Glass IR:**
```gallowglass
-- Snapshot: pin#ff1234
-- Source: Collections.List.nub:12:3
-- Budget: 2048 tokens

let Collections.List.nub [pin#ab12cd]
  : ∀ a. (eq_a : Eq a) → Core.Types.List a → Core.Types.List a
  = λ eq_a xs →
      fix λ self acc remaining →
        match remaining {
          | Core.Types.Nil → Core.Types.Nil
          | Core.Types.Cons h t →
              if Collections.List.elem [eq_a] h acc
                then self acc t
                else Core.Types.Cons h (self (Core.Types.Cons h acc) t)
        }
      Core.Types.Nil xs
```

### 10.2 Mutually Recursive SCC Group

```gallowglass
-- Snapshot: pin#cc9988
-- Source: Compiler.Check.even_odd:44:1
-- Budget: 2048 tokens

@![pin#deadbeef] {
  let Compiler.Check.is_even [pin#dead01]
    : Core.Nat.Nat → Core.Bool.Bool
    = λ n →
        match n {
          | 0 → Core.Bool.True
          | _ → Compiler.Check.is_odd (Core.Nat.sub n 1)
        }

  let Compiler.Check.is_odd [pin#dead02]
    : Core.Nat.Nat → Core.Bool.Bool
    = λ n →
        match n {
          | 0 → Core.Bool.False
          | _ → Compiler.Check.is_even (Core.Nat.sub n 1)
        }
}
```

The `@![pin#deadbeef]` labels the shared SCC group pin. Both `is_even` and `is_odd` are compiled into a single PLAN pin containing a row of laws, per spec/02-mutual-recursion.md. The individual pin hashes (`pin#dead01`, `pin#dead02`) identify the individual laws within the group.

### 10.3 Traced Value with Reduction History

```gallowglass
-- Snapshot: pin#aabb11
-- Source: Core.Nat.add:1:1
-- Budget: 4096 tokens

let Core.Nat.add_trace [pin#trace01]
  : Trace Core.Nat.Nat
  = { value  = 5
    , steps  = [ { from = Core.Nat.add 2 3
                 , to   = Core.Nat.add 2 3
                 , rule = Delta
                 }
               , { from = (λ x y → Core.PLAN.increment_by x y) 2 3
                 , to   = Core.PLAN.increment_by 2 3
                 , rule = Beta
                 }
               , { from = Core.PLAN.increment_by 2 3
                 , to   = 5
                 , rule = Delta
                 }
               ]
    , pin    = pin#nat_add_hash
    , source = Core.Nat.add:1:1
    }
```

### 10.4 Pending Effect at Handler Boundary

```gallowglass
-- Snapshot: pin#eeff00
-- Source: Files.Csv.load:18:9
-- Budget: 2048 tokens

@![pin#9c3f81] Collections.List.map
  : ∀ a b. (a → b) → Core.Types.List a → Core.Types.List b

let Files.Csv.pending_io [pin#pend01]
  : Pending Core.IO.IO Core.Bytes.Bytes
  = { effect = Core.IO.read_file "/data/input.csv"
    , cont   = pin#445566
    }
```

This fragment shows the computation suspended at an `IO.read_file` call. The continuation `pin#445566` is the rest of `Files.Csv.load` after the `@raw ← Core.IO.read_file path` bind. An LLM or developer can request the continuation's fragment separately, or use effect injection (section 9.3) to provide a synthetic result.

### 10.5 Contract Statuses

```gallowglass
-- Snapshot: pin#112233
-- Source: Core.Nat.safe_div:5:1
-- Budget: 2048 tokens

let Core.Nat.safe_div [pin#div001]
  : Core.Nat.Nat → (d : Core.Nat.Nat | d ≠ 0) → Core.Nat.Nat
  | pre  Proven        (d ≠ 0)
  | post Deferred(NonLinear) (result * d ≤ n)
  | post Deferred(NonLinear) (n - result * d < d)
  = λ n d → Core.PLAN.nat_div n d
```

The precondition `d ≠ 0` is `Proven` -- it falls within Tier 1 (linear arithmetic over Nat) and is enforced by the refined type of `d`. The postconditions involve multiplication (nonlinear arithmetic) and are `Deferred(NonLinear)` -- they become runtime checks.

### 10.6 Compiler CSE Pin

```gallowglass
-- Snapshot: pin#778899
-- Source: Data.Csv.load:20:1
-- Budget: 4096 tokens

-- Compiler identified this subexpression as shared across three call sites
@![pin#cse001] Data.Internal.shared_split
  : Core.Types.List Core.Text.Text
  = Core.Text.split "\n" (Core.Text.from_bytes raw)

let Data.Csv.load [pin#csv001]
  : Core.Text.Text → {Core.IO.IO, Core.Exn.Exn Data.Csv.CsvError | r}
    (Core.Types.List (Core.Types.List Core.Text.Text))
  = λ path →
      @raw ← Core.IO.read_file path
      Collections.List.map [Core.Text.text_ord] Data.Csv.parse_row
        pin#cse001
```

The compiler extracted `Core.Text.split "\n" (Core.Text.from_bytes raw)` into a CSE pin (`@!`) because it appeared at multiple use sites. In the body, `pin#cse001` references the factored-out subexpression.

---

## 11. Revision Log

| Issue | Resolution |
|-------|------------|
| Initial draft | Document created with full grammar, elaboration rules, semantic types, round-trip specification, context budget system, mode switching rules, debugger integration, and examples |
