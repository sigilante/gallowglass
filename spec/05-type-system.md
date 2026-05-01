# Gallowglass Type System

**Spec version:** 0.1
**Depends on:** SPEC.md, spec/06-surface-syntax.md

This document is the complete formal specification of the Gallowglass type system. It covers type inference, effect rows, row typing, typeclasses, contracts, refined types, kinding, and type erasure.

---

## 1. Overview

Gallowglass uses a Hindley-Milner type system extended with:

- **Algebraic effects via row typing.** Every effectful computation carries an explicit effect row in its type signature. Row variables enable polymorphism over unknown effects.
- **Typeclass constraints.** Ad-hoc polymorphism through typeclasses with laws, elaborated to explicit dictionary arguments in Glass IR.
- **Contract-based refinements.** Pre-conditions, post-conditions, and invariants expressed as predicates on types, discharged through a tiered system from syntactic checks to optional SMT.
- **Full type erasure.** All type information is erased at compile time. No runtime type information exists. The PLAN output is untyped. Dictionaries are the only type-level construct that survives compilation as PLAN values.
- **Effects erased.** Effect rows are a compile-time concern. All PLAN laws are pure. The effect discipline ensures correctness before erasure.

The type system is designed so that an LLM reading or writing a function signature sees the complete effect footprint, all constraints, and all contracts immediately, without consulting the implementation.

---

## 2. Core Type Language

The type language is defined by the following grammar. This corresponds to SPEC.md section 3.1 and the type grammar in `spec/06-surface-syntax.md` section 5.

### 2.1 Syntax

```
Type ::=
  | a                          -- type variable (single char a-q)
  | T                          -- type constructor (PascalCase)
  | Type Type                  -- type application (left-associative)
  | Type → Type                -- function type (right-associative)
  | Type ⊗ Type                -- product type (left-associative)
  | Type ⊕ Type                -- sum type (left-associative)
  | {EffRow} Type              -- effect-annotated type
  | (x : Type | Pred)          -- refined type
  | ∀ a. Type                  -- universal quantification
  | ∃ a. Type                  -- existential quantification
  | ⊤                          -- unit type (single inhabitant)
  | ⊥                          -- bottom type (uninhabited)

EffRow ::=
  | ∅                          -- empty row (pure)
  | Effect                     -- single effect
  | Effect, EffRow             -- multiple effects
  | EffRow | r                 -- open row with row variable r
```

### 2.2 Type Variables

Type variables are single lowercase characters in the range `a`--`q`. They range over types of any kind unless constrained by context. Type variables are introduced by:

- Explicit quantification: `∀ a b. a → b → a`
- Implicit quantification: bare type variables in a top-level signature are implicitly universally quantified at the outermost scope
- Typeclass constraints: `Eq a => a → a → Bool`

### 2.3 Row Variables

Row variables are single lowercase characters in the range `r`--`z`. They range over effect rows. Row variables and type variables occupy disjoint namespaces; using a character from `a`--`q` as a row variable or `r`--`z` as a type variable is a compile error.

Row variables are introduced in effect annotations:

```gallowglass
let read_file : Path → {IO, Exn IOError | r} Bytes
```

Here `r` is an open row variable, meaning "plus whatever other effects the caller operates in."

### 2.4 Type Constructors

Type constructors are `PascalCase` identifiers. They include:

- Algebraic types: `List`, `Option`, `Result`
- Builtin types: `Nat`, `Int`, `Text`, `Bytes`, `Bool`
- Opaque types from `external mod`: `Connection`, `Statement`
- Record types: `Point`, `Tolerance`

Type constructors are fully applied via juxtaposition: `List a`, `Result Text Error`, `Map k v`.

### 2.5 Function Types

Function types use `→` (right-associative):

```gallowglass
-- a → b → c  parses as  a → (b → c)
let add : Nat → Nat → Nat
```

All functions are curried. Multi-argument functions are syntactic sugar for nested single-argument functions.

### 2.6 Product and Sum Types

The binary type operators `⊗` (product) and `⊕` (sum) are left-associative:

```gallowglass
-- Tuple syntax (A, B) is sugar for A ⊗ B
-- (A, B, C) is sugar for (A ⊗ B) ⊗ C  (left-associated)

type Pair a b = a ⊗ b
```

Sum types are more commonly declared as algebraic types with constructors:

```gallowglass
type Result a b =
  | Ok  a
  | Err b
```

### 2.7 Effect-Annotated Types

An effect annotation `{EffRow} Type` decorates the return type of a function to indicate which effects that function may perform:

```gallowglass
let transfer : Account → Account → Nat → {State Bank, Exn InsufficientFunds} ⊤
```

See section 4 for the full effect system specification.

### 2.8 Refined Types

A refined type `(x : Type | Pred)` is a type paired with a predicate that constrains its inhabitants:

```gallowglass
let safe_div : Nat → (d : Nat | d ≠ 0) → Nat
```

See section 8 for the full refined types specification.

### 2.9 Quantification

Universal quantification (`∀`) introduces type variables scoped over a type:

```gallowglass
let id : ∀ a. a → a
```

Existential quantification (`∃`) hides a type variable from the consumer:

```gallowglass
type Showable = ∃ a. (a ⊗ (a → Text))
```

Existential types are eliminated by pattern matching on the existential package.

### 2.10 Unit and Bottom

