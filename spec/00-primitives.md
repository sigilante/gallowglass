# Core.Primitives

**Spec version:** 0.1
**Depends on:** SPEC.md, spec/06-surface-syntax.md

This document specifies every primitive operation available in Gallowglass through `external mod` declarations. These are the foundational operations implemented in Sire (the bootstrap host language) and exposed to Gallowglass as external modules. All other Gallowglass functionality is built on top of these primitives.

There are 101 operations across 11 modules. Each operation is declared using the `external mod` grammar from `spec/06-surface-syntax.md`. All types are erased at compile time; the PLAN encoding notes describe the runtime representation.

**Conventions used throughout:**

- `{External}` marks operations that cross the VM boundary but perform no I/O.
- `{External, IO}` marks operations that cross the VM boundary and perform I/O.
- `Abort` never appears in any effect row. Operations that can abort (e.g., division by zero) do so unconditionally; the abort is not an effect but a contract violation propagated to the cog supervisor.
- Opaque types carry variance annotations: `[~]` invariant (default), `[+]` covariant, `[-]` contravariant.

---

## 1. Core.PLAN

Direct access to the five PLAN opcodes. These are the lowest-level operations in the system. Every other primitive is ultimately built from these.

```gallowglass
external mod Core.PLAN {
  -- Opcode 0: construct a law from name, arity, and body
  mk_law    : Nat → Nat → a → {External} Law

  -- Opcode 1: dispatch on PLAN constructor (pin/law/app/nat)
  reflect   : a → (Pin → b) → (Law → b) → (a → a → b) → (Nat → b) → {External} b

  -- Opcode 2: structural recursion on naturals
  --   nat_case zero_val succ_fn n
  --   if n = 0: zero_val
  --   if n > 0: succ_fn (n - 1)
  nat_case  : b → (Nat → b) → Nat → {External} b

  -- Opcode 3: increment a natural number
  inc       : Nat → {External} Nat

  -- Opcode 4: pin a value (normalize and content-address)
  pin       : a → {External} Pin
}
```

**PLAN encoding:** Each operation compiles to a direct invocation of the corresponding opcode (0--4). These are the only operations that map one-to-one to PLAN opcodes. The `Law` and `Pin` types referenced here are abstract PLAN-level entities; they are opaque at the Gallowglass level and are only manipulated through `Core.Inspect`.

**Count: 5 operations**

---

## 2. Core.Nat

Arbitrary-precision natural number operations. `Nat` is the PLAN native natural number -- it never overflows.

```gallowglass
external mod Core.Nat {
  -- Arithmetic
  add       : Nat → Nat → Nat
  sub       : Nat → Nat → Nat           -- saturating: sub 3 5 = 0
  mul       : Nat → Nat → Nat
  div       : Nat → (d : Nat | d ≠ 0) → Nat
  mod       : Nat → (d : Nat | d ≠ 0) → Nat
  pow       : Nat → Nat → Nat

  -- Comparison
  eq        : Nat → Nat → Bool
  neq       : Nat → Nat → Bool
  lt        : Nat → Nat → Bool
  gt        : Nat → Nat → Bool
  lte       : Nat → Nat → Bool
  gte       : Nat → Nat → Bool
  min       : Nat → Nat → Nat
  max       : Nat → Nat → Nat

  -- Bit operations
  bit_and   : Nat → Nat → Nat
  bit_or    : Nat → Nat → Nat
  bit_xor   : Nat → Nat → Nat
  bit_shift : Nat → Int → Nat           -- positive = left, negative = right
}
```

**PLAN encoding:** `add`, `sub`, `mul`, `div`, `mod` are laws that reduce to combinations of `nat_case` (opcode 2) and `inc` (opcode 3) at the PLAN level. In practice, all of these are jet candidates -- the VM matches their pin hashes against native implementations for performance. `sub` saturates at zero because PLAN has no negative naturals; negative results require `Core.Int`. Division and modulo by zero are prevented by the refined type on `d`; violation is a contract `Abort`.

**Count: 18 operations**

---

## 3. Core.Int

Integer operations using sign-magnitude representation. `Int` is an arbitrary-precision integer encoded as a pair `(sign : Nat, magnitude : Nat)` where `sign` is 0 for non-negative and 1 for negative. This module also provides fixed-width bounds checking for interop with `Int32`, `Int64`, `Uint32`, and `Uint64`.

