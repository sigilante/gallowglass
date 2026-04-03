# Seed Serialization Format

**Spec version:** 0.1
**Depends on:** SPEC.md, spec/04-plan-encoding.md
**Reference implementation:** xocore-tech/PLAN (`sire/boot.sire` lines 1931–2227, `doc/planvm-amd64.tex` lines 5272–5773)

This document specifies the Seed format: the canonical binary serialization of PLAN values. Every compiled Gallowglass program is emitted as a Seed file. The format matches xocore-tech/PLAN exactly — interoperability with the reference VM is a hard requirement during the bootstrap phase.

---

## 1. Overview

A Seed is a portable, deterministic serialization of a PLAN value. Seeds serve three purposes:

1. **Bootstrap loading** — The PLAN VM loads a seed file to start execution
2. **Compilation output** — The Gallowglass compiler emits seeds
3. **Network transit** — Seeds are the wire format for PLAN values

### 1.1 Design Properties

- **Deterministic:** The same PLAN value always produces the same seed bytes
- **Compact:** Bit-packed tree encoding, machine-native number storage
- **DAG-aware:** Shared subtrees are serialized once as separate fragments
- **Template-based:** Pins and Laws are not encoded directly — they are constructed by applying template parameters (holes)
- **Minimal:** No magic bytes, no version field, no checksums — the loader does no input validation

### 1.2 What Seeds Do NOT Contain

Seeds do not directly represent PLAN's four constructors. The format knows only two things:

- **Atoms** — Natural numbers (nats)
- **Cells** — Function applications

Pins and Laws are constructed indirectly: `MkPin` and `MkLaw` are passed as **holes** (template parameters), and pin/law construction is encoded as applications of those constructors. The VM evaluates the resulting template expression to produce the final normalized PLAN value.

---

## 2. Seed Structure

A seed is a contiguous byte sequence with five sections, laid out sequentially:

```
┌──────────────────────────────────┐
│  Header (40 bytes)               │  5 × u64 little-endian
├──────────────────────────────────┤
│  Bignat sizes                    │  n_bigs × u64
├──────────────────────────────────┤
│  Bignat data                     │  sum(sizes) × u64
├──────────────────────────────────┤
│  Word values                     │  n_words × u64
├──────────────────────────────────┤
│  Byte values                     │  n_bytes × u8
├──────────────────────────────────┤
│  Fragment bitstream              │  bit-packed tree nodes
└──────────────────────────────────┘
```

### 2.1 Header

The header is exactly **40 bytes**: five little-endian `u64` values at fixed offsets.

```
Offset  Size   Field       Description
──────  ─────  ──────────  ─────────────────────────────────────────
0       u64    n_holes     Number of template parameters (external references)
8       u64    n_bigs      Number of multi-word bignat values
16      u64    n_words     Number of single-word (u64) values (> 255)
24      u64    n_bytes     Number of single-byte (u8) values (0–255)
32      u64    n_frags     Number of tree fragment entries
```

There is no magic number, no version field, and no checksum. The format is identified by context (file extension, loader expectation), not by content.

### 2.2 Atom Table

Immediately after the header, all natural numbers in the PLAN value are stored in descending size order. Numbers are categorized by their storage width:

**Bignats** (numbers requiring more than 64 bits):

```
Section 1: Bignat sizes — n_bigs × u64 values
  Each u64 gives the word-width (number of u64s) of the corresponding bignat.

Section 2: Bignat data — contiguous u64 words
  The actual bignat values, little-endian, concatenated in order.
  Total words = sum of all bignat sizes.
```

**Words** (numbers 256–2^64-1):

```
Section 3: Word values — n_words × u64 values
  Numbers that fit in a single u64 but not a single u8.
```

**Bytes** (numbers 0–255):

```
Section 4: Byte values — n_bytes × u8 values
  Small numbers stored as single bytes.
```

All multi-byte values are little-endian. Numbers are stored in machine-native format — loading is a trivial memcpy with no decoding overhead.

### 2.3 Fragment Bitstream

After the atom table, the remaining bytes contain the **fragment bitstream**: a bit-packed encoding of the PLAN value's tree structure (application spines).

The bitstream is read LSB-first within each byte.

---

## 3. Scope Table

The seed format uses a **scope table** — a growing array of PLAN values that fragments reference by index. The scope table is built incrementally during deserialization:

```
Index range              Contents
──────────────────────   ────────────────────────────────────
0 .. n_holes-1           Hole values (template parameters)
n_holes .. +n_bigs-1     Bignat values
+n_bigs .. +n_words-1    Word values
+n_words .. +n_bytes-1   Byte values
+n_bytes .. +n_frags-1   Fragment values (appended as each is decoded)
```

When a fragment references index `i`, it refers to the `i`th entry in this scope table. The reference width grows as the table grows (see §4.2).

### 3.1 Holes

Holes are template parameters — external values passed to the seed at load time. The most common hole configuration:

- **Hole 0 = `MkPin`** — The pin constructor. To create a pin from value `v`, the seed encodes `(MkPin v)` as an application of hole 0 to `v`.

When a seed contains no pins, the hole list is empty (`n_holes = 0`).

When a seed contains pins, `MkPin` is passed as hole 0. The loader calls `MkPin(0)` to produce the initial hole value, then the template expression constructs pins by applying this to their content.

### 3.2 Laws in Seeds

Laws `{name arity body}` are encoded as applications of opcode 0 (the law-creation opcode):

```
(0 name arity body)
```

This is a standard PLAN application — opcode 0, applied to three arguments, evaluates to a Law. No special hole is needed because opcode 0 is a nat (atom), already representable in the seed.

---

## 4. Fragment Encoding

Each fragment encodes a tree node — either a leaf (reference to an existing scope entry) or an application spine (a function applied to arguments).

### 4.1 Fragment Size Encoding

The number of arguments in an application spine is encoded in two parts:

1. **Size-of-size** in unary: count zero bits, terminated by a one bit
2. **Size** in binary: `(size_of_size - 1)` bits, representing the size minus the implicit high bit

The encoding (shown LSB-first):

```
Arguments   Bit encoding      Explanation
─────────   ────────────────  ──────────────────────────────
0 (leaf)    1                 Just the terminator; no args
1           01                One zero bit, then terminator
2           001.0             Two zero bits, terminator, then 1 bit: 0 → 2
3           001.1             Two zero bits, terminator, then 1 bit: 1 → 3
4           00010.00          Three zeros, terminator, 0 in 2 bits, then 2 bits: 00 → 4
5           00010.01          ... 01 → 5
6           00010.10          ... 10 → 6
7           00010.11          ... 11 → 7
```

Formally, for an application with `n` arguments:

- If `n = 0`: emit a single `1` bit
- Otherwise:
  - Let `k = floor(log2(n)) + 1` (the bit-width of `n`)
  - Emit `k` zero bits, then a `1` bit
  - Emit the low `(k - 1)` bits of `n` (the high bit is implicit)

### 4.2 Reference Encoding

After the size encoding, the fragment contains references to scope table entries:

- First reference: the function being applied
- Subsequent references: the arguments, left to right

Each reference is a fixed-width integer index into the scope table. The bit-width is:

```
ref_width = ceil(log2(scope_size))
```

where `scope_size` is the current size of the scope table at the point this fragment is being decoded. As fragments are decoded and appended to the scope table, `scope_size` grows, and subsequent references may use more bits.

For a leaf node (0 arguments), the fragment is just the size encoding (`1` bit) followed by one reference (the leaf value).

For an application `(f x₁ x₂ ... xₙ)`, the fragment contains:
1. Size encoding for `n`
2. Reference to `f`
3. Reference to `x₁`
4. ...
5. Reference to `xₙ`

Total: `(n + 1)` references, each `ref_width` bits wide.

### 4.3 Root Value

The root of the PLAN value is the **last fragment** decoded. There is no explicit root index — it is always the final entry in the fragment table.

---

## 5. Serialization Algorithm

Given a PLAN value `v`, produce a seed:

### 5.1 Internalization

Traverse `v` as a DAG (not a tree), building a deduplicated node table:

1. **Identify atoms:** Collect all nat values. Categorize each as byte (0–255), word (256–2^64-1), or bignat (> 2^64-1).
2. **Identify holes:** Determine which external references are needed. For values containing pins, `MkPin` becomes hole 0.
3. **Identify shared subtrees:** Any node referenced more than once must become its own fragment (to preserve sharing).

### 5.2 Shattering

Decompose the DAG into fragments. A node becomes a fragment if:

- It is the root, OR
- It is referenced by more than one other node (shared), OR
- It is a pin body (needs to be passed to MkPin)

Each fragment encodes a single application spine: the function and its arguments, where each argument is either a reference to an atom, a hole, or a previously emitted fragment.

### 5.3 Ordering

Emit fragments in dependency order — a fragment's references must all have been emitted (or be atoms/holes) before it appears. The root is always last.

