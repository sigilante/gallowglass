# Pattern Match Exhaustiveness Checker

**Spec version:** 0.1
**Depends on:** SPEC.md, spec/06-surface-syntax.md

This document specifies the pattern match exhaustiveness checker for Gallowglass. The checker runs during code generation (SPEC.md Section 9.1) and statically verifies that every `match` expression covers all possible values of its scrutinee type. When the checker cannot verify coverage, it requires an explicit catch-all arm and names the reason it cannot proceed.

---

## 1. Overview

Non-exhaustive pattern matches are a pervasive source of runtime crashes in languages that do not enforce coverage. A `match` on `Result a b` that handles `Ok` but not `Err` compiles silently in many languages and crashes when the unhandled case occurs.

Gallowglass eliminates this class of error statically. The exhaustiveness checker guarantees:

1. **Every value is covered.** For every possible value of the scrutinee type, at least one match arm's pattern matches it.
2. **Redundant arms are flagged.** If a match arm can never be reached because all values it would match are already covered by earlier arms, the compiler emits a warning.
3. **Undecidable cases are explicit.** When the checker cannot determine coverage (guards, infinite types, opaque types), it requires a catch-all `| _ ->` arm and names the specific `DeferralReason` in the error message.

The checker operates on typed AST nodes. It requires type information to know the complete constructor set of the scrutinee type.

---

## 2. Pattern Language

The checker handles the full pattern grammar defined in `spec/06-surface-syntax.md` Section 7. The pattern forms are:

| Pattern Form | Syntax | Coverage |
|---|---|---|
| Constructor | `Cons h t`, `None`, `Ok v` | Covers one constructor of a sum type |
| Literal | `0`, `1`, `"hello"` | Covers one value of an infinite domain |
| Variable | `x` | Covers everything (binds the value) |
| Wildcard | `_` | Covers everything (discards the value) |
| Tuple | `(a, b)` | Covers tuples; sub-patterns refine each component |
| Record | `{ x, y = pat }` | Covers records; sub-patterns refine named fields |
| Or-pattern | `p1 \| p2` | Covers the union of p1 and p2 |
| As-pattern | `p as name` | Coverage of p; additionally binds the whole value |
| Cons (infix) | `h :: t` | Desugars to `Cons h t` |
| List | `[a, b, c]` | Desugars to nested `Cons` with `Nil` terminator |
| Empty list | `[]` | Desugars to `Nil` |
| Nested | Arbitrarily deep | Sub-patterns checked recursively |

The checker operates after desugaring: `h :: t` is `Cons h t`, `[a, b]` is `Cons a (Cons b Nil)`, and `[]` is `Nil`. All list sugar is resolved before exhaustiveness analysis.

---

## 3. Algorithm

The exhaustiveness checker uses a pattern matrix approach based on Maranget's usefulness algorithm (Maranget, "Warnings for pattern matching," JFP 2007). The key concepts are the **pattern matrix**, the **usefulness predicate**, and **constructor specialization**.

### 3.1 Pattern Matrix

A `match` expression with _m_ arms and a scrutinee of type _T_ is represented as a pattern matrix _P_ of dimensions _m x n_, where _n_ is the number of scrutinee positions (initially 1, expanding as nested patterns are decomposed).

For a simple match:

```gallowglass
match xs {
  | Nil       -> 0
  | Cons h t  -> 1 + length t
}
```

The pattern matrix is:

```
P = | Nil      |
    | Cons h t |
```

This is a 2x1 matrix. When the checker specializes by the `Cons` constructor (which has two fields), the second row expands to a 1x2 matrix for recursive checking:

```
P_Cons = | h  t |
```

### 3.2 Usefulness

A pattern vector **q** = (q_1, ..., q_n) is **useful** with respect to a pattern matrix _P_ if there exists a value vector **v** = (v_1, ..., v_n) such that:

1. **v** matches **q** (i.e., each v_i matches q_i), and
2. **v** does not match any row in _P_ (i.e., for every row **p_i** in _P_, some component of **v** fails to match the corresponding component of **p_i**).

Intuitively: **q** is useful if it can match something that no row in _P_ already matches.

### 3.3 Exhaustiveness and Redundancy

