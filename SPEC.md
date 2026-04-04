# Gallowglass Language Specification

**Version:** 0.3 (alpha)
**Status:** Specification complete; bootstrap compiler and core prelude complete; self-hosting compiler M8.8 Path B complete — alpha release candidate
**VM Target:** PLAN (xocore-tech/PLAN) — runtime: Reaver (`sol-plunder/reaver`)
**Output Format:** Plan Assembler (textual) — binary seed format deprecated upstream
**Hash Algorithm:** BLAKE3-256

---

## 1. Overview

Gallowglass is a statically typed functional programming language designed with two equally weighted goals:

1. **LLMs can write it correctly.** Generation is constrained: high local constraint at every token, effects visible in every signature, canonical naming enforced by the compiler, no implicit state.
2. **LLMs can reason about it accurately.** Analysis is tractable: pure by default, explicit effects, contracts stated from mathematical specifications, Glass IR makes compiler decisions visible.

It targets the PLAN virtual machine — a minimal graph-reduction system with four constructors (Pin, Law, App, Nat) and five opcodes. All Gallowglass types are erased at compile time. The PLAN output is untyped. Type errors are purely a compile-time concern.

The current implementation status:

```
Phase 0: Foundation documents          ✅ complete
Phase 1: Python bootstrap compiler     ✅ complete (Milestones 1–7.5)
         → compiles restricted Gallowglass dialect to Plan Assembler
         → Core prelude: 36 definitions across 5 modules, planvm-valid
Phase 2: (merged into Phase 1)
Phase 3: Self-hosting compiler         ✅ ALPHA CANDIDATE (Milestone 8)
         → restricted Gallowglass compiler written in restricted Gallowglass
         → output format: Plan Assembler (textual), not binary seeds
         → M8.1 utilities ✅  M8.2 lexer ✅  M8.3 parser ✅  M8.4 scope ✅
         → M8.5 codegen ✅  M8.6 emitter ✅  M8.7 driver ✅
         → M8.8 self-hosting validation: Path B (harness) ✅
           GLS emit_program processes full Compiler.gls → Plan Assembler
           Path A (planvm byte-identical): deferred pending cog wrapping
Phase 4: Rust VM                       post-1.0
Phase 5: Debugger                      post-1.0
Phase 6: Hardening and ecosystem
```

**Bootstrap language:** Python (not Sire). The bootstrap compiler lives in `bootstrap/*.py`.
Archived Sire stubs (never executed) are in `bootstrap/archive/sire/`.
See `bootstrap/BOOTSTRAP.md` and `DECISIONS.md §"Why Python for the bootstrap compiler?"` for rationale.

---

## 2. Execution Model

### 2.1 PLAN VM

PLAN is a graph-reduction virtual machine. Every value is one of:

```
plan ::= <plan>       -- Pin: content-addressed, globally deduplicated
       | {@ @ plan}   -- Law: named pure function with fixed arity
       | (plan plan)  -- App: left-associative application
       | @            -- Nat: arbitrary-precision natural number
```

Laws are the only executable unit. A law `{n a b}` has name `n`, arity `a`, and body `b`. The body uses de Bruijn-style argument indices: index 0 is the law itself (self-reference for recursion), indices 1..a are the arguments.

Pins are content-addressed with BLAKE3-256. Two pins with identical content have identical hashes and are deduplicated in the heap. The heap is a Merkle-DAG: pins reference other pins but never the interior of other pins, keeping the reference graph acyclic.

**Pin evaluation:** A pin's content is reduced to WHNF (weak head normal form) plus the spine of any Law body — not to full normal form. Ephemeral pins used as intermediate results remain in WHNF. Mutually recursive functions can be encoded directly via pins (though they cannot be persisted, as persisted pins require acyclic content).

Five core opcodes (as nat values 0–4, accessed as `P(N(k))` — a Pin wrapping the opcode nat):

| Opcode | Name    | Arity | Semantics |
|--------|---------|-------|-----------|
| `0`    | Pin     | 1     | `P(x)` — content-address and pin a value |
| `1`    | MkLaw   | 3     | `L(arity, name, body)` — construct a named law |
| `2`    | Inc     | 1     | `n + 1` — increment a nat |
| `3`    | Case_   | 6     | `(p l a z m o)` — dispatch on constructor tag: pin→p, law→l, app→a, nat-zero→z, nat-succ→m(pred), with scrutinee o |
| `4`    | Force   | 1     | evaluate to weak head normal form |

