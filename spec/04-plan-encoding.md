# PLAN Encoding Specification

**Spec version:** 0.1
**Depends on:** SPEC.md, spec/02-mutual-recursion.md, spec/06-surface-syntax.md

This document specifies how every Gallowglass construct compiles to PLAN. It is the authoritative reference for the code generator in both the bootstrap compiler and the self-hosting compiler.

Throughout this document, PLAN values are written using the notation from SPEC.md section 2.1:

```
<i>       -- Pin: content-addressed, globally deduplicated
{n a b}   -- Law: name n (nat), arity a (nat), body b (PLAN expression)
(f g)     -- App: left-associative application
@         -- Nat: arbitrary-precision natural number
```

Law bodies use de Bruijn-style argument indices: index 0 is the law itself (self-reference), indices 1..a are the arguments left to right.

Five opcodes (nat values 0-4):
- `0` — create a law: `(0 name arity body)`
- `1` — reflect (dispatch on constructor): `(1 pin_case law_case app_case nat_case value)`
- `2` — nat iteration (structural recursion on naturals): `(2 zero_case succ_case n)`
- `3` — increment a nat: `(3 n)` yields `n + 1`
- `4` — pin a value (normalize and content-address): `(4 value)` yields `<value>`

---

## 1. Function Compilation

A `let` declaration compiles to a PLAN law. The law's three fields are determined as follows:

- **Name `n`:** The function name encoded as a nat. Characters are encoded as UTF-8 bytes packed into a little-endian nat. The name is the fully qualified name in Glass IR (e.g., `Collections.List.map`).
- **Arity `a`:** The number of top-level lambda parameters. Nested lambdas in the body do not increase the law's arity; they produce a new law returned from the outer law.
- **Body `b`:** The compiled body expression, with parameter references replaced by de Bruijn indices 1..a (left to right) and self-reference as index 0.

### Example

```gallowglass
let add_one : Nat -> Nat
  = fn x -> (3 x)
```

The function name `add_one` encodes to a nat (its UTF-8 bytes as a little-endian nat). Call this nat `N_add_one`. Arity is 1 (one parameter `x`). The parameter `x` is index 1. The body applies opcode 3 (increment) to index 1.

```
{N_add_one 1 (3 1)}
```

### Multi-argument functions

```gallowglass
let add : Nat -> Nat -> Nat
  = fn x y -> Core.Nat.add x y
```

Arity is 2. `x` is index 1, `y` is index 2. Assuming `Core.Nat.add` is a pinned value `<P_add>`:

```
{N_add 2 (<P_add> 1 2)}
```

### Nested lambdas

```gallowglass
let make_adder : Nat -> Nat -> Nat
  = fn x -> fn y -> Core.Nat.add x y
```

The outer function has arity 1. The inner lambda compiles to a separate law. The outer law's body returns a law that closes over `x`. After lambda lifting (section 2), `x` becomes an additional argument to the inner law:

```
-- Inner law (lambda-lifted): takes x as argument 1, y as argument 2
{N_inner 2 (<P_add> 1 2)}

-- Outer law: partially applies inner law with x
{N_make_adder 1 ({N_inner 2 (<P_add> 1 2)} 1)}
```

The outer law applies the inner law to index 1 (the captured `x`), producing a partially applied law that still expects `y`.

---

## 2. Lambda Lifting

Closures do not exist in PLAN. Every law is a closed term: its body may only reference indices 0..a (self and arguments). Free variables in a lambda body are eliminated by lambda lifting.

**Procedure:**

1. Identify free variables in the lambda body (variables bound in an enclosing scope).
2. Add each free variable as an additional leading parameter of the lifted law.
3. At each call site, pass the free variables as extra arguments.

### Example

```gallowglass
let apply_offset : Nat -> List Nat -> List Nat
  = fn offset xs ->
      map (fn x -> Core.Nat.add x offset) xs
```

The inner lambda `fn x -> Core.Nat.add x offset` has `offset` as a free variable. Lambda lifting produces:

```
-- Lifted inner law: offset is now argument 1, x is argument 2
{N_inner 2 (<P_add> 2 1)}

-- Outer law: offset is 1, xs is 2
-- Partially applies the inner law with offset, then passes to map
{N_apply_offset 2 (<P_map> ({N_inner 2 (<P_add> 2 1)} 1) 2)}
```

