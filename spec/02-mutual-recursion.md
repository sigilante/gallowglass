# Mutual Recursion: SCC Compilation, Shared Pins, and Lambda Lifting

**Spec version:** 0.1
**Depends on:** SPEC.md, spec/04-plan-encoding.md

This document specifies how Gallowglass compiles mutually recursive definitions to PLAN. Because PLAN pins form an acyclic Merkle-DAG and PLAN laws can only self-reference (via index 0), mutual recursion requires special compilation machinery: SCC detection, shared pin encoding, and lambda lifting.

---

## 1. Overview

PLAN imposes three structural constraints that make mutual recursion non-trivial:

1. **Pins are acyclic.** The PLAN heap is a Merkle-DAG. A pin's content hash includes the hashes of all pins it references. A pin cannot reference itself or any pin that transitively references it.

2. **Laws can only self-reference.** A law `{n a b}` has body `b` where index 0 refers to the law itself. There is no mechanism for a law to refer to a *different* law by index.

3. **No cross-law references.** Two laws pinned separately cannot refer to each other. Each is content-addressed independently; neither can contain the other's hash without creating a cycle.

These constraints mean that two mutually recursive functions (e.g., `is_even` calling `is_odd` and vice versa) cannot each be compiled to an independent pinned law. Gallowglass resolves this by grouping mutually recursive definitions into a single shared pin, using lambda lifting to thread the group through each law as an additional argument.

---

## 2. SCC Detection

The compiler builds a **definition dependency graph** within each module: vertices are definitions, and a directed edge from `f` to `g` exists when `f`'s body references `g`. Tarjan's algorithm decomposes this graph into strongly connected components (SCCs) in reverse topological order.

### 2.1 Input

A set of definitions within a single module. Each definition has a fully qualified name and a body referencing zero or more other definitions.

### 2.2 Output

A list of SCCs in topological order (dependencies before dependents). Each SCC is one of:

| SCC kind | Description | Compilation strategy |
|---|---|---|
| Singleton, non-recursive | Definition does not reference itself | Ordinary pinned law |
| Singleton, self-recursive | Definition references itself | Single law with index 0 self-reference |
| Mutual recursion group | Two or more definitions that form a cycle | Shared pin with lambda lifting |

### 2.3 Example Dependency Graph

Consider a module with five definitions:

```
Definitions: { alpha, beta, gamma, delta, epsilon }

References:
  alpha   → beta
  beta    → alpha       -- alpha and beta are mutually recursive
  gamma   → alpha       -- gamma depends on the alpha/beta group
  delta   → delta       -- delta is self-recursive
  epsilon → (none)      -- epsilon is non-recursive
```

Dependency graph:

```
  epsilon        delta ──┐
                   ↺     │
                         │
  gamma ──→ alpha ←──→ beta
```

Tarjan's algorithm produces SCCs in topological order:

```
SCC 0: { epsilon }        -- singleton, non-recursive
SCC 1: { delta }          -- singleton, self-recursive
SCC 2: { alpha, beta }    -- mutual recursion group
SCC 3: { gamma }          -- singleton, non-recursive (depends on SCC 2)
```

Each SCC is compiled independently. SCCs are emitted in topological order so that every definition's dependencies are already pinned when it is compiled.

---

## 3. Canonical Ordering

**Invariant:** Within an SCC, definitions are ordered lexicographically by their fully qualified name.

This ordering is:

- **Deterministic** — given the same set of names, the order is always the same.
- **Content-independent** — it depends only on names, never on definition bodies.
- **Stable** — adding or removing a definition outside the SCC does not change the ordering of definitions inside it.

### 3.1 Formal Ordering Rule

Given an SCC containing definitions with fully qualified names `n₀, n₁, ..., nₖ₋₁`, the canonical ordering is the sequence `nᵢ₀, nᵢ₁, ..., nᵢₖ₋₁` such that `nᵢⱼ < nᵢⱼ₊₁` under lexicographic comparison of UTF-8 byte sequences.

Concretely, for an SCC `{ Foo.beta, Foo.alpha }`, the canonical order is `[Foo.alpha, Foo.beta]` because `"Foo.alpha" < "Foo.beta"` lexicographically.

### 3.2 PinId Consequence

The shared pin's content is the selector law applied to the component laws in canonical order. Any deviation in ordering produces a different PLAN value, which produces a different BLAKE3-256 hash, which produces a different PinId. Two compilers that order differently will produce incompatible PinIds for the same source, breaking content-addressed identity.