The actual planvm (`vendor/PLAN/planvm-amd64/plan.s`) defines additional opcodes
(5–30+) for extended operations (seq, apply-n, is-pin/law/app/nat, unpin, name,
arity, body, nat-ops, etc.). These are BPLAN primitives exposed via
`external mod Core.*` declarations. See `spec/00-primitives.md`.

All other computation is expressed as laws applied to arguments. Jets accelerate
specific pinned laws by matching their BLAKE3 hash against a registry of native
implementations.

### 2.2 Execution Semantics

PLAN uses **lazy evaluation** (normal-order reduction). Opcode 4 (Force) evaluates a value to WHNF. Opcode 0 (Pin) content-addresses a value after reducing it to WHNF plus the spine of any Law body. Full normalization is not guaranteed and not required for correctness.

Because persistent pins are content-addressed and acyclic, values that are stored persistently are structurally sound. Ephemeral pins — common as intermediate results in practice — may contain unevaluated subterms in WHNF.

### 2.3 Effects and the I/O Model

The cog/driver model from earlier PLAN architectures has been replaced. PLAN now provides **direct support for side-effects**, with **virtualization** to run code in a pure sandbox.

From Gallowglass's perspective the design is unchanged: `IO`, `External`, and `Exn` effects mark functions that cross the VM boundary. The PLAN laws themselves are pure. The effect system tracks which functions require I/O interaction.

The VM-level mechanism — direct side-effects + virtualization API — is under active specification upstream. The Gallowglass effect system compiles to the correct abstraction layer; the binding to specific VM primitives is tracked in `IO.md` and will be completed once the API stabilizes.

---

## 3. Type System

### 3.1 Core Type Language

```
Type ::=
  | a                          -- type variable
  | T                          -- type constructor (PascalCase)
  | Type Type                  -- type application
  | Type → Type                -- function type
  | Type ⊗ Type                -- product type
  | Type ⊕ Type                -- sum type
  | {EffRow} Type              -- effect-annotated type
  | (x : Type | Pred)          -- refined type
  | ∀ a. Type                  -- universally quantified type
  | ⊤                          -- unit type
  | ⊥                          -- bottom (uninhabited)

EffRow ::=
  | ∅                          -- empty row (pure)
  | Effect                     -- single effect
  | Effect, EffRow             -- multiple effects
  | Effect, EffRow | r         -- open row with variable r
```

### 3.2 Primitive Types

All primitive types are exposed via `external mod Core.*` declarations and mapped to PLAN opcodes or planvm primitives by the compiler:

```gallowglass
-- Arbitrary precision (never overflow)
type Nat : builtin    -- natural numbers (PLAN nat)
type Int : builtin    -- integers (sign-magnitude pair)

-- Text and binary data
-- Encoding: structural pair (byte_length, content_nat).
-- Target encoding (post-alpha): use a high bit of content_nat to encode length,
-- eliminating the separate length field for small strings. See ROADMAP §Post-1.0.
type Text  : builtin  -- UTF-8 validated
type Bytes : builtin  -- raw binary

-- Boolean
type Bool  : builtin  -- True | False

-- VM identity
type PinId : Opaque[~]  -- BLAKE3-256 hash, invariant opaque

-- Fixed-width (FFI and performance; Abort on overflow by default)
type Int32  : builtin
type Int64  : builtin
type Uint32 : builtin
type Uint64 : builtin
```

### 3.3 Algebraic Types

Sum types use `|` for each constructor. Product types use `⊗` in type expressions and record syntax in declarations.

```gallowglass
-- Sum type
type List a =
  | Nil
  | Cons a (List a)

-- Product type (record)
type Point = {
  x : Real,
  y : Real
}

-- Enumeration
type Ordering =
  | Less
  | Equal
  | Greater
```

### 3.4 Effect System

Effects are declared with `eff`, used in function type signatures, and handled with `handle`.

```gallowglass
-- Effect declaration
eff State s {
  get : Unit → s
  put : s    → Unit
}

eff Exn e {
  raise : e → ⊥
}

-- Function with effects
let transfer : Account → Account → Nat → {State Bank, Exn InsufficientFunds} Unit

-- Pure function (no row annotation needed; {} also valid)
let add : Nat → Nat → Nat
```

**Effect rules:**
- Absence of effect annotation means pure (`{}`)
- `{}` may be written explicitly as a documentation signal
- `Abort` is NEVER in an effect row — it is unhandleable
- `External` marks VM boundary crossings — required in any function calling `external mod` operations
- Row variable `| r` means "plus whatever effects the caller operates in"