The partial application `({N_inner 2 (<P_add> 2 1)} 1)` produces a value that still expects one argument (`x`), which `map` will supply.

---

## 3. Application

Gallowglass function application compiles directly to PLAN App nodes. Application is left-associative.

```gallowglass
f x y z
```

Compiles to:

```
(((f x) y) z)
```

Which is written `(f x y z)` by left-associative convention.

### Partial application

PLAN natively supports partial application. A law `{n a b}` applied to fewer than `a` arguments produces an App node that is not yet reducible. It remains as a partially applied value until the remaining arguments arrive.

```gallowglass
let inc : Nat -> Nat
  = Core.Nat.add 1
```

Compiles to:

```
{N_inc 1 (<P_add> 1 1)}
```

Note: the first `1` after `<P_add>` is the nat literal 1; the second `1` is the de Bruijn index for the parameter. Alternatively, this can be expressed as a direct partial application at the pin level:

```
(<P_add> 1)
```

The compiler may choose either form. Both are semantically equivalent under PLAN's lazy evaluation.

### Currying

All multi-argument functions in Gallowglass are curried. A function `a -> b -> c` is a law of arity 2, but it can also be viewed as a function that takes `a` and returns a function `b -> c`. PLAN's partial application semantics make this transparent.

---

## 4. Algebraic Types

### 4.1 Sum types

Sum types encode each constructor as a **tagged row**: a nat tag followed by the constructor's fields, applied left to right.

Constructor tags are assigned by **declaration order**, starting at 0.

```gallowglass
type Shape =
  | Circle  Nat          -- tag 0
  | Square  Nat          -- tag 1
  | Rect    Nat Nat      -- tag 2
```

Each constructor is a function that builds the tagged value:

```
Circle r   =  (0 r)           -- tag 0, one field
Square s   =  (1 s)           -- tag 1, one field
Rect w h   =  (2 w h)         -- tag 2, two fields
```

A nullary constructor is just the tag nat:

```gallowglass
type Ordering =
  | Less       -- tag 0
  | Equal      -- tag 1
  | Greater    -- tag 2
```

```
Less    =  0
Equal   =  1
Greater =  2
```

Constructor functions compile to laws:

```
-- Circle : Nat -> Shape
{N_Circle 1 (0 1)}

-- Rect : Nat -> Nat -> Shape
{N_Rect 2 (0 2 1)}
```

Wait -- the tag for `Rect` is 2. Let me be precise about the encoding. The tag is applied first, then the fields:

```
-- Circle : Nat -> Shape
{N_Circle 1 (0 1)}        -- (tag field1)

-- Square : Nat -> Shape
{N_Square 1 (1 1)}        -- (tag field1)

-- Rect : Nat -> Nat -> Shape
{N_Rect 2 (2 1 2)}        -- (tag field1 field2)
```

### 4.2 Record types

Record types are products. Fields are positional, ordered by declaration order. A record is encoded identically to a single-constructor sum type with tag 0.

```gallowglass
type Point = {
  x : Nat,
  y : Nat
}
```

A `Point` value is:

```
(0 x_val y_val)
```

The constructor function:

```
{N_Point 2 (0 1 2)}
```

Field access compiles to pattern matching on the single constructor (see section 5).

### 4.3 Parametric types

Type parameters are erased. A parametric type compiles identically to a monomorphic type with the same structure.

```gallowglass
type Result a b =
  | Ok  a       -- tag 0
  | Err b       -- tag 1
```

```
Ok v    =  (0 v)
Err e   =  (1 e)
```

---

## 5. Pattern Matching

Pattern matching compiles to a combination of opcode 1 (reflect) and opcode 2 (nat iteration), depending on what is being matched.

### 5.1 Matching on algebraic types

Algebraic type matching dispatches on the constructor tag (a nat), then extracts fields. The compilation proceeds in two steps:

1. **Tag dispatch:** Extract the tag nat and dispatch using opcode 2 (nat iteration) or nested conditionals.
2. **Field extraction:** Once the constructor is identified, extract fields by position.

The general strategy uses opcode 1 to inspect the structure: a tagged value like `(tag field1 field2)` is an App node. Opcode 1 dispatches on the four PLAN constructors (pin, law, app, nat). For a tagged sum, the value is either a bare nat (nullary constructor) or an App node.

