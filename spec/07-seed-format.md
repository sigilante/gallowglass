# Seed Serialization Format

**Spec version:** 0.1
**Depends on:** SPEC.md, spec/04-plan-encoding.md

This document specifies the Seed format: the canonical binary serialization of PLAN values. Every compiled Gallowglass program is emitted as a Seed file. Every pin's BLAKE3-256 hash is computed over the canonical Seed encoding of its content. The format is deterministic, DAG-aware, and streamable.

---

## 1. Overview

A Seed is a self-contained binary encoding of a PLAN value and its transitive pin dependencies. It serves three purposes:

1. **Persistence.** Compiled programs are stored on disk as Seed files.
2. **Network transit.** PLAN values are transmitted between cogs and between machines as Seed bytes.
3. **Bootstrap loading.** The VM loads its initial state from a Seed file.

### 1.1 Properties (Normative)

- **Deterministic.** The same PLAN value always produces the same Seed bytes. This is critical because the BLAKE3-256 hash of a Seed encoding is the PinId used for content-addressing. Two VMs computing the same pin's hash must produce identical results.
- **DAG-aware.** Structural sharing in the PLAN heap is preserved. A node referenced by multiple parents is serialized once and referenced by index.
- **Streamable.** The format is structured so that a deserializer can begin constructing the PLAN heap before the full Seed has been received. The pin table and node table are ordered so that every back-reference resolves to an already-deserialized node.
- **Self-describing.** No external schema is needed. The Seed header identifies the format and version; the body encodes the complete PLAN DAG.
- **Compact.** Natural numbers use variable-length encoding. Small values (the common case) occupy minimal space.

### 1.2 Non-Goals

- Compression. The Seed format does not incorporate general-purpose compression (gzip, zstd). A transport layer may compress Seed bytes, but the canonical encoding is uncompressed.
- Random access. Seed is a sequential format. Extracting a single node requires reading the node table up to that index.
- Human readability. Glass IR is the human-readable view of PLAN values. Seed is the machine-readable serialization.

---

## 2. Design Goals

| Goal | Rationale |
|---|---|
| Deterministic serialization | PinIds are BLAKE3-256 hashes of Seed bytes. Non-determinism would break content-addressing. |
| Compact representation | Seeds are stored and transmitted frequently. Variable-length nat encoding minimizes overhead for small values. |
| DAG-aware sharing | PLAN heaps are DAGs (pins reference other pins). Serializing as a tree would exponentially inflate size. |
| Streamable deserialization | Large seeds (full program images) benefit from incremental loading. Topological ordering enables this. |
| Self-describing | A Seed file must be interpretable without external metadata. The header carries format identification and version. |
| Canonical encoding | Every PLAN value has exactly one valid Seed encoding. There are no optional fields, no alternative encodings, no padding choices. |

---

## 3. Seed Structure

A Seed file consists of five sections in fixed order:

```
+------------------+
| Header           |   Magic bytes + version
+------------------+
| Pin Table        |   External pin references (BLAKE3-256 hashes)
+------------------+
| Node Table       |   Serialized PLAN DAG in topological order
+------------------+
| Root Index       |   Index of the top-level value in the node table
+------------------+
| Checksum         |   BLAKE3-256 over all preceding bytes
+------------------+
```

### 3.1 Header (Normative)

| Offset | Length | Field | Value |
|--------|--------|-------|-------|
| 0 | 4 | Magic bytes | `0x53 0x45 0x45 0x44` (ASCII `SEED`) |
| 4 | 1 | Version major | `0x00` |
| 5 | 1 | Version minor | `0x01` |
| 6 | 2 | Flags | `0x00 0x00` (reserved, must be zero) |

Total header size: **8 bytes**.

The magic bytes identify the file as a Seed. A deserializer that encounters unknown magic bytes must reject the input immediately.

The flags field is reserved for future use. A deserializer must reject any Seed where the flags field is non-zero (forward-compatible rejection).

### 3.2 Pin Table