### 3.5 Handlers

Handlers discharge effects. The computation's effect row must include the handled effects. The handler's return type is the computation's return type with the handled effects removed.

```gallowglass
let run_state : ∀ s a. s → {State s | r} a → {r} (a ⊗ s)
  = λ s₀ computation →
      handle computation {
        | return x      → λ s → (x, s)
        | get    ()   k → λ s → k s  s
        | put    s'   k → λ _ → k () s'
      } s₀
```

- `| return x →` handles the pure return value
- `| op_name args k →` handles an operation; `k` is the explicit resume continuation
- `once` modifier: `| once op_name args k →` for shallow (single-shot) handling

### 3.6 Contracts

Contracts are pre- and post-conditions attached to function definitions. They appear between the type signature and the `=` body separator.

```gallowglass
let safe_div : Nat → (d : Nat | d ≠ 0) → Nat
  | pre  Proven   (d ≠ 0)
  | post Deferred(NonLinear) (result * d = n)
  = λ n d → n / d
```

**Proof status:**
```gallowglass
type ProofStatus =
  | Proven                      -- SMT or Tier 0/1 discharged statically
  | Deferred DeferralReason     -- runtime check emitted
  | Refuted                     -- compile error: statically contradicted
  | Checked                     -- runtime check passed
  | Violated                    -- runtime check failed → Abort

type DeferralReason =
  | NonLinear | HigherOrder | Recursive
  | NoSolver  | SolverTimeout  | OutsideTheory
```

**Contract discharge tiers:**
- **Tier 0 (syntactic):** Trivially true/false by construction or prior contract
- **Tier 1 (built-in):** Linear arithmetic over Nat/Int, propositional logic, list length properties
- **Tier 2 (runtime):** Anything outside Tier 0/1 becomes a runtime check; violation fires `Abort`
- **Tier 3 (optional SMT):** Pluggable backend (Z3 or CVC5); slips between Tiers 1 and 2 when present

`Abort` is never in the effect row. Contract violations are structurally distinct from `Exn`.

### 3.7 Typeclasses

```gallowglass
-- Declaration
class Eq a {
  eq  : a → a → Bool
  neq : a → a → Bool
    = λ x y → not (eq x y)    -- default implementation
  | law reflexive  : ∀ x. eq x x = True
  | law symmetric  : ∀ x y. eq x y = eq y x
  | law transitive : ∀ x y z. eq x y ∧ eq y z => eq x z
}

-- Instance
instance Eq Nat {
  eq = λ x y → Core.Nat.eq x y
}

-- Usage: constraint visible in signature
let sort : ∀ a. Ord a => List a → List a

-- Explicit dictionary override (escape hatch)
let desc_sorted = sort with (Ord.reverse Nat.ord) xs
```

In Glass IR, all typeclass constraints are elaborated as explicit named dictionary arguments:
```gallowglass
-- Glass IR elaboration of above
let sort : ∀ a. (ord_dict : Ord a) → List a → List a
```

### 3.8 Refined Types

```gallowglass
-- Refined parameter
let from_nat : (n : Nat | n ≥ 0) → Int

-- Refined return (postcondition)
let abs : Int → (result : Nat | result ≥ 0)

-- Used in type signatures
type NonEmptyList a = (xs : List a | length xs > 0)
```

---

## 4. Surface Syntax

See `spec/06-surface-syntax.md` for the complete formal grammar.

### 4.1 Modules

```gallowglass
mod Collections.List {

  -- Imports: qualified by default
  use Core.Types { List, Nat, Bool }
  use Core.Eq    { Eq }

  -- Unqualified import (explicit opt-in)
  use Core.Nat unqualified { (+), (-) }

  -- Exports (explicit list)
  export { List, map, filter, fold_left }

  -- Definitions follow
  let map : ∀ a b. (a → b) → List a → List b
    | post length result = length xs
    = λ f xs → ...

}
```

### 4.2 Functions

```gallowglass
let name : ∀ a b. TypeSignature
  | pre  ProofStatus (precondition_predicate)
  | post ProofStatus (postcondition_predicate)
  = implementation_body
```

The `=` is the most important structural separator in the language. Everything above is specification; everything below is implementation.

### 4.3 Types and Effects