```gallowglass
let area : Shape -> Nat
  = fn s -> match s {
      | Circle r   -> Core.Nat.mul r r
      | Square s   -> Core.Nat.mul s s
      | Rect w h   -> Core.Nat.mul w h
    }
```

The compiler generates code that:
1. Uses opcode 1 to determine if `s` is a nat (nullary constructor) or an App (constructor with fields).
2. For the App case, extracts the tag and fields.
3. Uses opcode 2 on the tag to select the appropriate branch.

The exact PLAN output depends on the compiler's case dispatch strategy. One canonical approach:

```
-- Dispatch on the tag extracted from the value.
-- For a value (tag f1 ... fn), the tag is the leftmost nat
-- at the spine of applications.
--
-- case_dispatch tag circle_branch square_branch rect_branch
-- uses nat iteration (opcode 2) to count down the tag:
--   tag 0 -> circle_branch
--   tag 1 -> square_branch
--   tag 2 -> rect_branch

{N_area 1
  (2                          -- opcode 2: nat iteration on tag
    (circle_body 1)           -- tag 0: Circle
    {N_step 2                 -- tag n+1: step function
      (2                      -- iterate again
        (square_body 1)       -- tag 1 (0 after one step): Square
        {N_step2 2
          (rect_body 1)       -- tag 2 (0 after two steps): Rect
        }
        2                    -- remaining tag
      )
    }
    (extract_tag 1)           -- the tag from the value
  )
}
```

The field extraction functions use opcode 1 to walk the App spine. For `(tag field1 field2)`, which is `((tag field1) field2)`:

- Opcode 1 on the whole value yields the App case with `head = (tag field1)` and `tail = field2`.
- Opcode 1 on `(tag field1)` yields the App case with `head = tag` and `tail = field1`.

### 5.2 Matching on Nat

Nat patterns use opcode 2 (nat iteration) directly.

```gallowglass
let is_zero : Nat -> Bool
  = fn n -> match n {
      | 0 -> 1    -- True
      | _ -> 0    -- False
    }
```

```
{N_is_zero 1
  (2           -- opcode 2: nat iteration
    1          -- zero case: return True (1)
    {N_k 2 0}  -- succ case: ignore predecessor, return False (0)
    1          -- the nat being matched (argument index 1)
  )
}
```

The succ case law takes two arguments (the accumulated/recursive result and the predecessor) but discards them, returning 0.

For matching specific nat literals:

```gallowglass
match n {
  | 0 -> "zero"
  | 1 -> "one"
  | _ -> "many"
}
```

This chains nat iteration: first check if `n` is 0, otherwise decrement and check if the predecessor is 0 (meaning `n` was 1), otherwise fall through to the default.

### 5.3 Matching on nested structures

Nested pattern matching (e.g., `Cons h (Cons _ Nil)`) compiles to nested dispatches. The compiler flattens nested patterns into a decision tree of opcode 1 and opcode 2 applications. See `spec/03-exhaustiveness.md` for the exhaustiveness analysis that validates the decision tree covers all cases.

---

## 6. Effect Handlers

Effect handlers compile via CPS (continuation-passing style) transformation. At the PLAN level, all functions are pure laws. The effect discipline is entirely erased; what remains is the CPS plumbing.

### 6.1 CPS transformation

An effectful computation `{Effect | r} a` compiles to a function that takes a handler record (a tuple of continuation-accepting functions) and produces a result.

An effect operation `op : Args -> Result` within a handler compiles to a call to the handler record's corresponding function, passing the operation's arguments and the current continuation.

The continuation `k` in a handler arm is a partially applied law representing "the rest of the computation after the effect operation."

### 6.2 Example

```gallowglass
eff State s {
  get : Unit -> s
  put : s    -> Unit
}

let run_state : forall s a. s -> {State s | r} a -> {r} (a, s)
  = fn s0 computation ->
      handle computation {
        | return x      -> fn s -> (x, s)
        | get    ()   k -> fn s -> k s  s
        | put    s'   k -> fn _ -> k () s'
      } s0
```

The handler compiles to a law that threads state through continuations. Each handler arm becomes a law:

```
-- return handler: takes the final value and current state, produces the pair
{N_return 2 (0 1 2)}          -- (x, s) = (0 x s)

-- get handler: takes unit arg, continuation k, and state s
-- calls k with s (the gotten value) and s (unchanged state)
{N_get 3 (1 (1 3) 3)}        -- k s s
-- (here 1=unit_arg, 2=k, 3=s — but after lambda lifting the
--  state-threading wrapper adjusts indices)

-- put handler: takes new state s', continuation k, and old state _
-- calls k with unit and the new state s'
{N_put 3 (2 (0) 1)}          -- k () s'
-- (here 1=s', 2=k, 3=old_state)
```

The full handler is assembled as a law that pattern-matches on the effect tag to select the appropriate arm, then applies the continuation. The initial state `s0` is passed as the first argument to the return-continuation chain.

### 6.3 Continuation reification

The continuation `k` captured in a handler arm is a PLAN value -- specifically, a partially applied law. When the computation performs an effect operation, the "rest of the computation" is packaged as a law that accepts the operation's return value and continues executing. This is a standard CPS transformation: every effectful operation becomes a function call that receives a callback.

---

## 7. Contracts

### 7.1 Proven contracts

`Proven` contracts are discharged statically. They produce no PLAN output. The compiler verifies them at compile time and erases them entirely.

### 7.2 Deferred contracts

`Deferred` contracts compile to a runtime check followed by a conditional `Abort`. The check evaluates the predicate; if it returns `False` (nat 0), the computation aborts.

```gallowglass
let safe_div : Nat -> (d : Nat | d /= 0) -> Nat
  | pre  Proven   (d /= 0)
  | post Deferred(NonLinear) (result * d = n)
  = fn n d -> Core.Nat.div n d
```

The `pre` contract is `Proven` and produces no code. The `post` contract is `Deferred` and compiles to:

```
{N_safe_div 2
  let result = (<P_div> 1 2)
  -- post-check: result * d = n
  -- if not (result * d = n) then Abort
  (<P_assert> (<P_eq> (<P_mul> result 2) 1) result)
}
```

Where `<P_assert>` is a pinned law that checks a condition and either returns the value or fires `Abort`:

```
-- assert : Bool -> a -> a
-- if condition is True (1), return value
-- if condition is False (0), Abort
<P_assert> = <{N_assert 2 (2 2 {N_abort 2 (<P_abort> 1)} 1)}>
```

The `Abort` call uses `Core.Abort.abort`, which is an external operation that halts the cog. It never appears in any effect row.

---

## 8. Pin Bindings

### 8.1 Programmer pins

A programmer pin `@name = expr` compiles to opcode 4 applied to the compiled expression.

```gallowglass
@table = compute_table large_input
```

Compiles to:

```
(4 (<P_compute_table> <P_large_input>))
```

Opcode 4 normalizes the value (forces it to normal form) and content-addresses it, producing a Pin `<value>`. The pinned value is then deduplicated in the heap: if another pin with identical content already exists, they share identity.

Within the scope of the pin binding, `table` refers to the resulting Pin value.

### 8.2 Compiler pins

The compiler introduces pins during DAG factoring (common subexpression pinning). These appear in Glass IR as `@![pin#hash] name = expr` but are structurally identical to programmer pins at the PLAN level -- both use opcode 4.

```
-- Glass IR
@![pin#3a7f9c] intermediate = complex_subexpression

-- PLAN
(4 complex_subexpression_plan)
```

### 8.3 Pin semantics

Pinning is the only mechanism that forces evaluation in PLAN's lazy evaluation model. A pin's content is always in normal form. This has two implications:

1. Programmer pins (`@name = expr`) serve as explicit evaluation points -- the programmer controls when computation happens.
2. The Merkle-DAG property is maintained: pins reference other pins but never the interior of other pins. The reference graph is acyclic by construction.

---

## 9. Type Erasure

All type information is erased during compilation. The PLAN output carries no type annotations. Specifically:

- **Type parameters** are erased. `List Nat` and `List Text` produce identical PLAN structure.
- **Effect rows** are erased. `{IO, Exn e | r} a` and `{} a` produce the same structure (modulo the CPS transformation for handlers).
- **Refined types** are erased. The refinement predicate `(n : Nat | n > 0)` becomes just `Nat` (a bare nat in PLAN). Runtime checks for `Deferred` contracts are separate (see section 7).
- **Type annotations** (`expr : Type`) are erased. They exist only for the type checker.
- **Quantifiers** (`forall a.`) are erased. Polymorphic functions compile to a single law that operates on any PLAN value.
- **Typeclass constraints** are erased as constraints but replaced by explicit dictionary arguments (see section 14).