| Offset | Length | Field |
|--------|--------|-------|
| 8 | VarNat | `pin_count` — number of external pin references |
| 8 + sizeof(pin_count) | 32 * pin_count | Pin hashes — BLAKE3-256 hashes, 32 bytes each, in order of first reference |

Each entry is a raw 32-byte BLAKE3-256 hash. Entries are ordered by their first appearance during the serializer's DAG traversal (depth-first, left-to-right). This ordering is deterministic because the traversal order is fixed.

The pin table lists only **external** pin references: pins whose content is not defined within this Seed. Pins whose content appears in the node table (local pins) are not listed here.

### 3.3 Node Table

| Offset | Length | Field |
|--------|--------|-------|
| After pin table | VarNat | `node_count` — number of nodes |
| After node_count | variable | `node_count` node entries, concatenated |

Nodes are serialized in **topological order**: if node A references node B, then B appears before A in the table. This guarantees that during deserialization, every reference resolves to an already-constructed node. Node indices are zero-based.

See section 4 for the encoding of individual nodes.

### 3.4 Root Index

| Offset | Length | Field |
|--------|--------|-------|
| After node table | VarNat | `root_index` — index into the node table |

The root index identifies the top-level PLAN value that the Seed encodes. It must be a valid index in the range `[0, node_count)`.

### 3.5 Checksum

| Offset | Length | Field |
|--------|--------|-------|
| After root index | 32 | BLAKE3-256 hash of all preceding bytes (header through root index) |

The checksum covers the entire Seed up to but not including the checksum itself. A deserializer must compute the hash of all preceding bytes and reject the Seed if the checksum does not match.

---

## 4. Node Encoding

Each node in the node table begins with a **tag byte** that identifies the PLAN constructor, followed by constructor-specific fields.

### 4.1 Tag Bytes (Normative)

| Tag | Constructor | Fields |
|-----|-------------|--------|
| `0x00` | Nat | value (VarNat) |
| `0x01` | Law | name (VarNat), arity (VarNat), body (NodeRef) |
| `0x02` | App | function (NodeRef), argument (NodeRef) |
| `0x03` | Pin (local) | content (NodeRef) |
| `0x04` | Pin (external) | pin_table_index (VarNat) |

Tag values `0x05` through `0xFF` are reserved. A deserializer must reject any node with a reserved tag.

### 4.2 NodeRef

A **NodeRef** is a VarNat encoding an index into the node table. Because nodes are in topological order, a NodeRef in node `i` must reference a node with index strictly less than `i`. A deserializer must reject any forward reference.

### 4.3 Nat Node (`0x00`)

```
+------+---------+
| 0x00 | VarNat  |
+------+---------+
  tag    value
```

The value is the natural number encoded as a VarNat (see section 6).

### 4.4 Law Node (`0x01`)

```
+------+---------+---------+---------+
| 0x01 | VarNat  | VarNat  | NodeRef |
+------+---------+---------+---------+
  tag    name      arity     body
```

- **name**: The law's name, encoded as a nat. Function names are UTF-8 bytes packed into a little-endian nat (see `spec/04-plan-encoding.md` section 1).
- **arity**: The law's parameter count (always > 0 for well-formed laws).
- **body**: A NodeRef pointing to the PLAN expression that is the law's body.

### 4.5 App Node (`0x02`)

```
+------+---------+---------+
| 0x02 | NodeRef | NodeRef |
+------+---------+---------+
  tag    function  argument
```

Application is left-associative. A multi-argument application `(f a b c)` is encoded as nested App nodes: `App(App(App(f, a), b), c)`.

### 4.6 Local Pin Node (`0x03`)

```
+------+---------+
| 0x03 | NodeRef |
+------+---------+
  tag    content
```

A local pin is a pin whose content is defined within this Seed. The NodeRef points to the node that is the pin's content. The pin's BLAKE3-256 hash is computed from the canonical Seed encoding of the content subtree (see section 8).

### 4.7 External Pin Node (`0x04`)

```
+------+---------+
| 0x04 | VarNat  |
+------+---------+
  tag    pin_table_index
```