**Exhaustiveness.** The match is exhaustive if and only if the wildcard vector **(_, _, ..., _)** is **not useful** with respect to _P_. If the wildcard is useful, there exists a value that no arm covers.

**Redundancy.** Row _i_ of _P_ is redundant if and only if **p_i** is **not useful** with respect to the submatrix _P[1..i-1]_ (all rows preceding it). A redundant row can never be reached.

### 3.4 Recursive Decomposition

The usefulness check proceeds by case analysis on the first column of the matrix. Let _Sigma_ be the set of constructors appearing in the first column.

**Definition: Specialize(c, P).** Given a constructor _c_ of arity _a_, the specialization of _P_ by _c_ is a new matrix formed by:

- For each row whose first column is _c_ applied to sub-patterns (p_1, ..., p_a): replace the row with (p_1, ..., p_a, remaining columns).
- For each row whose first column is a wildcard `_` or variable: replace the row with (_, ..., _ [a times], remaining columns).
- Rows whose first column is a different constructor _c'_ (where c' != c) are dropped.

**Definition: Default(P).** The default matrix is formed by:

- Dropping all rows whose first column is a constructor pattern.
- For each row whose first column is a wildcard or variable: keep the row with the first column removed.

The algorithm:

```
Useful(P, q):
  if P has zero columns:
    return P has zero rows     -- q is useful iff matrix is empty

  let Sigma = constructors in first column of P
  let c_1 = first component of q

  if c_1 is a constructor c of arity a:
    let P' = Specialize(c, P)
    let q' = (sub-patterns of c_1) ++ (rest of q)
    return Useful(P', q')

  if c_1 is a wildcard or variable:
    let T = type of the first scrutinee position
    if Sigma is complete (covers all constructors of T):
      -- Check usefulness under each constructor specialization
      return exists c in constructors(T):
        Useful(Specialize(c, P), (wildcards for c's arity) ++ (rest of q))
    else:
      -- Some constructors are missing from P; check the default matrix
      return Useful(Default(P), rest of q)
```

### 3.5 Sigma Completeness

_Sigma_ is **complete** when it contains every constructor of the scrutinee type _T_. This depends on the type (see Section 4). When _Sigma_ is complete, the wildcard case must check every constructor specialization. When _Sigma_ is incomplete, the default matrix suffices — the wildcard can match any missing constructor.

---

## 4. Type Information

The checker requires the **constructor signature** of each scrutinee type: the set of constructors and their arities. This information comes from the typed AST.

### 4.1 Algebraic Types (Finite Constructor Set)

For user-defined sum types, the constructor set is finite and fully known:

```gallowglass
type List a =
  | Nil
  | Cons a (List a)
```

Constructors: `{Nil (arity 0), Cons (arity 2)}`. A match on `List a` is exhaustive if and only if both `Nil` and `Cons` are covered (with sub-patterns that are themselves exhaustive).

```gallowglass
type Ordering =
  | Less
  | Equal
  | Greater
```

Constructors: `{Less (arity 0), Equal (arity 0), Greater (arity 0)}`. Three nullary constructors.

### 4.2 Bool

`Bool` is a sum type with exactly two constructors: `True` (arity 0) and `False` (arity 0). It is **not** a literal type. Pattern matching on `Bool` by constructor is finite and can be checked exhaustively:

```gallowglass
match flag {
  | True  -> "yes"
  | False -> "no"
}
-- Exhaustive: both constructors covered.
```

### 4.3 Nat

`Nat` has infinitely many values: `0, 1, 2, ...`. Each literal pattern covers exactly one value. No finite set of literal patterns can cover all of `Nat`. A match on `Nat` always requires a catch-all arm unless the scrutinee type has been refined (see Section 13).

Constructor signature: **infinite**. The checker treats `Nat` as having an unbounded constructor set where each natural number is a distinct nullary constructor.

### 4.4 Text and Bytes

`Text` and `Bytes` have infinitely many values. Like `Nat`, each literal pattern covers exactly one value. A match on `Text` or `Bytes` always requires a catch-all arm.

### 4.5 Int and Fixed-Width Integers