```gallowglass
external mod Core.Int {
  -- Construction
  from_nat      : Nat → Int
  negate        : Int → Int
  abs           : Int → Nat
  sign          : Int → Int              -- returns -1, 0, or 1

  -- Arithmetic
  add           : Int → Int → Int
  sub           : Int → Int → Int
  mul           : Int → Int → Int
  div           : Int → (d : Int | d ≠ 0) → Int
  mod           : Int → (d : Int | d ≠ 0) → Int

  -- Comparison
  eq            : Int → Int → Bool
  neq           : Int → Int → Bool
  lt            : Int → Int → Bool
  gt            : Int → Int → Bool
  lte           : Int → Int → Bool
  gte           : Int → Int → Bool

  -- Fixed-width bounds checking
  to_int32      : Int → {External} Int32     -- Abort if out of range
  to_int64      : Int → {External} Int64     -- Abort if out of range
}
```

**PLAN encoding:** `Int` is a PLAN app of two nats: `(sign magnitude)`. `sign` is 0 or 1. All arithmetic operations are laws over this pair representation. `from_nat` produces `(0 n)`. `negate` flips the sign bit. Fixed-width conversions (`to_int32`, `to_int64`) are marked `{External}` because they cross the VM boundary for range validation; out-of-range values produce `Abort` (not an effect -- propagated to the cog supervisor).

**Count: 17 operations**

---

## 4. Core.Pin

Operations on content-addressed pins. Pins are PLAN's mechanism for structural sharing and identity in the Merkle-DAG heap.

```gallowglass
external mod Core.Pin {
  type PinId : Opaque[~]                 -- BLAKE3-256 hash, invariant

  hash      : Pin → {External} PinId    -- extract the content hash
  eq        : Pin → Pin → {External} Bool  -- structural equality via hash
  unpin     : Pin → {External} a         -- extract pinned content
  same_pin  : Pin → Pin → {External} Bool  -- referential identity (same heap node)
}
```

**PLAN encoding:** `Pin` is PLAN constructor `<value>`. `hash` extracts the BLAKE3-256 hash of the normalized content. `eq` compares hashes (O(1) after hashing). `unpin` returns the interior value. `same_pin` checks heap-level pointer identity, which is strictly stronger than hash equality (two pins may have the same hash but occupy different heap nodes before deduplication completes). All operations are `{External}` because they require VM-level introspection of the pin structure.

**Count: 4 operations**

---

## 5. Core.Hash

BLAKE3-256 hashing operations. All hashing in Gallowglass uses BLAKE3-256 exclusively -- no exceptions.

```gallowglass
external mod Core.Hash {
  type HashDigest : Opaque[~]            -- 256-bit BLAKE3 digest, invariant

  hash_bytes    : Bytes → {External} HashDigest
  hash_text     : Text → {External} HashDigest
  hash_nat      : Nat → {External} HashDigest
  combine       : HashDigest → HashDigest → {External} HashDigest
  to_bytes      : HashDigest → Bytes
  from_bytes    : (b : Bytes | byte_length b = 32) → {External} HashDigest
}
```

**PLAN encoding:** `HashDigest` is an opaque nat (the 256-bit hash value stored as a PLAN nat). `hash_bytes`, `hash_text`, and `hash_nat` serialize their input to a canonical byte representation and apply BLAKE3-256. `combine` concatenates two digests and hashes the result (Merkle-tree style composition). `to_bytes` is a pure projection (the digest is already a nat encoding 32 bytes). `from_bytes` validates length and wraps. All hashing operations are `{External}` because they invoke the VM's BLAKE3 implementation.

**Count: 6 operations**

---

## 6. Core.Text

UTF-8 validated text operations. `Text` is represented as the structural pair `(byte_length : Nat, content_nat : Nat)` where `content_nat` encodes UTF-8 bytes as a little-endian nat.

Text supports three levels of indexing per SPEC.md section 7.2:

- **Byte offset** (`ByteOffset`): position in the underlying byte sequence. O(1) access but may land mid-codepoint.
- **Codepoint index** (`Nat`): Unicode scalar value position. O(n) access, may split grapheme clusters.
- **Grapheme index** (`GraphemeIdx`): user-perceived character position. O(n) access, always correct for display.

The default `length` returns grapheme count -- what users perceive as characters.