An external pin references a pin whose content is not in this Seed. The `pin_table_index` is an index into the pin table (section 3.2). The runtime must resolve this reference by looking up the hash in its pin store.

---

## 5. DAG Sharing

### 5.1 Serialization Traversal (Normative)

The serializer traverses the PLAN value as a **directed acyclic graph**, not as a tree. The traversal proceeds depth-first, left-to-right (function before argument in App nodes, body before the Law node in Law nodes).

Each unique PLAN node is assigned a node table index the first time it is encountered. Subsequent encounters of the same node emit a NodeRef to the existing index rather than re-serializing the node.

### 5.2 Node Identity

Two nodes are "the same" for sharing purposes if they are the same object in the PLAN heap (pointer equality). The serializer maintains an identity map from heap addresses to node table indices. This preserves exactly the sharing that exists in the heap — no more, no less.

Note: The serializer does not perform structural equality checks to discover additional sharing opportunities. Two structurally identical but distinct heap objects are serialized as separate nodes. This is intentional: structural deduplication would require hashing every subtree during serialization, which is expensive and unnecessary. The PLAN runtime already deduplicates pins by hash; non-pin sharing is a runtime optimization concern, not a serialization concern.

### 5.3 Pin Deduplication

Pins are deduplicated by their BLAKE3-256 hash regardless of heap identity. If two Pin nodes in the heap have the same hash (as they must if they have the same content), they are serialized as references to the same node table entry (for local pins) or the same pin table entry (for external pins).

---

## 6. Nat Encoding (VarNat)

Natural numbers are encoded using a variable-length format called **VarNat**. This encoding is used for all nat values in the Seed format: node values, law names, law arities, node table indices, pin table indices, and length fields.

### 6.1 Encoding Rules (Normative)

| Value range | Encoding |
|---|---|
| 0 to 127 | Single byte: the value itself. High bit is 0. |
| 128 and above | First byte: `0x80 \| length_minus_one`. Followed by `length` bytes of the value in little-endian order. |

Where `length` is the number of bytes needed to represent the value (the minimal byte count with no leading zero bytes), and `length_minus_one` is `length - 1`. The first byte's low 7 bits encode `length_minus_one`, and the high bit is set to 1.

### 6.2 Detailed Encoding Procedure

1. If the value is in `[0, 127]`: emit one byte equal to the value.
2. Otherwise:
   a. Compute the little-endian byte representation of the value with no leading zero bytes. Call this `payload` with length `L`.
   b. Emit the byte `0x80 | (L - 1)`. Since `L - 1` must fit in 7 bits, the maximum payload length is 128 bytes (supporting nats up to 2^1024 - 1). This is sufficient for all practical purposes; nats larger than 2^1024 are rejected.
   c. Emit `L` bytes of `payload` in little-endian order.

### 6.3 Canonicality (Normative)

The encoding is **canonical**: each natural number has exactly one valid VarNat encoding. A deserializer must reject any non-canonical encoding:

- A value in `[0, 127]` must use the single-byte form. The multi-byte form is invalid for these values.
- The payload must have no trailing zero bytes (little-endian representation, so trailing zeros are leading zeros of the value). Exception: the value 0 is always encoded as the single byte `0x00`.
- The length field must be the minimal length for the value.

### 6.4 Examples

| Value | Hex encoding | Explanation |
|---|---|---|
| 0 | `00` | Single byte, value 0 |
| 1 | `01` | Single byte, value 1 |
| 127 | `7F` | Single byte, value 127 |
| 128 | `80 80` | Multi-byte: length=1 (`0x80 \| 0`), payload=`0x80` |
| 255 | `80 FF` | Multi-byte: length=1 (`0x80 \| 0`), payload=`0xFF` |
| 256 | `81 00 01` | Multi-byte: length=2 (`0x80 \| 1`), payload=`0x00 0x01` (little-endian) |
| 65535 | `81 FF FF` | Multi-byte: length=2, payload=`0xFF 0xFF` |
| 65536 | `82 00 00 01` | Multi-byte: length=3, payload=`0x00 0x00 0x01` |