This is why canonical ordering is a key invariant of the language specification, not merely a compiler implementation detail.

---

## 4. Single Self-Recursive Definitions

A self-recursive definition compiles to a single PLAN law where index 0 provides self-reference. No shared pin is needed.

### 4.1 Gallowglass Input

```gallowglass
let factorial : Nat → Nat
  = λ n → match n {
    | 0 → 1
    | n → n * factorial (n - 1)
  }
```

### 4.2 PLAN Output

The law `{n a b}` has:
- `n` = name nat (encoding of `"factorial"`)
- `a` = 1 (one argument: `n`)
- `b` = body, where index 0 is the law itself and index 1 is `n`

```plan
<{%factorial 1
  (2 1               -- nat-case on arg 1 (n)
    1                 -- base case: n=0, return 1
    (λ pred →         -- inductive case: n = pred+1
      (Core.Nat.mul   -- n * factorial(n-1)
        1             -- n (arg 1)
        (0            -- self-reference (index 0) = factorial
          pred))))}>  -- applied to predecessor
```

Index 0 is the law itself. The recursive call `factorial (n - 1)` becomes `(0 pred)` — apply the self-reference to the predecessor.

---

## 5. Shared Pin Encoding

When an SCC contains `n` mutually recursive definitions, they are compiled into a single shared pin.

### 5.1 The Selector Law

The selector law is `{0 (n+1) 0}`:

- Name: `0` (anonymous — the selector has no user-visible name)
- Arity: `n + 1` (the selector itself at index 0, plus `n` law arguments at indices 1 through n)
- Body: `0` (returns index 0, which is the selector law itself — but this is never actually invoked; the selector is always partially applied)

**Correction on body semantics:** The body `0` means the selector returns its self-reference. But the selector is used through partial application: when applied to exactly one argument (an index `i`), the PLAN runtime's partial application mechanism selects argument `i` from the applied arguments. The selector is a row — a function that, when fully applied, provides access to all its arguments by position.

### 5.2 Construction

Given an SCC with `n` definitions producing laws `law₀, law₁, ..., lawₙ₋₁` in canonical order:

```
shared_pin = <({0 (n+1) 0} law₀ law₁ ... lawₙ₋₁)>
```

The outer `<...>` is opcode 4 (pin). The inner expression is the selector law partially applied to all `n` laws.

### 5.3 Indexing

To extract definition `i` from the shared pin, apply the pin's content to the nat `i`:

```
(shared_pin i)  →  lawᵢ
```

This works because the selector `{0 (n+1) 0}` applied to `n` laws is still awaiting one more argument (its arity is `n+1` but index 0 is self-reference, so it needs `n` explicit arguments plus one more to fully apply). When the final argument `i` is provided, the law body `0` (self-reference) plus the row mechanism selects the `i`-th positional argument.

More precisely, the row encoding uses PLAN's native application:

```
row = ({0 (n+1) 0} law₀ law₁ ... lawₙ₋₁)
```

This is a partially applied law with arity `n+1`, applied to `n` arguments. It awaits one more argument. When it receives nat `i`, the result is `lawᵢ` (the `i`-th argument, using 0-based indexing into the applied arguments).

### 5.4 Two-Definition Example

For an SCC `{ alpha, beta }` (canonical order: alpha at index 0, beta at index 1):

```
selector = {0 3 0}                    -- arity 3: self + 2 laws
row      = ({0 3 0} law_alpha law_beta)  -- partially applied, awaits 1 arg
pin      = <({0 3 0} law_alpha law_beta)>

(pin 0)  →  law_alpha
(pin 1)  →  law_beta
```

---

## 6. Lambda Lifting

Each law in a mutual recursion group needs to call other laws in the group. Since laws cannot natively reference other laws, the compiler adds the shared pin as an additional argument to each law. Cross-references become applications of that argument to the appropriate index.

### 6.1 Transformation

For each law in the SCC:

1. **Add one argument.** The law's arity increases by 1. The new argument (the shared pin) is always the first explicit argument — index 1 in PLAN terms (index 0 remains self-reference).

2. **Rewrite cross-references.** Every reference to another definition in the SCC becomes an application of the shared-pin argument to the target's index in the canonical ordering.

3. **Rewrite self-references.** Self-references can use either index 0 (native PLAN self-reference) or `(shared_pin_arg own_index)`. Both are equivalent. The compiler uses index 0 for efficiency.

### 6.2 Argument Layout