```gallowglass
external mod Core.Text {
  -- Construction and conversion
  from_bytes    : Bytes → {External} (Result Text TextError)
  to_bytes      : Text → Bytes
  from_nat      : Nat → {External} (Result Text TextError)

  -- Length (three levels)
  byte_length     : Text → Nat
  codepoint_count : Text → Nat
  grapheme_count  : Text → Nat

  -- Concatenation and slicing
  concat        : Text → Text → Text
  slice_bytes   : Text → ByteOffset → ByteOffset → {External} (Result Text TextError)
  split         : Text → Text → List Text

  -- Search
  contains      : Text → Text → Bool
  index_of      : Text → Text → Result Nat ⊤

  -- Comparison
  eq            : Text → Text → Bool
  ord           : Text → Text → Ordering
}
```

**PLAN encoding:** `Text` is a PLAN app of two nats: `(byte_length content_nat)`. The `content_nat` is the UTF-8 byte sequence encoded as a little-endian natural number. `from_bytes` validates UTF-8; `to_bytes` is a pure projection. `slice_bytes` operates on byte offsets and validates that the resulting slice is valid UTF-8 (hence `{External}` and `Result`). `concat` produces a new pair with summed byte lengths. `grapheme_count` requires UAX #29 grapheme cluster boundary detection.

**Count: 13 operations**

---

## 7. Core.Bytes

Raw binary data operations. `Bytes` shares the same structural pair encoding as `Text` but carries no UTF-8 invariant.

```gallowglass
external mod Core.Bytes {
  -- Construction
  empty         : Bytes
  singleton     : Nat → Bytes            -- single byte (Abort if > 255)
  from_nat      : Nat → Nat → Bytes      -- (byte_length, content_nat)
  from_list     : List Nat → Bytes       -- each Nat must be 0..255

  -- Length
  byte_length   : Bytes → Nat

  -- Access
  index         : Bytes → Nat → Result Nat ⊤  -- byte at offset
  slice         : Bytes → Nat → Nat → Bytes   -- start, length

  -- Concatenation
  concat        : Bytes → Bytes → Bytes
  concat_list   : List Bytes → Bytes

  -- Conversion
  to_nat        : Bytes → Nat            -- content nat (little-endian)
  to_list       : Bytes → List Nat       -- list of byte values

  -- Comparison
  eq            : Bytes → Bytes → Bool
  ord           : Bytes → Bytes → Ordering

  -- Search
  contains      : Bytes → Bytes → Bool
}
```

**PLAN encoding:** `Bytes` is a PLAN app of two nats: `(byte_length content_nat)`, identical in structure to `Text`. `empty` is `(0 0)`. `singleton 65` is `(1 65)`. The structural pair disambiguates trailing zero bytes: `b""` is `(0, 0)` while `b"\x00"` is `(1, 0)`. `to_nat` extracts the content nat; `from_nat` wraps a length-content pair. `concat` computes the new content nat by shifting and ORing.

**Count: 14 operations**

---

## 8. Core.Bool

Boolean operations. `Bool` is encoded as PLAN nats: `True = 1`, `False = 0`.

```gallowglass
external mod Core.Bool {
  and       : Bool → Bool → Bool
  or        : Bool → Bool → Bool
  not       : Bool → Bool
  xor       : Bool → Bool → Bool
  if_then   : ∀ a. Bool → a → a → a     -- if_then cond then_val else_val
  eq        : Bool → Bool → Bool
}
```

**PLAN encoding:** `True` compiles to PLAN nat `1`. `False` compiles to PLAN nat `0`. `and` is nat multiplication, `or` is `nat_case` (if first is 0 return second, else 1), `not` is `nat_case 1 (λ _ → 0)`. `if_then` is `nat_case else_val (λ _ → then_val)`. These are among the most heavily jetted operations.

**Count: 6 operations**

---

## 9. Core.IO

I/O operations that interact with the external world through Plunder's cog/driver model. All operations in this module carry both `External` (VM boundary crossing) and `IO` (observable side effect) in their effect rows.

```gallowglass
external mod Core.IO {
  read_file     : Text → {External, IO} (Result Bytes IOError)
  write_file    : Text → Bytes → {External, IO} (Result ⊤ IOError)
  read_stdin    : ⊤ → {External, IO} Bytes
  write_stdout  : Bytes → {External, IO} ⊤
  write_stderr  : Bytes → {External, IO} ⊤
  exit          : Nat → {External, IO} ⊥
}
```

**PLAN encoding:** IO operations compile to laws that produce **cog output values** -- PLAN structures interpreted by the cog supervisor and drivers. The law itself is pure; the effect occurs when the cog supervisor processes the output during the event loop. `read_file` and `write_file` return `Result` rather than raising exceptions, keeping the error path explicit. `exit` returns `⊥` (bottom) because it never returns -- the cog terminates. The exit code is a nat passed to the supervisor.