---

## 7. Pin Table

### 7.1 Purpose

The pin table lists the BLAKE3-256 hashes of all external pins that this Seed depends on. An external pin is a pin whose content is not serialized within the Seed's node table. The runtime must resolve each external pin by looking up its hash in a pin store (local cache, network, or other source) before the Seed can be fully loaded.

### 7.2 Structure (Normative)

The pin table immediately follows the header. It begins with a VarNat `pin_count`, followed by `pin_count` entries of exactly 32 bytes each (raw BLAKE3-256 hashes).

```
+----------+-------------------+-------------------+-----+
| pin_count| hash_0 (32 bytes) | hash_1 (32 bytes) | ... |
+----------+-------------------+-------------------+-----+
```

### 7.3 Ordering (Normative)

Pin table entries are ordered by **first encounter** during the serializer's depth-first left-to-right traversal of the PLAN DAG. This ordering is deterministic because the traversal order is fixed by the DAG structure and the left-to-right convention.

A given hash appears at most once in the pin table. If the same external pin is referenced multiple times in the DAG, all references use the same pin table index.

### 7.4 Local vs. External Pins

A pin is **local** if its content (the PLAN value it wraps) is serialized within the node table. Local pins use tag `0x03` and reference a NodeRef.

A pin is **external** if its content is not in this Seed. External pins use tag `0x04` and reference a pin table index.

The decision of which pins are local vs. external is made by the serializer. The minimal Seed for a PLAN value includes all reachable content as local pins (pin table is empty). A partial Seed can externalize pins that the recipient is expected to already have, reducing transfer size.

### 7.5 Pin Resolution

During deserialization, the runtime must resolve every external pin before the PLAN heap is fully constructed. Resolution proceeds as follows:

1. Read the pin table.
2. For each hash, look up the pin in the runtime's pin store.
3. If the pin is found, substitute its content. If the pin is not found, deserialization fails with a `MissingPin` error listing the unresolved hash.

The runtime may resolve pins lazily (on first access) or eagerly (before returning the deserialized value). The semantics are identical either way because PLAN evaluation is lazy.

---

## 8. Hash Canonicalization

### 8.1 PinId Computation (Normative)

A pin's PinId (its BLAKE3-256 hash) is computed over the **canonical Seed encoding** of the pin's content subtree. Specifically:

1. Serialize the pin's content as a complete, self-contained Seed (all transitive pin dependencies are externalized in the pin table).
2. Compute the BLAKE3-256 hash of the resulting Seed bytes (including header, pin table, node table, root index, and checksum).

This means the PinId depends on the content of the pin and the PinIds of all pins it references (since external pins appear as hashes in the pin table). This is the Merkle property: the hash of a node transitively covers all nodes it depends on.

### 8.2 Cross-VM Agreement (Normative)

Two VMs computing the same pin's PinId must produce identical results. This is guaranteed by the canonical encoding rules:

- The Seed format is deterministic (section 1.1).
- VarNat encoding is canonical (section 6.3).
- Node table ordering is deterministic (topological, depth-first, left-to-right) (section 5.1).
- Pin table ordering is deterministic (first encounter) (section 7.3).

Any implementation that deviates from these rules will produce different PinIds and will be incompatible with the Gallowglass ecosystem. Cross-VM PinId agreement is a first-class CI test (see DECISIONS.md).

### 8.3 Hash Input Structure

The hash input for PinId computation is the complete Seed bytes. This differs from some systems that hash only the content bytes — the Seed header and checksum are included in the hash input. This is intentional: it binds the PinId to a specific Seed format version, preventing accidental collisions between different format versions encoding the same logical content.

---

## 9. Bootstrapping

### 9.1 Bootstrap Seed Loading

The VM's bootstrap procedure is:

1. Read the Seed file from disk (or receive over the network).
2. Validate the magic bytes and version. Reject if unknown.
3. Validate the checksum. Reject if corrupt.
4. Read the pin table.
5. Resolve all external pins. Fail if any are unresolved.
6. Deserialize the node table, constructing the PLAN heap incrementally (each node can be constructed immediately because topological ordering guarantees all dependencies are already built).
7. Return the PLAN value at the root index.

### 9.2 Self-Referential Bootstrap

The Seed format is defined in terms of PLAN, which is loaded from a Seed. This circularity is broken by the bootstrap compiler:

1. The bootstrap compiler (written in Sire) produces Seed bytes directly, using a hard-coded implementation of the Seed format.
2. The VM loads this Seed to obtain the initial PLAN heap.
3. The self-hosting compiler (once compiled) can produce Seeds using its own Seed serializer — which is itself a PLAN program loaded from a Seed.

The bootstrap compiler's Seed output must be byte-identical to what the self-hosting compiler's Seed serializer would produce for the same PLAN value. This is verified in CI.

### 9.3 Initial Pin Store

On first boot, the VM's pin store is empty. The bootstrap Seed must therefore have an empty pin table (all pins are local). Subsequent Seeds may reference external pins that were loaded from earlier Seeds and retained in the pin store.

---

## 10. Versioning

### 10.1 Version Field Semantics

The version field in the header consists of a major and minor byte:

- **Major version change:** The node encoding, tag assignments, or structural layout has changed in a backward-incompatible way. A deserializer must reject Seeds with an unrecognized major version.
- **Minor version change:** New tag values or new sections have been added, but existing tag values and sections retain their meaning. A deserializer may attempt to load a Seed with a higher minor version, ignoring unknown tags, but must fail cleanly if it encounters a tag or section it cannot interpret.

### 10.2 Current Version

The current version is **0.1**. Major version 0 indicates the format is pre-stable. Breaking changes are permitted before major version 1.

### 10.3 Forward Compatibility

A deserializer encountering an unknown major version must reject the Seed with the error:

```
seed version mismatch: expected major version 0, got <N>
```

A deserializer encountering unknown flags (non-zero flags field) must reject the Seed:

```
seed flags not recognized: 0x<HH><HH>
```

### 10.4 Backward Compatibility

Once major version 1 is reached, the format is append-only within a major version: new minor versions may add new tag values and new sections after the checksum, but may not change the meaning of existing tags or sections. A version 1.0 deserializer can load any version 1.x Seed by ignoring unknown tags and trailing sections.

---

## 11. Error Handling

### 11.1 Error Conditions (Normative)

The deserializer must detect and report each of the following conditions:

| Condition | Detection | Error |
|---|---|---|
| Bad magic bytes | First 4 bytes are not `SEED` | `not a seed file` |
| Unknown major version | Major version > supported | `seed version mismatch: expected major version <E>, got <G>` |
| Non-zero flags | Flags field is not `0x0000` | `seed flags not recognized: 0x<HHHH>` |
| Checksum mismatch | Computed BLAKE3-256 does not match stored checksum | `seed checksum mismatch` |
| Truncated seed | Unexpected end of input during any read | `seed truncated at byte <offset>` |
| Invalid VarNat | Non-canonical encoding (section 6.3) | `non-canonical varnat at byte <offset>` |
| Forward reference | NodeRef >= current node index | `forward reference in node table: node <i> references node <j>` |
| Invalid tag | Tag byte >= `0x05` | `unknown node tag 0x<HH> at byte <offset>` |
| Root out of range | Root index >= node_count | `root index <i> out of range (node_count = <n>)` |
| Pin index out of range | pin_table_index >= pin_count | `pin table index <i> out of range (pin_count = <n>)` |
| Missing external pin | Hash not found in pin store | `missing external pin: <hex hash>` |
| Oversized VarNat | VarNat payload length > 128 | `varnat exceeds maximum size at byte <offset>` |

### 11.2 Partial Failure

A deserializer must not return a partially constructed PLAN value. If any error is detected, the entire deserialization fails. There is no recovery mechanism within the Seed format itself — the caller must obtain a valid Seed (re-download, re-compile, etc.).