```gallowglass
-- Algebraic type
type Shape =
  | Circle  Real
  | Square  Real
  | Rect    Real Real

-- Effect
eff Logger {
  log : Text → Unit
}

-- Typeclass
class Functor f {
  fmap : ∀ a b. (a → b) → f a → f b
  | law identity    : ∀ x. fmap id x = x
  | law composition : ∀ f g x. fmap (f · g) x = fmap f (fmap g x)
}
```

### 4.4 Pattern Matching

```gallowglass
match xs {
  | Nil          → 0
  | Cons h Nil   → 1
  | Cons h t     → 1 + length t
}

-- With guard
match n {
  | 0                  → "zero"
  | n if n < 0         → "negative"
  | _                  → "positive"
}

-- Or-pattern
match shape {
  | Circle r | Square r → area r
  | Rect w h            → w * h
}
```

### 4.5 DAG Pins

```gallowglass
-- Programmer-introduced pin: computed once, referenced many times
@expensive = compute_something large_input

-- Compiler-introduced pins appear in Glass IR only:
-- @![pin#3a7f9c] result : List Text = ...
```

### 4.6 Homoiconic Quoting

```gallowglass
-- Quote: code as data
let code : Term = `(map length xs)

-- Unquote: splice into quotation
let make_mapper : (∀ a b. (a → b)) → Term
  = λ f → `(map ,(f) xs)

-- Macro: compile-time term transformer with declared effect signature
macro log_and_return (expr) : {IO | r}
  = `(let result = ,expr
      IO.write_stdout (show result)
      result)
```

### 4.7 FFI

```gallowglass
external mod Sqlite {
  type Connection : Opaque[~]     -- invariant opaque
  type Statement  : Opaque[-]     -- contravariant in call position

  open    : Path → {External} Connection
  prepare : Connection → Text → {External, Exn SqlError} Statement
  step    : Statement → {External} (Result Row Done)
}
```

Variance annotations: `[+]` covariant, `[-]` contravariant, `[~]` invariant (default).

---

## 5. Operator Vocabulary

### 5.1 Active Operators

| Unicode | ASCII alt | Meaning |
|---|---|---|
| `→` | `->` | Function arrow, computation |
| `λ` | `fn` | Lambda |
| `∀` | `forall` | Universal quantification |
| `∃` | `exists` | Existential quantification |
| `←` | `<-` | Bind (effectful let) |
| `·` | `.` | Function composition |
| `⊕` | `\|+\|` | Sum types, row union |
| `⊗` | `\|*\|` | Product types |
| `⊤` | `Unit` | Unit type |
| `⊥` | `Never` | Bottom / unreachable |
| `∅` | `{}` | Empty effect row / empty collection |
| `≠` | `/=` | Not equal |
| `≤` | `<=` | Less than or equal |
| `≥` | `>=` | Greater than or equal |
| `∈` | `` `elem` `` | Set membership |
| `∉` | `` `notElem` `` | Set non-membership |
| `⊆` | `` `subsetOf` `` | Subset |
| `` ` `` | — | Quote (homoiconic) |
| `,` | — | Unquote (inside quote) |
| `+` `-` `*` | same | Arithmetic |
| `÷` | `/` | True division |
| `/` | `//` | Integer division |
| `mod` | `mod` | Modulo |
| `\|>` | `\|>` | Pipe (left to right) |

**ASCII alternatives are normalized to Unicode at the lexer.** They never appear in the parser, type checker, code generator, or Glass IR.

### 5.2 Reserved (Unassigned)

`\` — reserved for future use. Compiler rejects with "reserved symbol, not yet assigned."

### 5.3 Naming Conventions (Compiler-Enforced)

| Category | Convention | Example |
|---|---|---|
| Values, functions | `snake_case` | `zip_with`, `read_file` |
| Types, effects | `PascalCase` | `List`, `IO`, `CsvError` |
| Effect operations | `snake_case` | `read`, `put`, `raise` |
| Type variables | Single `a`–`q` | `a`, `b`, `elem` |
| Row variables | Single `r`–`z` | `r`, `s` |
| Modules | `Dot.Qualified` | `Collections.List` |

The compiler rejects names that violate their category's convention with a specific error message suggesting the correct form.

---

## 6. Numeric Tower

### 6.1 Exact Types (Fully Lawful)

```gallowglass
-- Arbitrary precision: Nat and Int never overflow
-- All typeclass laws hold exactly