`Int` is unbounded in both directions and always requires a catch-all. Fixed-width integers (`Int32`, `Int64`, `Uint32`, `Uint64`) have finite but extremely large domains. The checker treats them as infinite for practical purposes — exhaustive enumeration of 2^32 or 2^64 arms is not feasible.

### 4.6 Opaque Types

Types declared as `Opaque[v]` in `external mod` declarations have no visible constructors. The checker has no information about what values are possible. Matches on opaque types always require a catch-all arm.

```gallowglass
-- In external mod Sqlite:
type Connection : Opaque[~]

-- Matching on Connection is not possible without a catch-all:
match conn {
  | _ -> handle_connection conn
}
```

### 4.7 Product Types (Records and Tuples)

Product types have exactly one constructor. A match on a record or tuple is exhaustive if the sub-patterns for each field are exhaustive. The constructor set is `{(arity = number of fields)}`.

```gallowglass
type Point = { x : Real, y : Real }
-- Single constructor, arity 2.
```

---

## 5. Guard Interaction

Guards (`| pat if cond -> ...`) make exhaustiveness undecidable in general. The condition `cond` is an arbitrary expression whose truth value cannot be statically determined.

**Rule: Guarded arms do not contribute to coverage.**

A guarded arm is treated as if it might never match. When computing the pattern matrix for exhaustiveness, guarded arms are excluded from the matrix. They contribute to redundancy analysis (a guarded arm after a wildcard is still unreachable) but not to coverage.

The practical consequence: if every arm in a match is guarded, the match is non-exhaustive. The final arm must be unguarded for the match to be considered exhaustive.

```gallowglass
-- Non-exhaustive: all arms are guarded
match n {
  | x if x > 0  -> "positive"
  | x if x < 0  -> "negative"    -- hypothetical, n : Nat
}
-- Error: non-exhaustive match (DeferralReason: Guard)

-- Exhaustive: final arm is unguarded catch-all
match n {
  | 0             -> "zero"
  | n if n < 10   -> "small"
  | _             -> "large"
}
-- OK: catch-all covers everything guards might miss
```

---

## 6. Or-Pattern Semantics

An or-pattern `p1 | p2` covers the union of values matched by `p1` and `p2`. In the pattern matrix, or-patterns are expanded: a row containing `p1 | p2` is replaced by two rows, one for `p1` and one for `p2`.

**Variable binding constraint.** Both branches of an or-pattern must bind the same set of variables with compatible types. This is enforced during type checking (before the exhaustiveness checker runs). The checker can assume this invariant holds.

```gallowglass
-- Or-pattern: Circle and Square both bind r
match shape {
  | Circle r | Square r  -> area r
  | Rect w h             -> w * h
}
```

For exhaustiveness analysis, this is equivalent to:

```gallowglass
match shape {
  | Circle r  -> area r
  | Square r  -> area r
  | Rect w h  -> w * h
}
```

The checker expands or-patterns before constructing the pattern matrix. Usefulness and redundancy operate on the expanded form.

---

## 7. Nested Pattern Decomposition

Nested patterns are handled by the recursive specialization step of the algorithm (Section 3.4). When the checker specializes by a constructor, sub-patterns become new columns in the matrix, and the algorithm recurses.

### 7.1 Example: Two-Level Nesting

```gallowglass
type Tree a =
  | Leaf a
  | Node (Tree a) a (Tree a)

match t {
  | Leaf x                    -> x
  | Node (Leaf l) x (Leaf r)  -> l + x + r
  | Node left x right         -> fold_tree left + x + fold_tree right
}
```

The initial matrix (1 column):

```
P = | Leaf x                   |
    | Node (Leaf l) x (Leaf r) |
    | Node left x right        |
```

Specialize by `Leaf` (arity 1):

```
P_Leaf = | x |
```

The wildcard `_` is not useful against `P_Leaf` (the variable `x` already covers everything). `Leaf` is fully covered.

Specialize by `Node` (arity 3):

```
P_Node = | (Leaf l)  x  (Leaf r) |
         | left      x  right    |
```

Recurse on the first column (type `Tree a`). Constructors present: `{Leaf}`. Not complete (missing `Node`). Check the default matrix:

```
Default(P_Node) = | x  right |
```