After lambda lifting, a law's arguments are ordered:

```
Index 0:  self-reference (PLAN built-in)
Index 1:  shared pin (added by lambda lifting)
Index 2+: original arguments
```

If the definition also has typeclass dictionary arguments (from dictionary elaboration), the full layout is:

```
Index 0:  self-reference
Index 1:  shared pin
Index 2:  first dictionary argument
...
Index k:  last dictionary argument
Index k+1: first user-visible argument
...
```

Shared pin is always first among the explicit arguments. Dictionaries follow. User-visible arguments are last.

### 6.3 Cross-Reference Compilation

Given definitions `alpha` (index 0) and `beta` (index 1) in the shared pin:

- Inside `alpha`'s body, a call to `beta` compiles to: `((1) 1)` applied to beta's arguments — that is, extract `beta` from the shared pin (argument 1) by applying index 1, then apply the result to beta's arguments.

Wait — let me be precise. Inside `alpha`'s law body:
- Index 0 = `alpha` itself (self-reference)
- Index 1 = the shared pin
- Index 2+ = alpha's other arguments

A call to `beta(x)` inside alpha becomes:

```plan
((1 1) x)
```

Where `(1 1)` means: take argument 1 (the shared pin) and apply it to nat 1 (beta's index). This extracts `law_beta` from the shared pin. Then apply `law_beta` to `x`.

But `law_beta` also expects the shared pin as its first argument. So the actual call is:

```plan
((1 1) 1 x)
```

That is: extract beta from the shared pin, pass the shared pin to beta (so beta can make its own cross-references), then pass `x`.

### 6.4 Caller Indirection

When code *outside* the SCC calls a definition from the group, it goes through the pin. The compiler emits a wrapper that extracts the law and passes the shared pin:

```plan
-- External call to alpha(x):
let alpha_pin = (shared_pin 0)       -- extract law_alpha
(alpha_pin shared_pin x)             -- pass shared_pin, then user args
```

In practice, the compiler may emit a top-level binding that closes over the shared pin, so external callers see a normal pinned function with the expected arity (no shared-pin argument visible externally).

---

## 7. PLAN Output Examples

### 7.1 Single Self-Recursive Function: Fibonacci

**Gallowglass input:**

```gallowglass
let fib : Nat → Nat
  = λ n → match n {
    | 0 → 0
    | 1 → 1
    | n → fib (n - 1) + fib (n - 2)
  }
```

**PLAN output:**

```plan
<{%fib 1
  (2 1                          -- nat-case on n (arg 1)
    0                           -- fib(0) = 0
    (λ pred →                   -- n ≥ 1, pred = n-1
      (2 pred                   -- nat-case on pred
        1                       -- fib(1) = 1
        (λ pred2 →              -- n ≥ 2, pred2 = n-2
          (Core.Nat.add
            (0 pred)            -- fib(n-1): self-ref applied to pred
            (0 pred2))))))}>    -- fib(n-2): self-ref applied to pred2
```

No shared pin. Index 0 is self-reference. Single pinned law.

### 7.2 Two Mutually Recursive Functions: is_even / is_odd

**Gallowglass input:**

```gallowglass
let is_even : Nat → Bool
  = λ n → match n {
    | 0 → True
    | n → is_odd (n - 1)
  }

let is_odd : Nat → Bool
  = λ n → match n {
    | 0 → False
    | n → is_even (n - 1)
  }
```

**SCC analysis:** `{ is_even, is_odd }` form a single SCC.

**Canonical order:** `[is_even, is_odd]` (lexicographic: `"is_even" < "is_odd"`).

- `is_even` is at index 0
- `is_odd` is at index 1

**Lambda-lifted laws:**

Each law gains one additional argument (the shared pin) at index 1.

```plan
law_is_even = {%is_even 2          -- arity 2: shared_pin + n
  (2 2                              -- nat-case on arg 2 (n)
    %True                           -- is_even(0) = True
    (λ pred →                       -- n ≥ 1
      ((1 1) 1 pred)))}            -- is_odd(pred): extract is_odd from
                                    --   shared_pin (arg 1) at index 1,
                                    --   pass shared_pin (arg 1), pass pred

law_is_odd = {%is_odd 2            -- arity 2: shared_pin + n
  (2 2                              -- nat-case on arg 2 (n)
    %False                          -- is_odd(0) = False
    (λ pred →                       -- n ≥ 1
      ((1 0) 1 pred)))}            -- is_even(pred): extract is_even from
                                    --   shared_pin (arg 1) at index 0,
                                    --   pass shared_pin (arg 1), pass pred
```

**Shared pin:**

```plan
shared_pin = <({0 3 0} law_is_even law_is_odd)>
```

The selector `{0 3 0}` has arity 3 (self + 2 laws). Applied to both laws, it awaits one more argument (the index).

**External access:**

```plan
is_even = λ n → ((shared_pin 0) shared_pin n)
is_odd  = λ n → ((shared_pin 1) shared_pin n)
```

### 7.3 Three Mutually Recursive Functions

**Gallowglass input:**

```gallowglass
let mod3_is_0 : Nat → Bool
  = λ n → match n {
    | 0 → True
    | n → mod3_is_2 (n - 1)
  }

let mod3_is_1 : Nat → Bool
  = λ n → match n {
    | 0 → False
    | n → mod3_is_0 (n - 1)
  }

let mod3_is_2 : Nat → Bool
  = λ n → match n {
    | 0 → False
    | n → mod3_is_1 (n - 1)
  }
```

**SCC analysis:** `{ mod3_is_0, mod3_is_1, mod3_is_2 }` form a single SCC.

**Canonical order:** `[mod3_is_0, mod3_is_1, mod3_is_2]`

- `mod3_is_0` at index 0
- `mod3_is_1` at index 1
- `mod3_is_2` at index 2

**Lambda-lifted laws:**

```plan
law_0 = {%mod3_is_0 2              -- arity 2: shared_pin + n
  (2 2                              -- nat-case on n
    %True                           -- mod3_is_0(0) = True
    (λ pred →
      ((1 2) 1 pred)))}            -- mod3_is_2(pred)

law_1 = {%mod3_is_1 2
  (2 2
    %False                          -- mod3_is_1(0) = False
    (λ pred →
      ((1 0) 1 pred)))}            -- mod3_is_0(pred)

law_2 = {%mod3_is_2 2
  (2 2
    %False                          -- mod3_is_2(0) = False
    (λ pred →
      ((1 1) 1 pred)))}            -- mod3_is_1(pred)
```

**Shared pin:**

```plan
shared_pin = <({0 4 0} law_0 law_1 law_2)>
```

Selector arity is 4 (self + 3 laws).

**External access:**

```plan
mod3_is_0 = λ n → ((shared_pin 0) shared_pin n)
mod3_is_1 = λ n → ((shared_pin 1) shared_pin n)
mod3_is_2 = λ n → ((shared_pin 2) shared_pin n)
```

---

## 8. Glass IR Representation

In Glass IR, mutual recursion groups are rendered with the `@!` (compiler-introduced pin) syntax, using a grouped pin block.

### 8.1 GroupedPin Syntax

```
@![pin#<hash>] {
  let <name₀> [idx 0] : <Type₀> = <body₀>
  let <name₁> [idx 1] : <Type₁> = <body₁>
  ...
  let <nameₙ₋₁> [idx (n-1)] : <Typeₙ₋₁> = <bodyₙ₋₁>
}
```

The `pin#<hash>` is the BLAKE3-256 hash of the shared pin. Each definition within the group is annotated with its index.

### 8.2 Example: is_even / is_odd in Glass IR

```gallowglass
@![pin#7a3f2c] {
  let Mod.is_even [idx 0] : Nat → Bool
    = λ group n → match n {
      | 0 → True
      | n → (group 1) group (n - 1)   -- is_odd via shared pin
    }

  let Mod.is_odd [idx 1] : Nat → Bool
    = λ group n → match n {
      | 0 → False
      | n → (group 0) group (n - 1)   -- is_even via shared pin
    }
}
```

The `group` parameter is the shared pin, made explicit in Glass IR. Cross-references are visible as `(group <index>)` applications.

### 8.3 External References to Grouped Definitions

Outside the group, references to individual definitions appear with the shared pin's hash and the definition's index:

```gallowglass
let result = pin#7a3f2c.is_even 42
```

In elaborated Glass IR, this expands to:

```gallowglass
let result = (pin#7a3f2c 0) pin#7a3f2c 42
```

---

## 9. PinId Stability

PinId stability is the property that two independent compilers, given the same Gallowglass source, produce the same PinId for every definition. For mutual recursion groups, PinId stability depends on three factors:

1. **Canonical ordering.** The definitions must appear in the same order within the shared pin. Lexicographic ordering by fully qualified name guarantees this.

2. **Identical law encoding.** Each law in the group must have the same name nat, arity, and body. The lambda lifting transformation must be deterministic.

3. **Identical selector.** The selector law `{0 (n+1) 0}` is determined entirely by the SCC size `n`.

If any of these differ, the shared pin's PLAN content differs, its BLAKE3-256 hash differs, and the PinId differs. Downstream pins that reference definitions from the group will also get different PinIds, cascading through the Merkle-DAG.

The canonical ordering rule is the critical invariant. The other two factors follow mechanically from a correct compiler. But ordering is a design choice — a compiler that chose alphabetical-by-local-name instead of lexicographic-by-fully-qualified-name would produce different PinIds. The specification fixes the choice.

---

## 10. Module Boundary

**Mutual recursion is bounded by module.** Cross-module mutual recursion is not allowed. The compiler rejects it with an error.

### 10.1 Why

Modules are compiled independently. A module's compiled output is a set of pins. Those pins are identified by BLAKE3-256 hashes of their content. If module A and module B had mutually recursive definitions, they would need to share a pin. That shared pin's hash would depend on definitions from both modules. But module A's compilation would need module B's compiled output (to include B's laws in the shared pin), and module B's compilation would need module A's compiled output. This is a circular dependency — structurally impossible in a Merkle-DAG.

### 10.2 Workaround

If two modules need mutually recursive behavior, factor the mutually recursive definitions into a single module and have both modules depend on it. This is a structural constraint, not a limitation — it makes the dependency graph explicit.

```
-- REJECTED: cross-module mutual recursion
mod A { let f = ... B.g ... }
mod B { let g = ... A.f ... }

-- ACCEPTED: factor into shared module
mod Shared { let f = ... g ...
             let g = ... f ... }
mod A { use Shared { f } }
mod B { use Shared { g } }
```

---

## 11. Interaction with Typeclasses

When a definition in a mutual recursion group has typeclass constraints, dictionary elaboration adds dictionary arguments to the law. These interact with the shared-pin argument from lambda lifting.

### 11.1 Argument Order

The argument order is fixed:

```
Index 0:    self-reference (PLAN built-in)
Index 1:    shared pin (lambda lifting)
Index 2:    first dictionary argument (dictionary elaboration)
...
Index 1+d:  last dictionary argument (d dictionaries total)
Index 2+d:  first user-visible argument
...
Index 1+d+u: last user-visible argument (u user arguments total)
```

Total law arity: `1 + d + u` (shared pin + dictionaries + user arguments). Index 0 (self-reference) does not count toward arity.

### 11.2 Example

```gallowglass
let even_elements : ∀ a. Eq a => List a → List a
  = λ xs → match xs {
    | Nil → Nil
    | Cons x rest → if is_even (length xs)
        then Cons x (odd_elements rest)
        else odd_elements rest
  }

let odd_elements : ∀ a. Eq a => List a → List a
  = λ xs → match xs {
    | Nil → Nil
    | Cons x rest → if is_odd (length xs)
        then Cons x (even_elements rest)
        else even_elements rest
  }
```

After dictionary elaboration and lambda lifting, `even_elements` has the law signature:

```plan
{%even_elements 3                   -- arity 3
  ...}                              -- body uses:
                                    --   index 0: self-reference
                                    --   index 1: shared_pin
                                    --   index 2: eq_dict
                                    --   index 3: xs
```

Cross-reference to `odd_elements` inside `even_elements`:

```plan
((1 1) 1 2 rest)                    -- (shared_pin 1) shared_pin eq_dict rest
```

The shared pin is passed first, then the dictionary, then user arguments.

---

## 12. Complexity

| Operation | Complexity | Notes |
|---|---|---|
| Dependency graph construction | O(V + E) | One pass over all definitions; V = definitions, E = references |
| Tarjan's SCC algorithm | O(V + E) | Linear in the graph size |
| Canonical ordering within SCC | O(k log k) | Sort k names in the SCC; k is typically small (2-5) |
| Lambda lifting | O(k) per SCC | Add one argument per law, rewrite cross-references |
| Shared pin construction | O(k) | Build one selector law and apply it to k laws |
| Cross-reference rewrite | O(1) per reference | Replace name with `(shared_pin_arg index)` |

The total compilation cost for mutual recursion handling is **O(V + E)** dominated by graph construction and Tarjan's algorithm. The per-SCC work is O(k log k) for sorting, but k (the SCC size) is bounded by V and is typically very small in practice. Lambda lifting and shared pin construction are linear in the SCC size.