type Nat  : builtin    -- PLAN nat, arbitrary precision
type Int  : builtin    -- sign-magnitude, arbitrary precision
type Rational = { num : Int, den : (d : Int | d ≠ 0) }
type Fixed (scale : Nat) = { value : Int }
```

### 6.2 Approximate Types (Honest — No Lawful Eq or Add)

```gallowglass
-- IEEE 754: hardware-backed, explicitly approximate
-- No Eq instance: would be unlawful (NaN ≠ NaN)
-- No Add instance: would be unlawful (not associative)
-- NaN-producing operations become Abort instead
type Float32 : builtin
type Float64 : builtin

-- Posit: lawful Eq (NaR = NaR), software implementation
-- First-class for when hardware support arrives (RISC-V extension)
type Posit32 : builtin
type Posit64 : builtin

-- ML/specialist storage formats
type BFloat16 : builtin
type Float16  : builtin
```

### 6.3 Tolerance-Based Approximate Equality

```gallowglass
-- Both components required — no single epsilon
type Tolerance a = {
  abstol : (t : a | t ≥ zero),
  reltol : (t : a | t ≥ zero)
}

class ApproxEq a {
  approx_eq : a → a → Tolerance a → Bool
  default_tolerance : Tolerance a
  | law symmetric : ∀ x y t. approx_eq x y t = approx_eq y x t
  | law reflexive : ∀ x t.   approx_eq x x t = True
  -- transitivity explicitly absent: epsilon-balls do not compose
}
```

### 6.4 Fixed-Width Integer Overflow Policy

```gallowglass
-- Default: Abort on overflow (visible in type signature)
let (+) : Int32 → Int32 → {Abort | r} Int32

-- Explicit wrapping (opt-in, visible at call site)
let wrap_add : Int32 → Int32 → Int32
let sat_add  : Int32 → Int32 → Int32   -- saturating

-- Bignum promotion (always safe)
let safe_add : Int32 → Int32 → Int
  = λ x y → Core.Int.from_int32 x + Core.Int.from_int32 y
```

---

## 7. Text and Bytes

### 7.1 Representation

Both `Text` and `Bytes` use the structural pair encoding in PLAN:

```
(byte_length : Nat, content_nat : Nat)
```

This is a PLAN app of two nats. `byte_length` gives O(1) length access. `content_nat` encodes the bytes as a little-endian nat. The pair disambiguates trailing zero bytes: `b""` is `(0, 0)` while `b"\x00"` is `(1, 0)` — structurally distinct.

`Text` carries an additional invariant: `content_nat` must be a valid UTF-8 encoding of exactly `byte_length` bytes. This invariant is established at creation and maintained by all `Text` operations.

### 7.2 Three Levels of Text Indexing

```gallowglass
-- Three distinct index types
type ByteOffset  = Nat
type CodePoint   = Nat    -- Unicode scalar value, U+0000 to U+10FFFF
type GraphemeIdx = Nat    -- user-perceived character position

-- Default length: grapheme count (what users expect)
let length : Text → Nat = grapheme_count

-- Explicit alternatives
let byte_length     : Text → Nat
let codepoint_count : Text → Nat
let grapheme_count  : Text → Nat
```

### 7.3 Literals

```gallowglass
-- Text literal: UTF-8, validated at compile time
let greeting : Text = "héllo, #{name}!"

-- Raw text: no interpolation, no escape processing
let pattern : Text = r"hello, \n#{name}"

-- Byte literal: raw bytes
let magic : Bytes = b"\x89PNG\r\n\x1a\n"

-- Hex byte literal
let key : Bytes = x"deadbeef cafebabe"
```

Interpolation `#{expr}` desugars to `show expr`. Requires a `Show` instance for `expr`'s type — compile error otherwise.

### 7.4 Show, Debug, Serialize

```gallowglass
-- Show: human-facing, stable API, written by hand
class Show a {
  show : a → Text
}

-- Debug: developer-facing, unstable, shows internal structure, auto-derivable
class Debug a {
  debug : a → Text
}

-- Serialize: machine-facing, round-trippable
class Serialize a {
  serialize   : a → Bytes
  deserialize : Bytes → {Exn SerializeError | r} a
}
```

These three classes are distinct and must not be conflated. Interpolation uses `Show`. Glass IR uses `Debug`. Wire protocols use `Serialize`.

---

## 8. Module System

### 8.1 Three Levels

```
Package    — unit of distribution (content-addressed by PinId once built)
Module     — unit of namespacing and visibility
Definition — unit of identity (content-addressed by BLAKE3-256 PinId)
```

### 8.2 Identity vs Names

**PinId is identity.** A definition's identity is the BLAKE3-256 hash of its compiled PLAN content. Two definitions with identical compiled content have the same PinId regardless of name.