The result: PLAN output is untyped. Type safety is a compile-time guarantee, not a runtime property.

---

## 10. Boolean Encoding

Booleans are PLAN nats:

```
True  = 1
False = 0
```

This encoding is consistent with opcode 2 (nat iteration), which treats 0 as the base case and non-zero as the inductive case. Boolean operations compile to nat operations:

```gallowglass
not x       -- compiles to: (2 1 {N_k 2 0} x)
                            -- if x=0 then 1 else 0
and x y     -- compiles to: (2 0 {N_k 2 y} x)
                            -- if x=0 then 0 else y
or  x y     -- compiles to: (2 y {N_k 2 1} x)
                            -- if x=0 then y else 1
```

Conditional expressions:

```gallowglass
if cond then t else e
```

```
(2 e {N_k 2 t} cond)
-- nat iteration on cond:
--   cond = 0 (False): return e
--   cond > 0 (True):  return t (ignoring predecessor)
```

---

## 11. Text and Bytes Encoding

Both `Text` and `Bytes` use the structural pair encoding: `(byte_length, content_nat)`.

This is a PLAN App of two nats:

```
(byte_length content_nat)
```

### 11.1 Encoding rules

- `byte_length` is the number of bytes in the content.
- `content_nat` is the bytes packed as a little-endian nat.
- The pair disambiguates trailing zero bytes: `b""` is `(0 0)` while `b"\x00"` is `(1 0)`.

### 11.2 Examples

```gallowglass
""          -- empty text
```

```
(0 0)
```

```gallowglass
"A"         -- single ASCII character, byte 0x41
```

```
(1 65)      -- byte_length=1, content_nat=65 (0x41)
```

```gallowglass
"AB"        -- two ASCII characters, bytes 0x41 0x42
```

```
(2 16961)   -- byte_length=2, content_nat = 0x41 + 0x42*256 = 16961
```

```gallowglass
b"\x00\x01" -- two bytes: 0x00, 0x01
```

```
(2 256)     -- byte_length=2, content_nat = 0x00 + 0x01*256 = 256
```

### 11.3 Text invariant

`Text` values carry the additional invariant that `content_nat` decodes to valid UTF-8 of exactly `byte_length` bytes. This invariant is established at creation and is not encoded in the PLAN representation -- it is a Gallowglass-level type system concern.

---

## 12. List Encoding

Lists use a standard cons-cell encoding:

```
Nil       = 0
Cons h t  = (h t)
```

`Nil` is the nat 0. `Cons` is a two-element App node where the head is the first element and the tail is the rest of the list.

### 12.1 Examples

```gallowglass
[]                  -- Nil
```

```
0
```

```gallowglass
[1]                 -- Cons 1 Nil
```

```
(1 0)
```

```gallowglass
[1, 2, 3]           -- Cons 1 (Cons 2 (Cons 3 Nil))
```

```
(1 (2 (3 0)))
```

### 12.2 Constructor functions

```
-- Nil : List a
Nil = 0

-- Cons : a -> List a -> List a
{N_Cons 2 (1 2)}              -- (head tail)
```

### 12.3 Disambiguation

A list `[0]` is `(0 0)`, which is structurally identical to the Text/Bytes encoding of an empty string. This is not a problem: Gallowglass types are erased, and the same PLAN value can represent different Gallowglass types. Type safety ensures that a `List Nat` is never confused with a `Text` at the Gallowglass level.

---

## 13. Tuple Encoding

Tuples are products encoded as nested App nodes. A pair `(a, b)` compiles to a two-element App:

```
(a b)
```

Triples and larger tuples nest left-associatively:

```gallowglass
(a, b, c)       -- (A otimes B) otimes C, left-associated
```

```
((a b) c)
```

### 13.1 Examples

```gallowglass
(1, 2)
```

```
(1 2)
```

```gallowglass
(1, 2, 3)
```

```
((1 2) 3)
```

### 13.2 Accessing tuple elements

Tuple element access compiles to opcode 1 (reflect) to destructure App nodes:

```gallowglass
let fst : forall a b. (a, b) -> a
```

Uses opcode 1 on the pair `(a b)` to extract the head of the App:

```
{N_fst 1
  (1
    {N_p 1 0}                -- pin case: unreachable for tuples
    {N_l 3 0}                -- law case: unreachable for tuples
    {N_a 2 1}                -- app case: (head tail) -> return head (index 1)
    {N_n 1 0}                -- nat case: unreachable for tuples
    1                        -- the tuple value (argument index 1)
  )
}
```

Similarly, `snd` returns index 2 (the tail) from the App case.

---

## 14. Typeclass Dictionaries

Typeclass constraints are elaborated into explicit dictionary arguments during compilation. A dictionary is a PLAN value (typically a tuple/record of function pins) passed as an additional leading argument to the law.

### 14.1 Dictionary structure

A typeclass dictionary is a tuple of its method implementations:

```gallowglass
class Eq a {
  eq  : a -> a -> Bool
  neq : a -> a -> Bool
}
```

An `Eq` dictionary is a pair `(eq_impl, neq_impl)`:

```
(eq_impl neq_impl)
```

### 14.2 Elaboration

```gallowglass
let elem : forall a. Eq a => a -> List a -> Bool
```

In Glass IR, this becomes:

```gallowglass
let elem : forall a. (eq_dict : Eq a) -> a -> List a -> Bool
```

In PLAN, the dictionary is the first argument:

```
{N_elem 3                     -- arity 3: dict, needle, haystack
  ...body using
    (fst 1) for eq            -- extract eq from dictionary (arg 1)
    2 for needle              -- arg 2
    3 for haystack            -- arg 3
  ...
}
```

### 14.3 Dictionary construction

An `instance` declaration compiles to a pinned dictionary value:

```gallowglass
instance Eq Nat {
  eq  = fn x y -> Core.Nat.eq x y
  neq = fn x y -> Core.Nat.neq x y
}
```

```
<(                             -- pinned pair
  {N_eq_nat 2 (<P_nat_eq> 1 2)}
  {N_neq_nat 2 (<P_nat_neq> 1 2)}
)>
```

### 14.4 Superclass constraints

A typeclass with a superclass constraint carries the superclass dictionary within its own dictionary:

```gallowglass
class Eq a => Ord a {
  compare : a -> a -> Ordering
}
```

An `Ord` dictionary is `(eq_dict, compare_impl)`:

```
(eq_dict compare_impl)
```

Functions with an `Ord a` constraint receive one dictionary argument. To access `Eq` methods, the compiler extracts the `eq_dict` component from the `Ord` dictionary.

---

## 15. Mutual Recursion