The wildcard is not useful. The second row covers all remaining `Node` values. `Node` is fully covered.

The match is exhaustive.

### 7.2 Depth Limit

The checker imposes a configurable maximum recursion depth (default: 64 levels of nesting). Beyond this depth, the checker defers with `DeferralReason: OutOfBounds`. In practice, patterns deeper than a few levels are rare.

---

## 8. Literal Patterns

Literal patterns interact with exhaustiveness through the type's constructor signature.

### 8.1 Nat Literals

Each `Nat` literal covers exactly one value. The set of natural numbers is infinite, so no finite set of `Nat` literal patterns is exhaustive. The checker always requires a catch-all for `Nat` unless a contract refinement narrows the domain (Section 13).

```gallowglass
match n {
  | 0 -> "zero"
  | 1 -> "one"
  | _ -> "other"
}
-- Exhaustive: catch-all present.
```

```gallowglass
match n {
  | 0 -> "zero"
  | 1 -> "one"
}
-- Non-exhaustive. Missing: 2, 3, 4, ...
-- DeferralReason: InfiniteType
```

### 8.2 Text and Bytes Literals

Identical to `Nat`: each literal covers one value from an infinite domain. A catch-all is always required.

```gallowglass
match command {
  | "quit" -> exit 0
  | "help" -> show_help
  | _      -> unknown_command command
}
-- Exhaustive: catch-all present.
```

### 8.3 Bool Is Not a Literal

`Bool` values `True` and `False` are constructors, not literals. Matching on `Bool` by constructor is finite and does not require a catch-all when both constructors are present. See Section 4.2.

---

## 9. Redundancy Detection

The checker detects redundant (unreachable) match arms using the usefulness predicate. A match arm at row _i_ is redundant if its pattern vector is not useful with respect to the submatrix of all preceding rows _P[1..i-1]_.

**Redundancy is a warning, not an error.** The program is still correct --- every value is covered, and the redundant arm simply never executes. The warning alerts the programmer to dead code.

### 9.1 Examples

```gallowglass
match xs {
  | Nil      -> 0
  | Cons h t -> 1 + length t
  | Nil      -> 42             -- Warning: redundant arm (Nil already covered)
}
```

```gallowglass
match x {
  | _    -> "anything"
  | None -> "nothing"          -- Warning: redundant arm (_ covers everything)
}
```

### 9.2 Redundancy Under Guards

A guarded arm is never considered to make later arms redundant (since it might not match). However, an unguarded arm that follows a wildcard is redundant regardless of intervening guards:

```gallowglass
match n {
  | _             -> "default"
  | 0 if flag     -> "zero"     -- Warning: redundant (wildcard above is unguarded)
}
```

---

## 10. Error Messages

When the checker detects a non-exhaustive match, the error message must provide actionable information.

### 10.1 Required Components

1. **Missing patterns.** The specific constructor(s) or value(s) not covered by any arm.
2. **Concrete example.** A representative uncovered value, expressed as a pattern the programmer could add.
3. **Deferral reason.** If the checker cannot determine coverage, the `DeferralReason` explaining why.

### 10.2 Example Error Messages

**Missing constructor:**

```
error[E0301]: non-exhaustive match
  --> src/example.gls:14:3
   |
14 |   match result {
   |   ^^^^^ patterns not covered
   |
   = missing: Err _
   = example: Err "connection refused"
   = help: add `| Err e -> ...` or `| _ -> ...` to cover all cases
```

**Infinite type without catch-all:**

```
error[E0302]: non-exhaustive match (InfiniteType)
  --> src/example.gls:20:3
   |
20 |   match n {
   |   ^^^^^ Nat has infinitely many values
   |
   = covered: 0, 1, 2
   = missing: 3, 4, 5, ...
   = help: add `| _ -> ...` to handle remaining values
```

**Guard preventing coverage analysis:**

```
error[E0303]: non-exhaustive match (Guard)
  --> src/example.gls:26:3
   |
26 |   match x {
   |   ^^^^^ guarded arms do not guarantee coverage
   |
   = note: arm at line 27 has guard `if x > 0`; may not match
   = help: add an unguarded `| _ -> ...` as the final arm
```

**Opaque type:**