This ordering is the same as the scope table construction order during deserialization: atoms and holes first, then fragments in dependency order.

### 5.4 Writing

1. Compute `n_holes`, `n_bigs`, `n_words`, `n_bytes`, `n_frags`
2. Write the 40-byte header
3. Write bignat sizes (each as u64)
4. Write bignat data (concatenated u64 words)
5. Write word values (each as u64)
6. Write byte values (each as u8)
7. Bit-pack each fragment into the bitstream

### 5.5 Padding

The final seed is padded to a word-aligned (8-byte) boundary. The padding bytes are zero.

---

## 6. Deserialization Algorithm

Given seed bytes, reconstruct the PLAN value:

```
1.  Read header: n_holes, n_bigs, n_words, n_bytes, n_frags
2.  Initialize scope table (empty)
3.  For each hole (0..n_holes-1):
      Push hole value onto scope table
      (e.g., hole 0 = MkPin(0) for seeds with pins)
4.  For each bignat (0..n_bigs-1):
      Read size (u64), read that many u64 words, construct nat
      Push onto scope table
5.  For each word (0..n_words-1):
      Read u64, push onto scope table
6.  For each byte (0..n_bytes-1):
      Read u8, push onto scope table
7.  For each fragment (0..n_frags-1):
      Read size encoding from bitstream
      ref_width = ceil(log2(scope_table.len))
      Read (size + 1) references, each ref_width bits
      If size = 0:
        Push scope_table[ref] (leaf — just a reference)
      Else:
        Construct App chain: ((scope[f] scope[x₁]) scope[x₂]) ...
        Push result onto scope table
8.  Return scope_table.last (the root)
9.  Evaluate the template expression to normalize pins and laws
```

Step 9 is critical: the deserialized value is a **template** containing applications of `MkPin` and opcode 0 (law creation). The VM evaluates these to produce the final PLAN value with proper pins and laws.

---

## 7. Pin Encoding

Pins are not a primitive of the seed format. They are encoded as applications of the `MkPin` hole:

```gallowglass
-- Gallowglass source:
@result = expensive_computation x

-- PLAN output:
<(expensive_computation x)>    -- a pin wrapping the computation

-- Seed encoding:
-- Hole 0 = MkPin
-- Fragment for inner value: (expensive_computation x)
-- Fragment for pin: (hole_0 inner_value)
-- The VM evaluates (MkPin inner_value) to produce <inner_value>
```

For nested pins, each pin body becomes a separate fragment, and each is wrapped in a `MkPin` application.

### 7.1 Pin Deduplication

If two subtrees in the PLAN value are identical pins (same content), the serializer's DAG deduplication ensures they share a single fragment. After deserialization and evaluation, the VM's pin normalization (opcode 4) ensures they share identity.

---

## 8. Worked Examples

### 8.1 Simple Nat

The PLAN value `42`:

```
Header:
  n_holes = 0
  n_bigs  = 0
  n_words = 0
  n_bytes = 1    (42 fits in a byte)
  n_frags = 1    (one leaf fragment)

Atom table:
  bytes: [0x2A]  (42)

Fragment bitstream:
  Fragment 0 (leaf, referencing byte 42):
    size: 1 (zero args = leaf)
    ref: 0 (index 0 in scope = the byte 42)
    ref_width = ceil(log2(1)) = 0 bits... (special case: single entry)

Scope table after loading:
  [0] = 42       (byte atom)
  [1] = 42       (leaf fragment referencing [0])

Root = scope_table.last = 42
```

### 8.2 Identity Function

The PLAN value `{1 1 1}` — a law named 1, arity 1, body = argument 1 (returns its argument):

```
Header:
  n_holes = 0
  n_bigs  = 0
  n_words = 0
  n_bytes = 2    (the nats 0 and 1)
  n_frags = 1    (one fragment: the application (0 1 1 1))

Atom table:
  bytes: [0x00, 0x01]

Fragment bitstream:
  Fragment 0 (3 args: opcode 0 applied to name, arity, body):
    size encoding for 3: 001.1 (see §4.1)
    ref_width = ceil(log2(2)) = 1 bit
    refs: [0, 1, 1, 1]  →  (scope[0] scope[1] scope[1] scope[1])
                         →  (0 1 1 1)
                         →  {1 1 1}  after evaluation

Scope table:
  [0] = 0        (byte atom)
  [1] = 1        (byte atom)
  [2] = (0 1 1 1) = {1 1 1}  (law: name=1, arity=1, body=1)

Root = {1 1 1}
```