**Count: 6 operations**

---

## 10. Core.Inspect

PLAN value inspection operations for homoiconicity. These operations allow Gallowglass code to examine the structure of arbitrary PLAN values at runtime -- the foundation for quoting, macro expansion, and the debugger.

```gallowglass
external mod Core.Inspect {
  type Term : Opaque[+]                  -- reified PLAN value, covariant

  -- Classification
  is_pin    : Term → Bool
  is_law    : Term → Bool
  is_app    : Term → Bool
  is_nat    : Term → Bool

  -- Decomposition
  pin_val   : Term → {External} (Result Term ⊤)
  law_name  : Term → {External} (Result Nat ⊤)
  law_arity : Term → {External} (Result Nat ⊤)
  law_body  : Term → {External} (Result Term ⊤)
  app_parts : Term → {External} (Result (Term ⊗ Term) ⊤)
}
```

**PLAN encoding:** `Term` is any PLAN value -- it is the identity type at the PLAN level (every PLAN value is already a term). The classification operations use opcode 1 (`reflect`) to dispatch on the PLAN constructor. The decomposition operations extract components: `pin_val` unpins, `law_name`/`law_arity`/`law_body` extract from `{n a b}`, and `app_parts` splits `(f g)` into the pair `(f, g)`. All decomposition operations return `Result` because the operation is only valid on the matching constructor. The `Term` type is covariant (`[+]`) because a `Term` produced in a more specific context can always be used in a more general one.

**Count: 9 operations**

---

## 11. Core.Abort

Abort operations. `Abort` is structurally unhandleable -- it is never in an effect row. Abort propagates directly to the cog supervisor, bypassing all handlers. These operations are used by the contract system and by explicit programmer abort calls.

```gallowglass
external mod Core.Abort {
  abort         : ∀ a. Text → a          -- abort with message, returns ⊥ (any type)
  abort_with    : ∀ a. Text → Term → a   -- abort with message and diagnostic PLAN value
}
```

**PLAN encoding:** `abort` and `abort_with` compile to laws that produce a cog-level abort signal. The return type is universally quantified (`∀ a`) because the expression never produces a value -- it diverges. The `Text` message is for diagnostic purposes (logged by the cog supervisor). `abort_with` additionally attaches a `Term` (any PLAN value) as structured diagnostic data, useful for contract violation reports that include the violating values. Note that `Abort` carries **no effect annotation** -- this is by design. Abort is outside the effect system entirely.

**Count: 2 operations**

---

## Summary

| Module | Operations | Key characteristic |
|---|---|---|
| Core.PLAN | 5 | Direct PLAN opcode access |
| Core.Nat | 18 | Arbitrary-precision natural arithmetic |
| Core.Int | 17 | Sign-magnitude integers, fixed-width conversion |
| Core.Pin | 4 | Content-addressed pin manipulation |
| Core.Hash | 6 | BLAKE3-256 hashing |
| Core.Text | 13 | UTF-8 text with three-level indexing |
| Core.Bytes | 14 | Raw binary data |
| Core.Bool | 6 | Boolean logic |
| Core.IO | 6 | File, stdio, and process control |
| Core.Inspect | 9 | PLAN value introspection (homoiconicity) |
| Core.Abort | 2 | Unhandleable termination |
| **Total** | **100** | |

The total is 100 operations. The count of "~101" in SPEC.md section 13.1 is approximate; 100 is the precise count after resolving each module's operation list.

---

## Cross-References

- **PLAN opcodes 0--4:** See SPEC.md section 2.1 for the complete opcode semantics.
- **Effect system rules:** See SPEC.md section 3.4. `Abort` never in a row. `External` required for VM boundary crossings.
- **Text/Bytes pair encoding:** See SPEC.md section 7.1 for the `(byte_length, content_nat)` representation.
- **Three-level text indexing:** See SPEC.md section 7.2 for byte, codepoint, and grapheme distinctions.
- **Int sign-magnitude encoding:** See DECISIONS.md for rationale on representation choices.
- **`external mod` grammar:** See spec/06-surface-syntax.md, `ExternalMod` and `ExtOp` productions.
- **Jet system:** See SPEC.md section 12. All Core.Primitives operations are primary jet candidates.
- **Seed serialization:** See spec/07-seed-format.md for how compiled primitives are serialized.