**Names are labels.** The module system maintains a mutable `name → PinId` index. Names can change without changing identity. Renaming a function does not change its PinId (since names are erased during compilation).

In Glass IR, both are shown:
```gallowglass
let Collections.List.map [pin#9c3f81]
  : ∀ a b. (a → b) → List a → List b
  = ...
```

### 8.3 Module Declarations

```gallowglass
mod Collections.List {

  -- Explicit export list (no implicit export-everything)
  export {
    List, map, filter, fold_left, fold_right,
    instance Eq (List a),
    instance Ord (List a)
  }

  -- Imports: qualified by default
  use Core.Types  { List, Nat, Bool }
  use Core.Eq     { Eq }

  -- Unqualified import: explicit
  use Core.Nat unqualified { (+), (-), (≤) }

  -- Re-export
  export { module Core.Types }   -- re-exports Core.Types public interface

}
```

### 8.4 Package Manifests

```gallowglass
package Gallowglass.Collections {
  version = "1.0.0"

  -- Dependencies pinned to content hashes, not version ranges
  -- No dependency resolution at install time; manifest IS the lock file
  depends {
    Gallowglass.Core    at pin#8f3c2a,
    Gallowglass.Prelude at pin#2b9e71
  }

  modules {
    Collections.List,
    Collections.Map,
    Collections.Set
  }
}
```

### 8.5 Coherence

Typeclass coherence is enforced via content-addressing: an instance's PinId is unique in the transitive closure of the program's dependency DAG. The orphan instance problem is structurally avoided — there can be no ambiguity about which instance is in scope because instance identity is content-addressed, not module-scoped.

---

## 9. Compilation Pipeline
9.1 Gallowglass → PLAN
Source Text (UTF-8 Bytes)
    ↓ Lexer: token stream, ASCII→Unicode normalization
Token Stream
    ↓ Parser: recursive descent, LL(1) grammar
AST (PLAN value — homoiconic)
    ↓ Name Resolution: qualified names, scope, free variables
Resolved AST
    ↓ Type Checker: inference, effect rows, contract discharge