---

## 12. Wire Format Specification

### 12.1 Byte Order

All multi-byte values in the Seed format are **little-endian**. This applies to:

- VarNat payloads (section 6)
- BLAKE3-256 hashes are stored as raw bytes in their canonical byte order (as output by the BLAKE3 algorithm)

### 12.2 Alignment

The Seed format has **no alignment requirements**. All fields are packed with no padding. This simplifies implementation and ensures deterministic byte layout.

### 12.3 Maximum Sizes

| Field | Maximum | Rationale |
|---|---|---|
| VarNat value | 2^1024 - 1 | 128-byte payload (section 6.2). Sufficient for any practical nat. |
| Pin table entries | 2^(7*128) - 1 | Limited by VarNat `pin_count`. Practically limited by memory. |
| Node table entries | 2^(7*128) - 1 | Limited by VarNat `node_count`. Practically limited by memory. |
| Total Seed size | No format limit | Limited by storage and memory. Checksum covers all bytes. |

### 12.4 Complete Byte Layout

```
Offset  Field                Size
──────  ─────                ────
0       Magic "SEED"         4 bytes
4       Version major        1 byte
5       Version minor        1 byte
6       Flags                2 bytes
8       pin_count            VarNat
...     pin_hash[0]          32 bytes
...     pin_hash[1]          32 bytes
...     ...
...     pin_hash[pin_count-1]  32 bytes
...     node_count           VarNat
...     node[0]              variable (tag + fields)
...     node[1]              variable (tag + fields)
...     ...
...     node[node_count-1]   variable (tag + fields)
...     root_index           VarNat
...     checksum             32 bytes (BLAKE3-256)
```

---

## 13. Examples

### 13.1 Encoding a Simple Nat

The PLAN value `42` (a single nat).

**Node table:** One node: `Nat(42)`.

**Seed hex dump:**

```
Offset  Bytes                          Field
──────  ─────                          ─────
00      53 45 45 44                    Magic "SEED"
04      00                             Version major: 0
05      01                             Version minor: 1
06      00 00                          Flags: 0x0000
08      00                             pin_count: 0
09      01                             node_count: 1
0A      00 2A                          node[0]: tag=0x00 (Nat), value=42
0C      00                             root_index: 0
0D      <32 bytes>                     BLAKE3-256 checksum
```

Total size: 45 bytes (13 + 32 checksum).

### 13.2 Encoding the Identity Function

The PLAN value `{0 1 1}` — a law named `0` (anonymous), arity 1, body is argument index 1 (the first and only parameter).

This is the identity function: `fn x -> x`.

**Node table:** Two nodes.

| Index | Node |
|---|---|
| 0 | `Nat(1)` — the body (de Bruijn index 1, referencing the parameter) |
| 1 | `Law(name=0, arity=1, body=NodeRef(0))` |

**Seed hex dump:**

```
Offset  Bytes                          Field
──────  ─────                          ─────
00      53 45 45 44                    Magic "SEED"
04      00                             Version major: 0
05      01                             Version minor: 1
06      00 00                          Flags: 0x0000
08      00                             pin_count: 0
09      02                             node_count: 2
0A      00 01                          node[0]: tag=0x00 (Nat), value=1
0C      01 00 01 00                    node[1]: tag=0x01 (Law), name=0, arity=1, body=NodeRef(0)
10      01                             root_index: 1
11      <32 bytes>                     BLAKE3-256 checksum
```

Total size: 49 bytes (17 + 32 checksum).

### 13.3 Encoding a Program with Pins

Consider a small program: the `add_one` function from `spec/04-plan-encoding.md`, pinned and then applied to the value 5.

```
PLAN structure:
  (<add_one_pin> 5)

where add_one_pin = Pin({N_add_one 1 (3 1)})
```

The structure decomposes into the following nodes (topological order):