Mutually recursive definitions (as identified by Tarjan's SCC algorithm on the dependency graph) compile to a **shared pin** containing all laws in the SCC. See `spec/02-mutual-recursion.md` for the complete specification.

### 15.1 Shared pin encoding

A group of `n` mutually recursive laws is encoded as:

```
({0 (n+1) 0} law_0 law_1 ... law_{n-1})
```

The selector `{0 (n+1) 0}` is a law with name 0, arity `n+1`, and body 0 (returns self). When the shared pin is applied to `n` laws, it produces a partially applied value. Each law in the group receives the shared pin as an extra argument (via lambda lifting), allowing cross-references without introducing cycles in the Merkle-DAG.

### 15.2 Example

```gallowglass
let is_even : Nat -> Bool
  = fn n -> match n {
      | 0 -> True
      | k -> is_odd (k - 1)
    }

let is_odd : Nat -> Bool
  = fn n -> match n {
      | 0 -> False
      | k -> is_even (k - 1)
    }
```

SCC analysis identifies `{is_even, is_odd}` as mutually recursive. Canonical lexicographic ordering: `is_even` at index 0, `is_odd` at index 1.

Each law is lambda-lifted to take the shared group as an additional first argument:

```
-- is_even: group is arg 1, n is arg 2
-- to call is_odd: extract index 1 from group
{N_is_even 2
  (2                            -- nat iteration on n (arg 2)
    1                           -- zero case: True
    {N_step 2                   -- succ case: predecessor is arg 2
      ((1 1) (2 - 1))           -- is_odd(pred) via group[1](pred)
                                -- group is inherited from outer scope
    }
    2                           -- n
  )
}

-- is_odd: group is arg 1, n is arg 2
{N_is_odd 2
  (2
    0                           -- zero case: False
    {N_step 2
      ((1 0) (2 - 1))          -- is_even(pred) via group[0](pred)
    }
    2
  )
}
```

The shared pin:

```
<({0 3 0} {N_is_even ...} {N_is_odd ...})>
```

The selector `{0 3 0}` has arity 3 (2 laws + 1 for self). Individual functions are extracted by applying the shared pin to select indices.

### 15.3 Canonical ordering

Laws within the shared pin are ordered lexicographically by their fully qualified name. This ordering is deterministic and canonical -- any reordering would change the PinId of the shared pin, which would cascade to all downstream PinIds. See `spec/02-mutual-recursion.md` for the full ordering specification.

---

## 16. Fix (Self-Reference)

The `fix` combinator enables anonymous recursion. A `fix fn self args -> body` expression compiles to a law where de Bruijn index 0 (the law's self-reference) is bound to `self`.

```gallowglass
let count_down : Nat -> List Nat
  = fn n ->
      fix fn self m -> match m {
        | 0 -> [0]
        | k -> k :: self (k - 1)
      } n
```

The `fix` lambda compiles to a law where index 0 is the recursive self-reference:

```
-- The fix lambda: self is index 0, m is index 1
{N_fix_count 1
  (2                          -- nat iteration on m (arg 1)
    (0 0)                     -- zero case: [0] = Cons 0 Nil = (0 0)
    {N_step 2                 -- succ case: predecessor is available
      (2 (0 (2 - 1)))         -- k :: self(k-1)
                              -- k is reconstructed as (3 predecessor)
                              -- self is index 0 of the inner law?
    }
    1                         -- m
  )
}
```

More precisely, `fix fn self -> body` compiles the body as a law body where every occurrence of `self` is replaced by index 0. The law calls itself by applying index 0 to arguments. This is exactly how PLAN laws work: index 0 is always the law itself, providing built-in support for direct recursion without a fixpoint combinator at the PLAN level.

### Example: factorial

```gallowglass
fix fn self n -> match n {
  | 0 -> 1
  | k -> Core.Nat.mul k (self (k - 1))
}
```

```
{N_factorial 1
  (2                           -- nat iteration on n (arg 1)
    1                          -- zero case: return 1
    {N_step 2                  -- succ case: arg 1 = result so far (unused),
                               --            arg 2 = predecessor
      (<P_mul> (3 2) (0 2))    -- k * self(pred)
                               -- k = (3 pred) = pred + 1
                               -- self = index 0 of THIS law
                               -- BUT self should be the outer law...
    }
    1
  )
}
```

Note: the exact de Bruijn index mapping depends on whether `fix` compiles to a single law or nests laws for the match arms. The key invariant is that `self` always resolves to the law that `fix` introduces, using index 0.

---

## 17. Summary of Encodings

| Gallowglass Construct | PLAN Encoding |
|------------------------|---------------|
| Function `let f x y = body` | `{N_f 2 body'}` where `x`=1, `y`=2 |
| Lambda `fn x -> body` | Lambda-lifted law with free vars as extra args |
| Application `f x y` | `(f x y)` (left-associative App) |
| Sum constructor `C a b` (tag `t`) | `(t a b)` |
| Nullary constructor `C` (tag `t`) | `t` |
| Record `{x=a, y=b}` | `(0 a b)` (tag 0, positional) |
| Pattern match | Opcode 1 (reflect) + opcode 2 (nat iteration) |
| Handler | CPS transformation; `k` is a partially applied law |
| Proven contract | Erased (no PLAN output) |
| Deferred contract | Conditional Abort check |
| Pin `@name = expr` | `(4 expr')` (opcode 4) |
| Types, effects, quantifiers | Erased entirely |
| `True` / `False` | `1` / `0` |
| Text/Bytes `"hello"` | `(byte_length content_nat)` |
| List `[a, b, c]` | `(a (b (c 0)))` |
| Nil | `0` |
| Tuple `(a, b)` | `(a b)` |
| Tuple `(a, b, c)` | `((a b) c)` |
| Typeclass constraint | Extra dictionary argument (leading) |
| Instance declaration | Pinned tuple of method implementations |
| Mutual recursion (SCC) | Shared pin: `({0 (n+1) 0} law_0 ... law_{n-1})` |
| `fix fn self -> body` | Law with `self` = index 0 |
| `if c then t else e` | `(2 e {_ 2 t} c)` |

---

## 18. Revision Log

| Issue | Resolution |
|-------|------------|
| Initial draft | All 16 sections specified with PLAN output examples |