The VM evaluates `(0 1 1 1)` via opcode 0 to produce the law `{1 1 1}`.

### 8.3 Pinned Value

The PLAN value `<42>` — the nat 42, pinned:

```
Header:
  n_holes = 1     (MkPin)
  n_bigs  = 0
  n_words = 0
  n_bytes = 1     (42)
  n_frags = 1     (one fragment: (MkPin 42))

Atom table:
  bytes: [0x2A]

Fragment bitstream:
  Fragment 0 (1 arg: MkPin applied to 42):
    size encoding for 1: 01
    ref_width = ceil(log2(2)) = 1 bit
    refs: [0, 1]  →  (scope[0] scope[1])
                  →  (MkPin 42)
                  →  <42>  after evaluation

Scope table:
  [0] = MkPin(0)  (hole)
  [1] = 42        (byte atom)
  [2] = <42>      (pinned value, after evaluation)

Root = <42>
```

---

## 9. Interaction with Persistence

Seeds and persistence are distinct systems:

| Aspect | Seed format | Persistence format |
|--------|-------------|-------------------|
| Purpose | Portable serialization, bootstrap loading | On-disk state between cog events |
| Checksums | None | CRC32C (SSE4.2) |
| Magic | None | `PLANv1` (0x31764e414c50) |
| Write strategy | Single write | Dual-superblock write-ahead logging |
| Scope | Single PLAN value | Entire VM heap |

The bootstrap flow: seed file → loader → PLAN value → (optional) persist to binary.

The persistence format is a VM implementation detail, not part of the Gallowglass specification. Seeds are the portable interchange format.

---

## 10. Hash Algorithm

The xocore-tech/PLAN specification does not mandate a hash algorithm for pin content-addressing. Gallowglass specifies **BLAKE3-256** for PinId computation:

- PinId = BLAKE3-256 hash of the pin's content in canonical Seed encoding
- The hash input is the seed-format serialization of the pin's inner value (without holes — pin content is always a complete PLAN value)
- Two pins with identical content produce identical PinIds

This choice is Gallowglass-specific (see DECISIONS.md: "PLAN's spec deliberately leaves the hash algorithm as an implementation detail"). The xocore VM uses CRC32C internally for persistence checksums, but this is unrelated to content-addressing.

### 10.1 Canonical Hash Input

To compute a PinId for value `v`:

1. Serialize `v` to seed format with `n_holes = 0` (pin content has no holes)
2. Compute BLAKE3-256 over the resulting bytes
3. The 32-byte digest is the PinId

This serialization must be deterministic — the same `v` must always produce the same bytes — which the seed format guarantees by construction (deterministic atom ordering, deterministic fragment ordering, deterministic reference encoding).

---

## 11. Compatibility and Versioning

The seed format has no version field. Format changes are tracked by the xocore-tech/PLAN repository:

- **Old format:** Unary-encoded closure sizes (wasteful for large closures)
- **Current format:** Size-of-size in unary + size in binary (§4.1)

The `renew` tool in xocore-tech/PLAN converts between old and current format.

Gallowglass targets the **current format** exclusively. The bootstrap compiler must produce seeds loadable by the current xocore-tech/PLAN VM (`x/plan`). Format evolution is tracked by the xocore project; Gallowglass follows.

---

## 12. Implementation Notes

### 12.1 Bit Packing

The fragment bitstream is packed LSB-first within each byte. When reading bit `i` of the stream:

```
byte_index = i / 8
bit_offset = i % 8
bit_value  = (bytes[byte_index] >> bit_offset) & 1
```

### 12.2 Reference Width Edge Cases

When the scope table has exactly 1 entry, `ref_width = ceil(log2(1)) = 0`. A zero-width reference always refers to index 0. This occurs for the first fragment when there is exactly one hole or one atom and no other entries.

When the scope table is empty (no holes, no atoms), no fragments can reference anything — this would be a degenerate seed.

### 12.3 Padding

The serialized seed nat is padded to fill complete `u64` words (8-byte boundary). Padding bytes are zero. This is required by the Sire `seedOutput` function, which converts the seed to a nat for storage.

### 12.4 Bootstrap Compiler Requirements

The original bootstrap plan called for a binary seed emitter. This is superseded
by Plan Assembler output (see §13). The seed format is documented here for
reference and for any tooling that needs to read legacy seeds.

---

## 13. Plan Assembler Format (Reaver)