```
error[E0304]: non-exhaustive match (AbstractType)
  --> src/example.gls:32:3
   |
32 |   match conn {
   |   ^^^^^ Connection is opaque; no constructors visible
   |
   = help: add `| _ -> ...` to handle all values
```

### 10.3 Redundancy Warning

```
warning[W0301]: redundant match arm
  --> src/example.gls:18:5
   |
18 |   | Nil -> 42
   |   ^^^^^^^^^ this arm is unreachable
   |
   = note: Nil is already covered by arm at line 15
```

---

## 11. DeferralReasons for Exhaustiveness

When the checker cannot statically verify coverage, it requires a catch-all arm and reports one of the following `DeferralReason` values:

| DeferralReason | Condition | Resolution |
|---|---|---|
| `Guard` | One or more arms have guards; the checker cannot determine whether the guard conditions collectively cover all values. | Add an unguarded catch-all as the final arm. |
| `InfiniteType` | The scrutinee type has infinitely many values (`Nat`, `Text`, `Bytes`, `Int`, fixed-width integers). No finite set of literal patterns is exhaustive. | Add a catch-all arm. |
| `AbstractType` | The scrutinee type is opaque (`Opaque[v]`). The checker has no visibility into what values exist. | Add a catch-all arm. |
| `OutOfBounds` | Pattern nesting exceeds the checker's analysis depth limit (default: 64 levels). | Add a catch-all arm or restructure the match to reduce nesting depth. |

These reasons are first-class values of the `DeferralReason` type and appear in compiler error messages. They are distinct from the contract-system `DeferralReason` values (`NonLinear`, `HigherOrder`, etc.) defined in SPEC.md Section 3.6; the exhaustiveness checker defines its own set.

---

## 12. Examples

### 12.1 Complete Coverage of a Sum Type

```gallowglass
type Shape =
  | Circle Real
  | Square Real
  | Rect Real Real

let describe : Shape -> Text
  = fn shape -> match shape {
    | Circle r  -> "circle of radius #{r}"
    | Square s  -> "square of side #{s}"
    | Rect w h  -> "rectangle #{w} by #{h}"
  }
-- Exhaustive: all three constructors covered.
```

### 12.2 Missing Constructor Detected

```gallowglass
let describe : Shape -> Text
  = fn shape -> match shape {
    | Circle r  -> "circle"
    | Square s  -> "square"
  }
-- Error: non-exhaustive match.
-- Missing: Rect _ _
-- Example: Rect 1.0 2.0
```

### 12.3 Redundant Arm Detected

```gallowglass
let describe : Shape -> Text
  = fn shape -> match shape {
    | Circle r  -> "circle"
    | Square s  -> "square"
    | Rect w h  -> "rectangle"
    | Circle r  -> "another circle"   -- Warning: redundant arm
  }
```

### 12.4 Guard Requiring Catch-All

```gallowglass
let classify : Nat -> Text
  = fn n -> match n {
    | 0            -> "zero"
    | n if n < 10  -> "small"
    | n if n < 100 -> "medium"
    | _            -> "large"
  }
-- Exhaustive: unguarded catch-all present.
```

Without the catch-all:

```gallowglass
let classify : Nat -> Text
  = fn n -> match n {
    | 0            -> "zero"
    | n if n < 10  -> "small"
    | n if n < 100 -> "medium"
  }
-- Error: non-exhaustive match (Guard, InfiniteType).
-- Guarded arms do not guarantee coverage.
```

### 12.5 Nat Patterns Requiring Catch-All

```gallowglass
let factorial : Nat -> Nat
  = fn n -> match n {
    | 0 -> 1
    | n -> n * factorial (n - 1)
  }
-- Exhaustive: variable pattern `n` is a catch-all.
```

```gallowglass
let small_name : Nat -> Text
  = fn n -> match n {
    | 0 -> "zero"
    | 1 -> "one"
    | 2 -> "two"
  }
-- Error: non-exhaustive match (InfiniteType).
-- Missing: 3, 4, 5, ...
```

### 12.6 Nested Pattern Coverage