Typed AST + Proof Status Map
    ↓ Code Generator:
    │   - SCC detection (Tarjan's algorithm)
    │   - Lambda lifting for mutual recursion
    │   - Exhaustiveness checking
    │   - Effect handler compilation (direct style)
    │   - Contract insertion
    │   - DAG factoring (@pins + automatic CSE)
PLAN Laws/Apps/Pins/Nats
    ↓ Serializer: Seed format
Seed Bytes
9.2 Compilation Principles
Type erasure: All type information is erased. The PLAN output carries no type annotations. Types exist only in Gallowglass.
Effect erasure: Effect rows are erased. All functions are pure PLAN laws. The effect discipline is a compile-time concern.
Dictionary elaboration: Typeclass constraints become explicit law arguments in PLAN. sort : Ord a => List a → List a compiles to a law taking an Ord dictionary as its first argument.
CPS transformation: Effect handlers compile using direct-style CPS. Continuations k in handler arms are reified as partially applied PLAN laws.
DAG factoring: Programmer @pin annotations emit PLAN pins. The compiler additionally performs automatic common subexpression pinning (marked @! in Glass IR).
9.3 Mutual Recursion
Definitions that are mutually recursive (as determined by SCC analysis of the dependency graph) are compiled to a single shared pin containing all laws in canonical lexicographic order.
The shared pin uses PLAN's row encoding: ({0 (n+1) 0} law₀ law₁ ... lawₙ₋₁).
Lambda lifting passes the group as an additional argument to each law, resolving cross-law references without PLAN-level cycles (pins are acyclic by construction).
See spec/02-mutual-recursion.md for complete specification.
10. Glass IR
Glass IR is the human- and LLM-readable form of compiled Gallowglass. It is a view over PLAN + compiler metadata, not an independent artifact. A Glass IR fragment is a valid Gallowglass source file that round-trips to the same PLAN output.
10.1 Key Properties
All names fully qualified — no use directives
All dictionaries explicit — typeclass constraints elaborated as named arguments
All pins labeled — @![pin#hash] name : Type = expr
All proof statuses shown — | pre Proven (pred), | post Deferred(NoSolver) (pred)
All reductions traceable — Trace a type with steps : List Reduction
All pending effects visible — Pending e a type at effect boundaries
Context budget enforced — fragments fit in an LLM context window
10.2 Fragment Structure
-- Snapshot: pin#7a3f91
-- Source: Files.Csv.load:14:5
-- Budget: 4096 tokens

-- Pin declarations: the complete local namespace
@![pin#9c3f81] Collections.List.map
  : ∀ a b. (map_ord : Ord a) → (a → b) → List a → List b

-- Body
let Files.Csv.load [pin#3a7c44]
  : (path : Text) →
    (path_ord : Core.Text.NonEmpty path) →
    {Core.IO.IO, Core.Exn.Exn Files.Csv.CsvError | r}
    (List (List Text))
  | pre  Proven   (Core.Text.byte_length path > 0)
  | post Deferred(NoSolver) (pin#4d7f19 result ≥ 0)
  = λ path path_ord →
      @raw ← Core.IO.read_file path
      pin#9c3f81 [Core.Text.text_ord] Files.Csv.parse_row
        (Core.Text.split "\n" (Core.Text.from_bytes raw))
See spec/01-glass-ir.md for the complete formal grammar.
11. Debugger
The LLM-maximalist debugger operates on snapshots — immutable, serializable captures of VM state at effect boundaries.
11.1 Snapshot Structure
type Snapshot = {
  heap     : MerkleDAG,
  focus    : NodeId,
  trace    : List Reduction,
  effects  : List (Pending e a),
  meta     : GallowglassMeta    -- type/contract info from source
}
11.2 Query Interface
The debugger exposes structured queries that return Glass IR fragments:
query focus        : Snapshot → GlassExpr
query view         : Snapshot → NodeId → GlassExpr
query effects      : Snapshot → List (EffectName ⊗ GlassExpr)
query trace        : Snapshot → Nat → List GlassStep
query contracts    : Snapshot → List ContractResult
query type_of      : Snapshot → NodeId → GlassType
query sharing      : Snapshot → List (PinId ⊗ Nat)
query diff         : Snapshot → Snapshot → SnapshotDiff
query explain_coverage : Snapshot → SourceSpan → CoverageReport
query jets_fired   : Snapshot → List (PinId ⊗ JetEntry)
11.3 Time-Travel
Snapshots are immutable PLAN heaps. "Going back" is returning a reference to an earlier snapshot. Effect boundaries are free snapshot points — the runtime is already capturing complete heap state at every effect. Full trace retention in development builds; configurable in production.
11.4 Effect Injection
The debugger can inject mock results for pending effects — provide a synthetic value for an IO.read without accessing the file system. This is the primary mechanism for testing without real FFI.
12. Jet System
12.1 What Jets Are
A jet is a native (Rust) implementation of a specific PLAN law. When the runtime encounters a pinned law whose BLAKE3-256 hash matches a registered jet, it substitutes the native implementation. The law is the spec; the jet is the acceleration. They must be observationally equivalent.
12.2 Jet Semantics
Jets use stateless hints (%wild semantics, not %fast). Jet firing is transparent at the semantic level — the program's observable behavior is identical whether or not a jet fires. No audit trail. No runtime side effects from jetting.
The diagnostic layer (VMDiagnostic, separate from the semantic Snapshot) can report which jets fired, but this does not affect the computation's identity or result.
12.3 Jet Registry
type JetEntry = {
  law_hash    : PinId,          -- BLAKE3-256 hash of the pinned law
  jet_version : Nat,            -- monotonically increasing
  introduced  : Nat,            -- VM spec version when added
  corrected   : List Nat,       -- VM spec versions where corrected
  status      : JetStatus
}

type JetStatus =
  | Active
  | Deprecated { reason : Text, since : Nat }
  | Retracted  { reason : Text, since : Nat }
Retracted jets block deployment. The CI system enforces this. The runtime never encounters a retracted jet if deployment gates work correctly.
12.4 Production Mismatch Handling
Canary evaluation: a configurable fraction of jetted computations are also evaluated interpretively. Divergence produces a JetDivergence diagnostic and triggers the configured policy: Log | Fallback | Abort | Quarantine. Quarantine disables the jet for the session; the program continues correctly using interpretation.
13. Prelude Scope
The prelude is a package whose PinId is part of the Gallowglass spec. Prelude functions are primary jet candidates.
13.1 Core.Primitives
~101 operations across 11 external modules backed by BPLAN primitive opcodes
(planvm opcodes 5–30+ plus the five core opcodes). In the Python bootstrap,
`external mod Core.PLAN { ... }` declarations compile to real opcode pins;
all other `external mod` declarations compile to opaque sentinel pins pending
the self-hosting compiler. See `spec/00-primitives.md` for full declarations.

**Note on spec/00-primitives.md opcode numbering:** that document uses a
historical numbering (inc=3, pin=4) that does not match the actual planvm.
The canonical mapping is in `SPEC.md §2.1` above.
Core.PLAN — 5 opcodes, direct PLAN access
Core.Nat — 18 arithmetic/comparison/bit operations
Core.Int — 17 operations including fixed-width bounds checking
Core.Pin — 4 operations (hash, eq, unpin, same_pin)
Core.Hash — 6 operations (BLAKE3-256 hash, combine, bytes conversion)
Core.Text — 13 operations
Core.Bytes — 14 operations
Core.Bool — 6 operations
Core.IO — 6 operations (read_file, write_file, read_stdin, write_stdout, write_stderr, exit)
Core.Inspect — 9 operations (PLAN value inspection for homoiconicity)
Core.Abort — 2 operations
See spec/00-primitives.md for complete declarations.
13.2 Standard Types and Classes
Built in Gallowglass on top of Core.Primitives:
Types: Bool, Option, Result, List, Pair
Typeclasses: Eq, Ord, Show, Debug, Serialize, Add, Sub, Mul, Div, EuclideanDiv, FromNat, RoundedAdd, ApproxEq
Effects: Exn, State, IO, Generator, Par
Numeric: Nat, Int, Rational, Fixed n with full instances
Text: Text, Bytes with three-level indexing
Combinators: id, const, flip, fix, ·, |>, fst, snd, absurd
14. Implementation Status

| Component | Status | Location |
|---|---|---|
| Foundation documents | ✅ Complete | `spec/` |
| Core.Primitives spec | ✅ Complete | `spec/00-primitives.md` |
| Glass IR grammar | ✅ Complete | `spec/01-glass-ir.md` |
| Mutual recursion spec | ✅ Complete | `spec/02-mutual-recursion.md` |
| Exhaustiveness spec | ✅ Complete | `spec/03-exhaustiveness.md` |
| PLAN encoding spec | ✅ Complete | `spec/04-plan-encoding.md` |
| Type system spec | ✅ Complete | `spec/05-type-system.md` |
| Surface syntax spec | ✅ Complete | `spec/06-surface-syntax.md` |
| Seed format spec | ✅ Complete | `spec/07-seed-format.md` |
| Python bootstrap compiler | ✅ Complete (M1–M7.5) | `bootstrap/` |
| Core prelude (36 definitions) | ✅ Complete (M7–M7.5) | `prelude/src/Core/` |
| Self-hosting compiler | ✅ Alpha candidate (M8 complete) | `compiler/src/` |
| Rust VM | 🔲 Post-1.0 | `vm/src/` |
| Debugger | 🔲 Post-1.0 | — |

### CI Test Coverage

| Layer | What is tested | Tool |
|---|---|---|
| Python harness | Semantic correctness of compiled PLAN | `dev/harness/plan.py` |
| planvm seed loading | Seed format validity; `x/plan` accepts the file | Docker `planvm` |
| planvm evaluation | **Not yet tested** — format valid ≠ computation correct | Pending Reaver CLI |

M8.8 Path B partially closes this gap: GLS `emit_program` is verified against the
full Compiler.gls module via the BPLAN harness. M8.8 Path A (running `compiler.seed`
via planvm on its own source and comparing output) will close it fully.
Reaver (`sol-plunder/reaver`) is the planned CLI eval solution for full
evaluation-based CI once available.
15. Key References
DECISIONS.md — design rationale for all non-obvious choices
spec/00-primitives.md — Core.Primitives complete declarations
spec/01-glass-ir.md — Glass IR formal PEG grammar
spec/02-mutual-recursion.md — SCC compilation and shared pins
spec/03-exhaustiveness.md — Pattern match exhaustiveness checker
spec/04-plan-encoding.md — Gallowglass → PLAN compilation rules
spec/05-type-system.md — Complete type system formal specification
spec/06-surface-syntax.md — Complete surface grammar (PEG)
spec/07-seed-format.md — Seed serialization format
bootstrap/BOOTSTRAP.md — Bootstrap compiler implementation guide
prelude/PRELUDE.md — Prelude implementation guide
compiler/COMPILER.md — Self-hosting compiler implementation guide
xocore-tech/PLAN — PLAN VM reference implementation
Pallas documentation — https://docs.opfn.co/explanation/plan