| Index | Node | Description |
|---|---|---|
| 0 | `Nat(3)` | Opcode 3 (increment) |
| 1 | `Nat(1)` | De Bruijn index 1 (parameter) |
| 2 | `App(0, 1)` | `(3 1)` — apply increment to parameter |
| 3 | `Nat(N_add_one)` | Law name (encoded as nat) |
| 4 | `Law(3, 1, 2)` | `{N_add_one 1 (3 1)}` — the law |
| 5 | `Pin(local: 4)` | `<{N_add_one 1 (3 1)}>` — pinned law |
| 6 | `Nat(5)` | The argument value 5 |
| 7 | `App(5, 6)` | `(<add_one_pin> 5)` — application |

Assuming `N_add_one` = `7236708724627300705` (the UTF-8 bytes of `"add_one"` packed as a little-endian nat):

**Seed hex dump:**

```
Offset  Bytes                          Field
──────  ─────                          ─────
00      53 45 45 44                    Magic "SEED"
04      00                             Version major: 0
05      01                             Version minor: 1
06      00 00                          Flags: 0x0000
08      00                             pin_count: 0
09      08                             node_count: 8
0A      00 03                          node[0]: Nat(3)
0C      00 01                          node[1]: Nat(1)
0E      02 00 01                       node[2]: App(func=0, arg=1)
11      00 87 61 64 64 5F 6F 6E 65     node[3]: Nat(N_add_one), VarNat multi-byte
1A      01 03 01 02                    node[4]: Law(name=3, arity=1, body=2)
1E      03 04                          node[5]: Pin(local, content=4)
20      00 05                          node[6]: Nat(5)
22      02 05 06                       node[7]: App(func=5, arg=6)
25      07                             root_index: 7
26      <32 bytes>                     BLAKE3-256 checksum
```

Total size: 70 bytes (38 + 32 checksum).

Note how Nat(1) at index 1 is used both as the law body reference (de Bruijn index for the parameter) and as the law arity. These are the same nat value, but because they are distinct heap objects in this example, the serializer emits them separately. If the PLAN runtime had shared them (pointer equality), they would be a single node referenced twice.

### 13.4 External Pin Reference

If the `add_one` pin from section 13.3 were an external dependency (already in the recipient's pin store), the Seed would instead contain:

```
Offset  Bytes                          Field
──────  ─────                          ─────
00      53 45 45 44                    Magic "SEED"
04      00                             Version major: 0
05      01                             Version minor: 1
06      00 00                          Flags: 0x0000
08      01                             pin_count: 1
09      <32 bytes>                     pin_hash[0]: BLAKE3-256 of add_one pin
29      03                             node_count: 3
2A      04 00                          node[0]: Pin(external, pin_table_index=0)
2C      00 05                          node[1]: Nat(5)
2E      02 00 01                       node[2]: App(func=0, arg=1)
31      02                             root_index: 2
32      <32 bytes>                     BLAKE3-256 checksum
```

Total size: 82 bytes (50 + 32 checksum). The Seed is larger than the fully-local version because it includes the 32-byte hash, but the recipient does not need to deserialize the add_one law's internal structure — it is already in the pin store.

---

## 14. Normative vs. Informative Summary

### 14.1 Normative (Required for Conformance)

- Header structure and magic bytes (section 3.1)
- Tag byte assignments (section 4.1)
- VarNat encoding and canonicality rules (section 6)
- Topological node ordering with back-references only (sections 3.3, 4.2)
- Pin table ordering by first encounter (section 7.3)
- Deterministic traversal order: depth-first, left-to-right (section 5.1)
- Hash canonicalization procedure (section 8.1)
- Checksum computation and validation (section 3.5)
- Error detection and rejection conditions (section 11.1)
- Little-endian byte order (section 12.1)
- No alignment or padding (section 12.2)

### 14.2 Informative (Explanatory)

- Design rationale and non-goals (sections 1.2, 2)
- Examples (section 13)
- Versioning policy (section 10, except the current version number which is normative)
- Bootstrap procedure description (section 9)
- Lazy vs. eager pin resolution (section 7.5, last paragraph)