```gallowglass
type Expr =
  | Lit Nat
  | Add Expr Expr
  | Neg Expr

let eval : Expr -> Int
  = fn e -> match e {
    | Lit n          -> Core.Int.from_nat n
    | Add (Lit a) (Lit b) -> Core.Int.from_nat (a + b)
    | Add left right -> eval left + eval right
    | Neg inner      -> Core.Int.negate (eval inner)
  }
-- Exhaustive: Lit covered, Add covered (second arm is a specialization,
-- third arm catches remaining Add cases), Neg covered.
```

### 12.7 Or-Pattern Coverage

```gallowglass
type Color =
  | Red
  | Green
  | Blue
  | Yellow

let is_primary : Color -> Bool
  = fn c -> match c {
    | Red | Blue | Yellow -> True
    | Green               -> False
  }
-- Exhaustive: all four constructors covered.
-- (Red, Blue, Yellow via or-pattern; Green explicitly.)
```

---

## 13. Interaction with Contracts

Refined types can narrow the domain of a scrutinee, potentially making a match exhaustive that would otherwise require a catch-all.

### 13.1 Refinement Narrowing

When the scrutinee type carries a refinement predicate, the checker uses the refinement to restrict the set of possible values.

```gallowglass
let day_name : (d : Nat | d >= 1, d <= 7) -> Text
  = fn d -> match d {
    | 1 -> "Monday"
    | 2 -> "Tuesday"
    | 3 -> "Wednesday"
    | 4 -> "Thursday"
    | 5 -> "Friday"
    | 6 -> "Saturday"
    | 7 -> "Sunday"
  }
-- Exhaustive: refinement restricts Nat to {1, 2, 3, 4, 5, 6, 7},
-- all of which are covered.
```

Without the refinement, this match would be non-exhaustive (`Nat` is infinite).

### 13.2 Tier Interaction

The checker integrates with the contract discharge tiers (SPEC.md Section 3.6) as follows:

- **Tier 0 (syntactic):** Refinements that are trivially true or false by construction. The checker uses these without restriction.
- **Tier 1 (built-in):** Linear arithmetic over `Nat`/`Int`, propositional logic. The checker can use Tier 1 to determine that a refinement like `n >= 0, n < 3` restricts the domain to `{0, 1, 2}`.
- **Tier 2 and beyond:** Refinements outside Tier 1 are not used by the exhaustiveness checker. The checker falls back to the unrefined type and requires a catch-all if the unrefined type is infinite or otherwise uncoverable.

This means a refinement like `(n : Nat | is_prime n)` does not help the exhaustiveness checker --- `is_prime` is a higher-order predicate outside Tier 1. The match must include a catch-all.

### 13.3 Example: Refinement Outside Tier 1

```gallowglass
let prime_name : (p : Nat | is_prime p) -> Text
  = fn p -> match p {
    | 2 -> "two"
    | 3 -> "three"
    | 5 -> "five"
    | _ -> "other prime"
  }
-- Exhaustive: catch-all present.
-- The checker cannot use the is_prime refinement (outside Tier 1)
-- so it treats p as unrestricted Nat.
```

---

## 14. Implementation Notes

### 14.1 Algorithm Complexity

The worst-case complexity of the usefulness algorithm is exponential in the number of columns (scrutinee positions). In practice, patterns are shallow and the number of columns after specialization is small. The depth limit (Section 7.2) bounds the recursion.

### 14.2 Integration Point

The exhaustiveness checker runs during code generation (SPEC.md Section 9.1), after type checking. It receives:

- The pattern matrix from the `match` expression.
- The type of the scrutinee (from the typed AST).
- The constructor signatures of all types in scope (from the type environment).
- Any refinement predicates on the scrutinee type.

It produces:

- **Pass:** the match is exhaustive (possibly with redundancy warnings).
- **Fail:** the match is non-exhaustive, with missing patterns, concrete examples, and `DeferralReason`.

### 14.3 Interaction with Compilation

A non-exhaustive match is a **compile error**, not a warning. The program does not compile. This is consistent with the design principle that `Abort` is reserved for violated contracts at runtime --- a missing match arm is a static error, not a runtime `Abort`.

A redundant arm is a **warning**. The program compiles successfully. The redundant arm is still emitted in the PLAN output (it is dead code but does not affect correctness).