- `⊤` (unit) has exactly one inhabitant, also written `⊤` or `()` at the value level.
- `⊥` (bottom) has no inhabitants. A function returning `⊥` never returns normally. The primary use is in effect operations like `raise : e → ⊥`, indicating that control does not continue past the operation invocation (it is captured by the handler's continuation).

---

## 3. Type Inference

Gallowglass uses Algorithm W-style Hindley-Milner inference extended with constraint solving for effect rows (HM(X) family). The extensions are:

- Row constraints for effect unification
- Typeclass constraints as qualified types
- Bidirectional checking where annotations are provided

### 3.1 Bidirectional Type Checking

When a type annotation is provided, the checker operates in **checking mode**: the annotation supplies the expected type, and the implementation is checked against it. When no annotation is provided, the checker operates in **synthesis mode**: the type is inferred from the expression.

Top-level `let` bindings require type annotations. Local `let` bindings and lambda arguments may omit annotations, in which case inference synthesizes the type.

```gallowglass
-- Annotation provided: checking mode
let map : ∀ a b. (a → b) → List a → List b
  = λ f xs → match xs {
      | Nil      → Nil
      | Cons h t → Cons (f h) (map f t)
    }

-- Local let: inference synthesizes the type of `doubled`
let example : List Nat → List Nat
  = λ xs →
      let doubled = map (λ n → n * 2) xs
      doubled
```

### 3.2 Let-Generalization

At a `let` binding, the inferred type is generalized: all type variables and row variables that do not appear free in the enclosing environment are universally quantified.

```
          Γ ⊢ e : τ    α̅ = ftv(τ) \ ftv(Γ)
    ——————————————————————————————————————————
          Γ ⊢ let x = e : ∀ α̅. τ
```

Generalization occurs at `let` boundaries, not at lambda boundaries. This is the standard HM restriction: lambda-bound variables are monomorphic within their body.

### 3.3 Value Restriction for Effect Polymorphism

Effect-polymorphic types are only generalized over row variables when the bound expression is a **syntactic value** (lambda, constructor application, literal, or variable). This prevents unsound generalization of effectful computations:

```gallowglass
-- This is a syntactic value (lambda): row variable r is generalized
let pure_id : ∀ a. a → {r} a
  = λ x → x

-- This is NOT a syntactic value (application): row variable is NOT generalized
-- The inferred type is monomorphic in the effect row
let ref = State.new 0   -- type: {State Int | r₀} (Ref Int), r₀ fixed
```

The syntactic value forms are: `λ`, constructor names, literals, variables, and type-annotated syntactic values.

### 3.4 Subsumption for Effect Rows

A type with a smaller effect row is a subtype of a type with a larger effect row. This subsumption is checked during function application:

```gallowglass
-- A pure function can be used where an effectful one is expected
let pure_fn : Nat → Nat = λ x → x + 1

-- This is valid: {} ⊆ {IO | r}
let use_it : Nat → {IO | r} Nat = pure_fn
```

See section 7 for the full subsumption rules.

### 3.5 Error Reporting

When inference fails, the error message names the specific conflict. The compiler never reports a bare "type mismatch" — it always provides:

- The two types that failed to unify
- The source location where the conflict was detected
- The source location of the annotation or earlier inference that established the expected type
- For effect row conflicts: which effect is missing or unexpected

---

## 4. Effect System

Gallowglass uses row-typed algebraic effects. Every effectful computation carries an explicit effect row in its type. Effects are declared, used in signatures, and discharged by handlers.

### 4.1 Effect Declaration

An effect is declared with the `eff` keyword, naming its operations and their types:

```gallowglass
eff State s {
  get : ⊤ → s
  put : s  → ⊤
}

eff Exn e {
  raise : e → ⊥
}

eff Generator a {
  yield : a → ⊤
}
```

Each operation's type is written from the perspective of the caller. The operation `raise : e → ⊥` means "given an error value of type `e`, this operation does not return to the caller" (the `⊥` return indicates the continuation is captured by the handler, not that the program terminates).

### 4.2 Effect Rows

Effect rows are unordered collections of effects, optionally open via a row variable:

| Row form | Meaning |
|---|---|
| `{}` or absent | Pure: no effects |
| `{IO}` | Exactly the `IO` effect |
| `{IO, Exn e}` | Exactly `IO` and `Exn e` |
| `{IO, Exn e \| r}` | `IO` and `Exn e`, plus whatever effects `r` represents |
| `{r}` | An unknown set of effects |

The empty row `{}` and the absence of an effect annotation are equivalent. Both mean the function is pure:

```gallowglass
-- These two signatures are identical:
let add : Nat → Nat → Nat
let add : Nat → Nat → {} Nat
```

### 4.3 Row Unification

Row unification extends standard type unification to handle effect rows. Given two rows, unification proceeds by:

1. Decomposing both rows into their constituent effects and optional tail variable.
2. For each effect present in one row, finding its counterpart in the other row (effects are matched by name and type arguments).
3. Unifying the tail: if both rows have tail variables, unifying those variables; if one row is closed and the other open, constraining the open variable to include only the excess effects.

Formally, a row is a set of effect entries plus an optional tail:

```
Row ::= {E₁ τ₁, E₂ τ₂, ..., Eₙ τₙ}         -- closed row
      | {E₁ τ₁, E₂ τ₂, ..., Eₙ τₙ | ρ}      -- open row (ρ is a row variable)
```

Unification of `{E₁, E₂ | ρ₁}` with `{E₁, E₃ | ρ₂}` produces the substitution:

```
ρ₁ ↦ {E₃ | ρ₃}
ρ₂ ↦ {E₂ | ρ₃}
```

where `ρ₃` is a fresh row variable. This captures the insight that each row must accommodate the effects present in the other.

### 4.4 Abort

`Abort` is never in any effect row. It is structurally unhandleable. `Abort` fires when:

- A contract violation is detected at runtime (`Violated` status)
- A fixed-width integer overflows (default policy)
- An unrecoverable internal error occurs

`Abort` propagates directly to the cog supervisor. No user-written handler can intercept it. Any attempt to write `Abort` in an effect row is a compile error:

```gallowglass
-- COMPILE ERROR: Abort must never appear in an effect row
let bad : Nat → {Abort} Nat
```

> **Implementation status (AUDIT.md B5):** the bootstrap typechecker does
> not yet enforce the "no Abort in effect row" invariant — `let bad :
> Nat → {Abort} Nat` is silently accepted today. The strict-xfail
> regression gate
> `tests/bootstrap/test_typecheck.py::test_b5_abort_in_effect_row_is_rejected`
> will turn into an XPASS the moment enforcement lands.

### 4.5 External

`External` marks VM boundary crossings. Any function that calls an `external mod` operation must include `External` in its effect row (or an open row variable that subsumes it):

```gallowglass
external mod Sqlite {
  type Connection : Opaque[~]
  open : Path → {External} Connection
}

-- External is required because the body calls Sqlite.open
let connect : Path → {External, Exn SqlError | r} Connection
  = λ path → Sqlite.open path
```

### 4.6 Deep vs. Shallow Handling

By default, handlers are **deep**: after resuming the continuation `k`, the handler remains installed for subsequent operations of the same effect. The `once` modifier makes a handler arm **shallow**: the handler is discharged after the first operation.

```gallowglass
-- Deep handler: handles all State operations in the computation
handle computation {
  | return x    → λ s → (x, s)
  | get    () k → λ s → k s  s
  | put    s' k → λ _ → k ⊤ s'
}

-- Shallow handler: handles only the first yield
handle generator {
  | return x     → None
  | once yield v k → Some v
}
```

---

## 5. Handler Typing

Handlers transform effect rows: they consume a computation with an effect row containing the handled effect and produce a result with that effect removed from the row.

### 5.1 Handler Structure

A handler expression has the form:

```gallowglass
handle computation {
  | return x         → e_return
  | op₁ args₁... k₁ → e₁
  | op₂ args₂... k₂ → e₂
  ...
}
```

The typing judgment for a handler is:

```
Γ ⊢ computation : {E, R} α
Γ, x : α ⊢ e_return : β
∀ i. Γ, argsᵢ : Aᵢ, kᵢ : (Bᵢ → {R} β) ⊢ eᵢ : {R} β
————————————————————————————————————————————————————————
Γ ⊢ handle computation { ... } : {R} β
```

Where:
- `E` is the effect being handled, with operations `opᵢ : Aᵢ → Bᵢ`
- `R` is the residual row (all effects except `E`)
- `α` is the computation's return type
- `β` is the handler's result type

### 5.2 Return Arm

The `return` arm transforms the computation's pure return value into the handler's result type:

```
| return x → e_return
```

The variable `x` has type `α` (the computation's return type). The body `e_return` has type `β` (the handler's result type). The return arm is mandatory.

### 5.3 Operation Arms

Each operation arm handles one operation of the discharged effect:

```
| op_name args... k → body
```

- `args` have the types of the operation's arguments
- `k` is the continuation: it has type `Bᵢ → {R} β`, where `Bᵢ` is the operation's return type and `{R} β` reflects that the continuation runs in the residual effect context, producing the handler's result type
- `body` has type `{R} β`

For deep handlers, the handler is reinstalled around the continuation, so invoking `k` may trigger the handler again. For shallow handlers (`once`), `k` runs without the handler installed, so the continuation's type is `Bᵢ → {E, R} β` — the effect `E` remains in the row.

### 5.4 Handler Application

Handlers may be applied to additional arguments after the closing brace. This is the standard pattern for state-passing handlers:

```gallowglass
let run_state : ∀ s a. s → {State s | r} a → {r} (a ⊗ s)
  = λ s₀ computation →
      handle computation {
        | return x    → λ s → (x, s)
        | get    () k → λ s → k s  s
        | put    s' k → λ _ → k ⊤ s'
      } s₀
```

Here the handler produces a function `s → {r} (a ⊗ s)`, which is then applied to `s₀`.

---

## 6. Typeclass System

Typeclasses provide ad-hoc polymorphism with laws. Constraints are visible in source signatures and elaborated to explicit dictionary arguments in Glass IR.

### 6.1 Class Declaration

A class declaration introduces a set of methods, optional default implementations, and optional laws:

```gallowglass
class Eq a {
  eq  : a → a → Bool
  neq : a → a → Bool
    = λ x y → not (eq x y)    -- default implementation

  | law reflexive  : ∀ x. eq x x = True
  | law symmetric  : ∀ x y. eq x y = eq y x
  | law transitive : ∀ x y z. eq x y ∧ eq y z => eq x z
}
```

Components:
- **Methods** (`eq`, `neq`): operations available for any type with an `Eq` instance
- **Default implementations** (`neq`): used when an instance does not provide its own implementation
- **Laws** (`reflexive`, `symmetric`, `transitive`): properties that every instance must satisfy; verified by the contract system

### 6.2 Superclass Constraints

A class may require one or more superclasses:

```gallowglass
class Eq a => Ord a {
  compare : a → a → Ordering
  lt      : a → a → Bool
    = λ x y → compare x y = Less
}
```

The constraint `Eq a =>` means every `Ord a` instance must also have an `Eq a` instance. In Glass IR, the `Eq` dictionary is an explicit field of the `Ord` dictionary.

### 6.3 Instance Declaration

An instance provides method implementations for a specific type:

```gallowglass
instance Eq Nat {
  eq = λ x y → Core.Nat.eq x y
}
```

Instance declarations may themselves carry constraints:

```gallowglass
instance Eq a => Eq (List a) {
  eq = λ xs ys → match (xs, ys) {
    | (Nil, Nil)           → True
    | (Cons x xt, Cons y yt) → eq x y ∧ eq xt yt
    | _                    → False
  }
}
```

### 6.4 Constraint Syntax in Signatures

Constraints appear before `=>` in type signatures:

```gallowglass
let sort : ∀ a. Ord a => List a → List a

-- Multiple constraints
let group_by : ∀ a b. (Eq b, Ord b) => (a → b) → List a → List (List a)
```

### 6.5 Dictionary Elaboration

In Glass IR, every typeclass constraint becomes an explicit dictionary argument. The dictionary is a PLAN value (a record of method implementations). This is the only type-level construct that survives erasure.

Source:
```gallowglass
let sort : ∀ a. Ord a => List a → List a
```

Glass IR:
```gallowglass
let sort : ∀ a. (ord_dict : Ord a) → List a → List a
```

At call sites, the compiler inserts the appropriate dictionary:
```gallowglass
-- Source
sort [1, 3, 2]

-- Glass IR
sort [Core.Nat.ord_nat] [1, 3, 2]
```

### 6.6 Coherence via Content-Addressing

Typeclass coherence (the property that at most one instance exists for any type-class pair) is enforced via content-addressing. An instance's PinId is the BLAKE3-256 hash of its compiled PLAN content. In the transitive closure of a program's dependency DAG, each PinId is unique. This structurally eliminates the orphan instance problem: there can be no ambiguity about which instance is in scope because instance identity is content-addressed, not module-scoped.

If two modules define instances for the same type-class pair, they will have different PinIds (since their implementations differ or their dependencies differ). The import system requires explicit instance imports, making the choice visible:

```gallowglass
use MyLib { instance Ord MyType }
```

### 6.7 Explicit Dictionary Override

The `with` syntax allows overriding the dictionary at a call site:

```gallowglass
let desc_sorted = sort with (Ord.reverse Nat.ord) xs
```

This passes a custom `Ord` dictionary instead of the one the compiler would have inferred. In Glass IR, this is a direct dictionary argument.

---

## 7. Subtyping and Subsumption

Gallowglass is not a subtyping language in general. Subtyping appears in exactly one place: effect row subsumption.

### 7.1 Effect Row Subsumption

A type with a smaller effect row is a subtype of a type with a larger effect row:

```
{} ⊆ {E | r}    for all E, r
{E₁, ..., Eₙ} ⊆ {E₁, ..., Eₙ, Eₙ₊₁, ..., Eₘ | r}    for all r
```

This means a pure function can be used wherever an effectful function is expected:

```gallowglass
let pure_add : Nat → Nat → Nat
  = λ x y → x + y

-- Valid: pure_add's row {} ⊆ {IO | r}
let effectful_context : Nat → Nat → {IO | r} Nat
  = pure_add
```

The subsumption rule is:

```
    Γ ⊢ e : σ₁ → {R₁} τ₁      R₁ ⊆ R₂      τ₁ ≤ τ₂
    ——————————————————————————————————————————————————
    Γ ⊢ e : σ₁ → {R₂} τ₂
```

### 7.2 No Structural Subtyping

There is no structural subtyping on records or algebraic types. Two record types with identical fields but different names are distinct types. This is a deliberate design choice: structural subtyping creates typeclass coherence complications, and content-addressed identity requires nominal distinctions.

### 7.3 Variance Annotations on Opaque Types

Opaque types from `external mod` carry variance annotations:

| Annotation | Meaning | Rule |
|---|---|---|
| `[+]` | Covariant | `a ≤ b` implies `F a ≤ F b` |
| `[-]` | Contravariant | `a ≤ b` implies `F b ≤ F a` |
| `[~]` | Invariant (default) | No subtyping relationship |

```gallowglass
external mod IO {
  type Reader  : Opaque[+]   -- covariant: Reader Text ≤ Reader Bytes when Text ≤ Bytes
  type Writer  : Opaque[-]   -- contravariant: Writer Bytes ≤ Writer Text when Text ≤ Bytes
  type Channel : Opaque[~]   -- invariant: no subtyping on Channel
}
```

Variance annotations are checked by the compiler: a covariant type parameter must not appear in contravariant position in any operation's type, and vice versa.

---

## 8. Refined Types

Refined types pair a type with a predicate that constrains its inhabitants. They bridge the type system and the contract system.

### 8.1 Syntax

```gallowglass
-- Refined parameter
(x : Type | Pred)

-- Examples
(d : Nat | d ≠ 0)                    -- nonzero natural
(xs : List a | length xs > 0)        -- nonempty list
(t : Tolerance a | t.abstol ≥ zero)  -- nonneg tolerance
```

The name `x` is bound in `Pred` and refers to the value being refined.

### 8.2 Refinement Predicates

Predicates follow the grammar in `spec/06-surface-syntax.md` section 9. They fall into categories corresponding to the contract discharge tiers:

**Tier 0 (syntactic):** Trivially true or false by construction.
- `True`, `False`
- Predicates that repeat information already established by a prior contract or pattern match

**Tier 1 (built-in decision procedures):** Linear arithmetic and propositional logic.
- Linear arithmetic: `x + y ≤ z`, `length xs > 0`, `2 * n = m`
- Propositional logic: `p ∧ q`, `p ∨ q`, `¬p`, `p => q`
- List/text/bytes length properties: `length xs = length ys + 1`

**Tier 2 (runtime):** Anything outside Tier 0/1. The predicate becomes a runtime check; violation fires `Abort`.
- Nonlinear arithmetic: `x * y = z` (when neither `x` nor `y` is a literal)
- Higher-order properties: `∀ x. f x = g x`
- Recursive predicates: `is_sorted xs`

**Tier 3 (optional SMT):** When a Z3 or CVC5 backend is configured, some Tier 2 predicates may be discharged statically. SMT discharge is an optimization: if the solver times out or is unavailable, the predicate falls to Tier 2.

### 8.3 Refined Type Narrowing

In pattern match branches, the type checker narrows refined types based on the matched pattern:

```gallowglass
let process : List a → Nat
  = λ xs → match xs {
      | Nil      → 0
      -- In this branch, xs is known to satisfy (length xs > 0)
      | Cons h t → length xs
    }
```

The checker tracks which constructors have been eliminated by prior branches, and which predicates are entailed by the current branch's pattern.

### 8.4 Interaction with Type Aliases

Refined types can be used in type aliases to create named constrained types:

```gallowglass
type NonEmptyList a = (xs : List a | length xs > 0)
type NonZero       = (n : Nat | n ≠ 0)
type Percentage    = (p : Nat | p ≤ 100)
```

These aliases expand during type checking. A function accepting `NonZero` is checked as accepting `(n : Nat | n ≠ 0)`.

---

## 9. Contract System

Contracts are pre-conditions, post-conditions, invariants, and laws attached to definitions. They connect the type system to runtime verification.

### 9.1 Contract Syntax

Contracts appear between the type signature and the `=` separator:

```gallowglass
let safe_div : Nat → (d : Nat | d ≠ 0) → Nat
  | pre  Proven       (d ≠ 0)
  | post Deferred(NonLinear) (result * d ≤ n)
  = λ n d → n / d
```

Contract kinds:
- `pre` — precondition: must hold when the function is called
- `post` — postcondition: must hold when the function returns; the name `result` binds the return value
- `inv` — invariant: expands to both a `pre` and a `post` with the same predicate
- `law` — typeclass law: a universally quantified property

### 9.2 ProofStatus

Every contract clause carries a `ProofStatus` indicating how it is discharged:

```gallowglass
type ProofStatus =
  | Proven                      -- Tier 0/1 discharged statically
  | Deferred DeferralReason     -- runtime check emitted
  | Refuted                     -- compile error: statically contradicted
  | Checked                     -- runtime: check passed (post-execution status)
  | Violated                    -- runtime: check failed → Abort
```

- `Proven`: the compiler verified the predicate statically (Tier 0, 1, or 3). No runtime cost.
- `Deferred(reason)`: the compiler cannot prove or disprove the predicate. A runtime check is emitted. If the check fails, `Abort` fires.
- `Refuted`: the compiler proved the predicate is always false. This is a compile error. No executable code is generated.
- `Checked` and `Violated` are runtime statuses, not written by the programmer. They appear in debugger output and runtime diagnostics.

### 9.3 DeferralReason

```gallowglass
type DeferralReason =
  | NonLinear       -- nonlinear arithmetic (x * y)
  | HigherOrder     -- involves higher-order functions
  | Recursive       -- recursive predicate
  | NoSolver        -- would require SMT, none configured
  | SolverTimeout   -- SMT solver timed out
  | OutsideTheory   -- predicate outside supported SMT theories
  | Guard           -- depends on runtime guard value
  | InfiniteType    -- involves infinite type structure
  | AbstractType    -- type is abstract/opaque
  | OutOfBounds     -- array/index bounds check
```

The reason tells the programmer (and the LLM) why the contract was not discharged statically. `Deferred(NoSolver)` means "configure an SMT backend to potentially discharge this." `Deferred(NonLinear)` means "even an SMT backend may not help."

### 9.4 Tautology Detection

The compiler includes a heuristic tautology detector. A contract that is a syntactic recapitulation of the implementation body triggers a warning:

```gallowglass
-- WARNING: contract appears tautological (restates implementation)
let double : Nat → Nat
  | post Proven (result = n + n)    -- this IS the implementation
  = λ n → n + n
```

A valuable contract must be statable from the mathematical specification alone, without reading the implementation. The tautology detector catches the most obvious violations of this principle.

### 9.5 Abort on Violated

When a `Deferred` contract's runtime check fails, the status becomes `Violated` and `Abort` fires. `Abort` is never in the effect row. Contract violations are structurally distinct from `Exn`:

- `Exn` is an expected failure that can be handled
- `Abort` (from `Violated`) is an invariant violation that cannot be handled

This distinction prevents accidentally swallowing a contract violation with an exception handler.

---

## 10. Numeric Types

The numeric type system enforces honesty about precision, overflow, and lawfulness.

### 10.1 Exact Types (Fully Lawful)

These types satisfy all algebraic laws exactly:

```gallowglass
type Nat      : builtin    -- arbitrary precision, PLAN nat
type Int      : builtin    -- sign-magnitude, arbitrary precision
type Rational = { num : Int, den : (d : Int | d ≠ 0) }
type Fixed (scale : Nat) = { value : Int }
```

`Nat` and `Int` never overflow. They have lawful instances for `Eq`, `Ord`, `Add`, `Sub`, `Mul`, and all other standard typeclasses.

### 10.2 Approximate Types (No Lawful Eq or Add)

IEEE 754 floats do not have lawful `Eq` or `Add` instances:

- `NaN ≠ NaN` breaks `Eq` reflexivity
- `(a + b) + c ≠ a + (b + c)` breaks `Add` associativity

```gallowglass
type Float32 : builtin    -- IEEE 754 single
type Float64 : builtin    -- IEEE 754 double
```

These types have no `Eq` instance and no `Add` instance. They have `ApproxEq` and `RoundedAdd` instances instead. NaN-producing operations become `Abort`.

Posit types have lawful `Eq` (NaR = NaR, no signed zero anomaly):

```gallowglass
type Posit32 : builtin    -- lawful Eq
type Posit64 : builtin    -- lawful Eq
```

### 10.3 ApproxEq

```gallowglass
type Tolerance a = {
  abstol : (t : a | t ≥ zero),
  reltol : (t : a | t ≥ zero)
}

class ApproxEq a {
  approx_eq         : a → a → Tolerance a → Bool
  default_tolerance  : Tolerance a

  | law symmetric : ∀ x y t. approx_eq x y t = approx_eq y x t
  | law reflexive : ∀ x t.   approx_eq x x t = True
  -- transitivity is explicitly absent: epsilon-balls do not compose
}
```

Both `abstol` and `reltol` are required. The formula is `|a - b| ≤ abstol + reltol * max(|a|, |b|)`.

### 10.4 Fixed-Width Integer Overflow

Fixed-width integers `Abort` on overflow by default. This is visible in the type:

```gallowglass
let (+) : Int32 → Int32 → Int32    -- Abort on overflow (implicit, always present)
```

Explicit wrapping and saturation are opt-in:

```gallowglass
let wrap_add : Int32 → Int32 → Int32   -- wrapping
let sat_add  : Int32 → Int32 → Int32   -- saturating
let safe_add : Int32 → Int32 → Int     -- promotion to arbitrary precision
```

### 10.5 FromNat

Numeric literals have type `Nat`. The `FromNat` typeclass enables using literals at other numeric types:

```gallowglass
class FromNat a {
  from_nat : Nat → a
}
```

When a literal `42` appears where type `Int` is expected, the compiler inserts `from_nat 42`. For types with bounded range (e.g., `Int32`), `from_nat` may `Abort` if the literal is out of range; the compiler checks this statically when the literal is a constant.

---

## 11. Type Erasure

All types are erased during compilation to PLAN. The PLAN output carries no type annotations, no effect information, and no runtime type representations.

### 11.1 What is Erased

- **All type annotations**: function type signatures, type parameters, type applications
- **All effect rows**: `{IO, Exn e | r}` is erased; the PLAN law is pure
- **All refined type predicates**: the refinement `(d : Nat | d ≠ 0)` erases to `Nat`
- **All typeclass constraints**: replaced by dictionary arguments before erasure
- **All quantifiers**: `∀ a.` and `∃ a.` are erased; they have no runtime representation
- **All kind annotations**: kinds are a compile-time concern

### 11.2 What Survives

- **Dictionaries**: typeclass constraints become explicit PLAN law arguments. The dictionary is a PLAN value (typically a law or a product of laws). This is the only type-level construct with a runtime representation.
- **Contract checks**: `Deferred` contracts survive as runtime `Abort` checks. The check is a PLAN expression that evaluates the predicate and fires `Abort` on failure.
- **Constructor tags**: algebraic type constructors are encoded as PLAN nats (tags). The type information is erased, but the tag structure remains for pattern matching.

### 11.3 Consequences

- No runtime type reflection or type-case dispatch
- No runtime effect tracking
- Type errors are purely a compile-time concern
- A well-typed program cannot produce a type error at runtime (but can produce `Abort` from contract violations or overflow)

---

## 12. Kinding

The kind system classifies types by their arity and role.

### 12.1 Kind Language

```
Kind ::=
  | *              -- ordinary types (inhabited by values)
  | * → *          -- type constructors (List, Option)
  | * → * → *      -- binary type constructors (Result, Map)
  | Effect         -- effect types (State, Exn, IO)
  | Row            -- effect rows
  | Constraint     -- typeclass constraints (Eq a, Ord a)
```

Kind inference follows the same unification approach as type inference: kinds are inferred from usage and checked against declarations.

### 12.2 Kind Assignment

| Construct | Kind |
|---|---|
| `Nat`, `Int`, `Bool`, `Text` | `*` |
| `List`, `Option` | `* → *` |
| `Result`, `Map` | `* → * → *` |
| `State`, `Exn`, `Generator` | `* → Effect` |
| `IO` | `Effect` |
| `{IO, Exn e}` | `Row` |
| `Eq`, `Ord` | `* → Constraint` |
| `Functor`, `Monad` | `(* → *) → Constraint` |

### 12.3 Higher-Kinded Types

Gallowglass supports higher-kinded types. Type variables may range over type constructors, not just base types:

```gallowglass
class Functor f {
  fmap : ∀ a b. (a → b) → f a → f b
}
```

Here `f` has kind `* → *`. The kind is inferred from the usage `f a` and `f b`.

Higher-kinded type variables follow the same naming convention as ordinary type variables (`a`--`q`, single character). Their kind is determined by usage context and unification.

---

## 13. Formal Typing Rules

The core typing judgments use the form `Γ ⊢ e : τ` where `Γ` is the type environment, `e` is an expression, and `τ` is a type. Effect-aware judgments use `Γ ⊢ e : τ ! R` where `R` is an effect row.

### 13.1 Variable

```
    (x : σ) ∈ Γ
    ————————————
    Γ ⊢ x : σ
```

### 13.2 Lambda

```
    Γ, x : τ₁ ⊢ e : τ₂ ! R
    ——————————————————————————
    Γ ⊢ λ x → e : τ₁ → {R} τ₂
```

### 13.3 Application

```
    Γ ⊢ e₁ : τ₁ → {R} τ₂      Γ ⊢ e₂ : τ₁
    ———————————————————————————————————————————
    Γ ⊢ e₁ e₂ : τ₂ ! R
```

### 13.4 Let (with Generalization)

```
    Γ ⊢ e₁ : τ₁      α̅ = ftv(τ₁) \ ftv(Γ)      ρ̅ = frv(τ₁) \ frv(Γ)
    Γ, x : ∀ α̅ ρ̅. τ₁ ⊢ e₂ : τ₂ ! R
    ——————————————————————————————————————————————————————————————————
    Γ ⊢ let x = e₁ in e₂ : τ₂ ! R
```

Where `ftv` computes free type variables and `frv` computes free row variables. Generalization is subject to the value restriction (section 3.3): row variables are only generalized when `e₁` is a syntactic value.

### 13.5 Annotation

```
    Γ ⊢ e : τ₁      τ₁ ≤ τ₂
    ——————————————————————————
    Γ ⊢ (e : τ₂) : τ₂
```

Where `τ₁ ≤ τ₂` is the subsumption check (section 7).

### 13.6 Handler

```
    Γ ⊢ comp : {E, R} α
    Γ, x : α ⊢ e_ret : {R} β
    ∀ i. op_i : A_i → B_i ∈ E
         Γ, args_i : A_i, k_i : (B_i → {R} β) ⊢ e_i : {R} β
    ————————————————————————————————————————————————————————————
    Γ ⊢ handle comp { | return x → e_ret | op_i args_i k_i → e_i ... } : {R} β
```

For shallow handling (`once`), the continuation type changes:

```
    k_i : B_i → {E, R} β    -- effect E remains in the row
```

### 13.7 Match (with Exhaustiveness)

```
    Γ ⊢ e : τ
    ∀ i. Γ, pᵢ : τ ⊢ eᵢ : σ ! R
    exhaustive({p₁, ..., pₙ}, τ)
    ————————————————————————————————————————
    Γ ⊢ match e { | p₁ → e₁ | ... | pₙ → eₙ } : σ ! R
```

Pattern matching requires exhaustiveness (see `spec/03-exhaustiveness.md`). All arms must produce the same type `σ` under the same effect row `R`. Pattern variables are bound in the corresponding arm body.

### 13.8 Contract

```
    Γ ⊢ e : τ₁ → τ₂
    Γ, x : τ₁ ⊢ P_pre : Bool
    Γ, x : τ₁, result : τ₂ ⊢ P_post : Bool
    ————————————————————————————————————————————
    Γ ⊢ (let f : τ₁ → τ₂ | pre S₁ (P_pre) | post S₂ (P_post) = e) : τ₁ → τ₂
```

Where `S₁` and `S₂` are `ProofStatus` values. If `S₁` is `Proven`, no runtime check is emitted for the precondition. If `S₂` is `Deferred(reason)`, a runtime check is emitted that evaluates `P_post` after `e` returns and fires `Abort` if `P_post` is false.

### 13.9 Instantiation and Generalization

```
    Γ ⊢ e : ∀ α. τ
    ——————————————————————        (INST)
    Γ ⊢ e : τ[α ↦ σ]


    Γ ⊢ e : τ      α ∉ ftv(Γ)
    ——————————————————————        (GEN)
    Γ ⊢ e : ∀ α. τ
```

Instantiation substitutes a concrete type for a quantified variable. Generalization introduces a universal quantifier when the variable does not appear free in the environment.

---

## 14. Error Catalogue

The type checker produces the following categories of errors. Each error includes the source location, the conflicting types or constraints, and (where applicable) the location of the annotation that established the expectation.

### 14.1 Type Mismatch

**E0001: Type mismatch**

Two types that should be equal failed to unify.

```
error[E0001]: type mismatch
  --> src/main.gls:12:5
   |
12 |   f True
   |     ^^^^ expected Nat, found Bool
   |
note: expected type established here:
  --> src/main.gls:10:12
   |
10 | let f : Nat → Nat
   |         ^^^
```

### 14.2 Missing Typeclass Instance

**E0002: No instance found**

A typeclass constraint could not be satisfied.

```
error[E0002]: no instance for `Eq (Nat → Nat)`
  --> src/main.gls:15:3
   |
15 |   eq f g
   |   ^^ requires Eq (Nat → Nat)
   |
note: function types do not have an Eq instance
```

### 14.3 Effect Not In Scope

**E0003: Effect not handled**

A computation performs an effect that is not in the enclosing function's effect row and is not handled.

```
error[E0003]: effect `State Int` not in scope
  --> src/main.gls:20:5
   |
20 |   State.get ()
   |   ^^^^^^^^^ performs State Int
   |
note: enclosing function has effect row: {IO | r}
help: either add `State Int` to the effect row or handle it
```

### 14.4 Infinite Type

**E0004: Infinite type**

A type variable would need to be unified with a type containing itself.

```
error[E0004]: infinite type: a ~ List a
  --> src/main.gls:8:12
   |
 8 |   let x = Cons x x
   |           ^^^^^^^^^ a occurs in List a
```

### 14.5 Ambiguous Type Variable

**E0005: Ambiguous type variable**

A type variable cannot be determined from context.

```
error[E0005]: ambiguous type variable `a`
  --> src/main.gls:25:3
   |
25 |   show (from_nat 42)
   |   ^^^^ the type of `from_nat 42` is ambiguous
   |
help: add a type annotation: `(from_nat 42 : Int)`
```

### 14.6 Missing Type Annotation

**E0006: Type annotation required**

The checker cannot infer a type and requires an explicit annotation. This occurs at top-level bindings (which always require annotations) and at polymorphic recursion sites.

```
error[E0006]: type annotation required for top-level binding
  --> src/main.gls:3:1
   |
 3 | let my_fn = λ x → x
   |     ^^^^^ add a type annotation: `let my_fn : a → a`
```

### 14.7 Abort in Effect Row

**E0007: Abort in effect row**

`Abort` appeared in an effect row. `Abort` is never in any effect row; it is unhandleable.

```
error[E0007]: Abort must not appear in an effect row
  --> src/main.gls:5:22
   |
 5 | let bad : Nat → {Abort} Nat
   |                  ^^^^^ Abort is unhandleable; it propagates to the cog supervisor
   |
help: use Exn for handleable errors, or remove the annotation (Abort is implicit)
```

### 14.8 Naming Convention Violation

**E0008: Naming convention violation**

An identifier does not follow the naming convention for its category.

```
error[E0008]: naming convention violation
  --> src/main.gls:7:5
   |
 7 | let MyFunction : Nat → Nat
   |     ^^^^^^^^^^ function names must be snake_case
   |
help: rename to `my_function`
```

### 14.9 Kind Mismatch

**E0009: Kind mismatch**

A type expression has the wrong kind for its position.

```
error[E0009]: kind mismatch
  --> src/main.gls:10:12
   |
10 | let x : List → Nat
   |         ^^^^ expected kind *, found kind * → *
   |
help: List requires a type argument: `List a`
```

### 14.10 Contract Refuted

**E0010: Contract statically refuted**

A contract predicate was proved to be always false.

```
error[E0010]: contract statically refuted
  --> src/main.gls:14:5
   |
14 |   | pre Proven (n < 0)
   |     ^^^^^^^^^^^^^^^^^ predicate is always false for type Nat
   |
note: Nat values are always ≥ 0
```

### 14.11 External Without Effect

**E0011: Missing External effect**

A function calls an `external mod` operation but does not have `External` in its effect row.

> **Implementation status (AUDIT.md B5):** the bootstrap typechecker does
> not yet enforce E0011 — calls to `external mod` operations are silently
> accepted regardless of the caller's effect row. The strict-xfail
> regression gate
> `tests/bootstrap/test_typecheck.py::test_b5_missing_external_is_rejected`
> will turn into an XPASS the moment enforcement lands.

```
error[E0011]: missing External effect
  --> src/main.gls:22:5
   |
22 |   Sqlite.open path
   |   ^^^^^^^^^^^ calls external operation
   |
note: add External to the effect row:
      `let connect : Path → {External, Exn SqlError | r} Connection`
```

### 14.12 Row Variable Collision

**E0012: Row/type variable namespace collision**

A character in `r`--`z` was used as a type variable or a character in `a`--`q` as a row variable.

```
error[E0012]: variable namespace collision
  --> src/main.gls:4:15
   |
 4 | let f : ∀ r. r → r
   |             ^ characters r-z are reserved for row variables
   |
help: use a character from a-q for type variables
```

---

## 15. Summary of Invariants

These properties must hold at all times. Violating any of them is a compiler bug.

1. **Glass IR round-trip.** A Glass IR fragment must reparse to the same PLAN output.
2. **Abort never in a row.** No well-typed program has `Abort` in an effect row.
3. **External propagation.** Any function transitively calling an `external mod` operation has `External` in its effect row.
4. **Dictionary elaboration completeness.** Every typeclass constraint in source is elaborated to an explicit dictionary argument in Glass IR.
5. **Erasure completeness.** No type, effect, kind, or quantifier information survives in PLAN output. Only dictionaries (as values) and deferred contract checks remain.
6. **Coherence.** At most one typeclass instance for any type-class pair exists in the transitive dependency closure.
7. **Canonical SCC ordering.** Mutually recursive definitions are ordered lexicographically by name. Any deviation changes PinIds.
8. **Naming disjointness.** Type variables (`a`--`q`) and row variables (`r`--`z`) occupy disjoint namespaces. No overlap is possible.
9. **Value restriction.** Row variables are generalized only over syntactic values.
10. **Exhaustiveness.** Every pattern match is total over the matched type's constructors.