**Reference implementation:** `sol-plunder/reaver` (`src/hs/PlanAssembler.hs`,
`doc/reaver.md`). This is the current and forward-supported output format.
Binary seeds (§1–§12) are deprecated upstream.

Plan Assembler is a human-readable textual format for PLAN values. It uses
s-expression syntax and compiles to the same PLAN DAG that binary seeds represent.

### 13.1 Top-level Structure

A Plan Assembler file (`.plan`) is a sequence of **top-level forms**, each
terminated by a newline. The Gallowglass emitter produces one `#bind` form per
compiled definition:

```
(#bind "name" expr)
```

Where `name` is the decimal encoding of the definition's name nat, and `expr` is
the Plan Assembler expression for the compiled `PlanVal`.

### 13.2 Expressions

```
expr ::= nat-literal           -- decimal number → constant nat (auto-quoted in law body)
       | symbol                -- _N → de Bruijn ref N; or global name
       | "(" expr+ ")"         -- function application: (f x y) = ((f x) y)
       | "(#pin" expr ")"      -- pin constructor
       | law-form              -- see §13.3
```

**Decimal numbers** in a law body are auto-quoted as constant nats. The assembler
wraps them in `lawQuote` = `(0 k)`, i.e., a constant. Outside law bodies, numbers
are bare nats.

**Symbols** of the form `_N` (underscore followed by decimal digits) inside a law
body are de Bruijn references: `_0` = the law itself (self-ref), `_1` = first
argument, etc. Let-binding slots are allocated above the argument slots.

### 13.3 Law Form

```
law-form ::= "(#law" string sig let-bind* body ")"

sig      ::= "(" "_0" (" " "_" nat)* ")"
             -- _0 = self; _1.._k = arguments; k = arity

let-bind ::= symbol "(" expr ")"
             -- juxtaposition syntax: name(rhs) allocates next slot

body     ::= expr
```

Example (arity 2, one let-binding):

```plan
(#law "const" (_0 _1 _2)
  _3(some-expr)
  _1)
```

- `_0` = self, `_1` = first arg, `_2` = second arg
- `_3(some-expr)` = let-bind slot 3 to `some-expr`; `_3` is now in scope
- `_1` = body: return first argument

### 13.4 String Encoding

Strings in `#bind` and `#law` forms are **decimal encoding** of the name nat,
enclosed in double quotes. The name nat is the little-endian encoding of the
UTF-8 bytes of the identifier.

The Gallowglass emitter uses the decimal representation of the compiled name nat
directly (from `nat_to_decimal`).

### 13.5 Mapping from PlanVal to Plan Assembler

The M8.6 emitter (`emit_program` in `compiler/src/Compiler.gls`) converts
`List (Pair Nat PlanVal)` (output of `compile_program`) to `Bytes` (UTF-8 Plan
Assembler text).

| PlanVal at top level | Plan Assembler output |
|---|---|
| `PNat n` | decimal `n` |
| `PApp f x` | `(f_asm x_asm)` |
| `PLaw name (MkPair arity body)` | `(#law "name" sig lets... body)` |
| `PPin v` | `(#pin v_asm)` |

Inside a law body (de Bruijn context, depth = arity):

| PlanVal body node | Meaning | Assembler output |
|---|---|---|
| `PNat i` | de Bruijn ref | `_i` |
| `PApp (PNat 0) (PNat k)` | constant nat k (`cg_quote_nat`) | `k` (decimal) |
| `PApp (PApp (PNat 0) f) x` | application (`cg_bapp`) | `(f_asm x_asm)` |
| `PApp (PApp (PNat 1) rhs) body` | let-chain | `_d(rhs_asm)\n  body_seq` |
| `PPin v` | embedded constant | `(#pin v_top_asm)` |

Where `d` is the next slot index (arity + 1, arity + 2, ...) and `v_top_asm`
is the top-level emission of `v` (not inside the law body context).

### 13.6 Worked Example

```gallowglass
let const : Nat → Nat → Nat = λ x y → x
```

Compiles to `PLaw const_name (MkPair 2 (PNat 1))`. Emits:

```plan
(#bind "const_name_decimal" (#law "const_name_decimal" (_0 _1 _2) _1))
```

A let-binding example — `let add3 : Nat → Nat → Nat → Nat = λ a b c → add (add a b) c`:

```plan
(#bind "add3_decimal"
  (#law "add3_decimal" (_0 _1 _2 _3)
    _4((add _1 _2))
    (add _4 _3)))
```
