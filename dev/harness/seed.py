"""
Seed format serializer and deserializer.

Implements the xocore-tech/PLAN seed format:
- 40-byte header (5 x u64 LE)
- Machine-native atom table (bignats, words, bytes)
- Bit-packed fragment bitstream

Reference: xocore-tech/PLAN doc/planvm-amd64.tex lines 5272-5773
           sire/boot.sire lines 1931-2227
"""

import struct
import math
from dev.harness.plan import P, L, A, N, is_nat, is_pin, is_law, is_app, apply


# --- Bit stream reader ---

class BitReader:
    """Read bits LSB-first from a byte buffer."""

    def __init__(self, data):
        self.data = data
        self.bit_pos = 0

    def read_bit(self):
        byte_idx = self.bit_pos // 8
        bit_off = self.bit_pos % 8
        if byte_idx >= len(self.data):
            raise ValueError("BitReader: past end of data")
        val = (self.data[byte_idx] >> bit_off) & 1
        self.bit_pos += 1
        return val

    def read_bits(self, n):
        """Read n bits, LSB first, return as integer."""
        result = 0
        for i in range(n):
            result |= (self.read_bit() << i)
        return result


# --- Bit stream writer ---

class BitWriter:
    """Write bits LSB-first into a byte buffer."""

    def __init__(self):
        self.bits = []

    def write_bit(self, b):
        self.bits.append(b & 1)

    def write_bits(self, val, n):
        """Write n bits of val, LSB first."""
        for i in range(n):
            self.write_bit((val >> i) & 1)

    def to_bytes(self):
        # Pad to byte boundary
        while len(self.bits) % 8 != 0:
            self.bits.append(0)
        result = bytearray()
        for i in range(0, len(self.bits), 8):
            byte = 0
            for j in range(8):
                if i + j < len(self.bits):
                    byte |= (self.bits[i + j] << j)
            result.append(byte)
        return bytes(result)


# --- Fragment size encoding ---

def encode_frag_size(n):
    """Encode fragment size (number of args) per spec/07-seed-format.md §4.1."""
    bits = []
    if n == 0:
        bits.append(1)
    else:
        k = n.bit_length()  # ceil(log2(n)) + 1 for exact powers, bit_length works
        # k zero bits
        for _ in range(k):
            bits.append(0)
        # terminator
        bits.append(1)
        # low (k-1) bits of n (high bit is implicit)
        for i in range(k - 1):
            bits.append((n >> i) & 1)
    return bits


def decode_frag_size(reader):
    """Decode fragment size from bitstream."""
    # Count zero bits (size-of-size)
    k = 0
    while True:
        bit = reader.read_bit()
        if bit == 1:
            break
        k += 1

    if k == 0:
        return 0  # leaf

    # Read (k-1) bits for the size (with implicit high bit)
    if k == 1:
        return 1
    low_bits = reader.read_bits(k - 1)
    return (1 << (k - 1)) | low_bits


# --- Deserialization ---

def load_seed(data, holes=None):
    """
    Deserialize a seed from bytes.

    Args:
        data: Raw seed bytes
        holes: List of hole values (template parameters).
               Default: [MkPin] if n_holes > 0.

    Returns:
        The deserialized PLAN value.
    """
    if len(data) < 40:
        raise ValueError(f"Seed too short: {len(data)} bytes (need at least 40)")

    # Read header
    n_holes, n_bigs, n_words, n_bytes, n_frags = struct.unpack_from('<QQQQQ', data, 0)
    offset = 40

    # Build scope table
    scope = []

    # 1. Holes
    if holes is None:
        # Default: hole 0 = pin constructor (opcode 4 as a function)
        # MkPin is opcode 0: applying nat 0 to a value pins it
        # Actually, in the VM, MkPin is passed as a pre-constructed value
        # For our purposes: hole 0 = a callable that pins its argument
        holes = [P(N(0))] * n_holes  # P(0) = pin containing opcode 0 = pin constructor
    for h in holes[:n_holes]:
        scope.append(h)

    # 2. Bignats
    big_sizes = []
    for _ in range(n_bigs):
        sz = struct.unpack_from('<Q', data, offset)[0]
        big_sizes.append(sz)
        offset += 8

    for sz in big_sizes:
        words = struct.unpack_from(f'<{sz}Q', data, offset)
        offset += sz * 8
        val = 0
        for i, w in enumerate(words):
            val |= (w << (64 * i))
        scope.append(val)

    # 3. Words
    for _ in range(n_words):
        val = struct.unpack_from('<Q', data, offset)[0]
        offset += 8
        scope.append(val)

    # 4. Bytes
    for _ in range(n_bytes):
        val = data[offset]
        offset += 1
        scope.append(val)

    # 5. Fragments
    reader = BitReader(data[offset:])
    for _ in range(n_frags):
        size = decode_frag_size(reader)
        scope_len = len(scope)
        if scope_len <= 1:
            ref_width = 0 if scope_len == 0 else 0
        else:
            ref_width = math.ceil(math.log2(scope_len))

        if size == 0:
            # Leaf: just a reference
            if ref_width == 0:
                ref = 0
            else:
                ref = reader.read_bits(ref_width)
            scope.append(scope[ref])
        else:
            # Application: (f x1 x2 ... xn)
            refs = []
            for _ in range(size + 1):
                if ref_width == 0:
                    refs.append(0)
                else:
                    refs.append(reader.read_bits(ref_width))
            # Build left-associative application
            result = scope[refs[0]]
            for i in range(1, len(refs)):
                result = A(result, scope[refs[i]])
            scope.append(result)

    if not scope:
        raise ValueError("Empty seed: no values")

    # Evaluate the template to normalize pins and laws.
    # The seed stores templates (e.g. (1 arity name body) for laws);
    # evaluation reduces these to proper PLAN values.
    result = scope[-1]
    try:
        from dev.harness.plan import evaluate
        result = evaluate(result)
    except Exception:
        pass  # return unevaluated if evaluation fails (e.g. free holes)
    return result


# --- Serialization ---

def _intern(val, table, refs, _live=None):
    """
    Traverse a PLAN value as a DAG, building a deduplicated table.
    Returns the index of val in the table.

    _live: list to keep intermediate objects alive (prevents CPython address reuse).
    """
    # Use id() for object identity deduplication
    vid = id(val)
    if vid in refs:
        return refs[vid]

    if is_nat(val):
        # Check if already in table by value (skip non-atom entries)
        for i, entry in enumerate(table):
            if entry[0] == 'atom' and entry[1] == val:
                refs[vid] = i
                return i
        idx = len(table)
        table.append(('atom', val))
        refs[vid] = idx
        return idx

    if is_pin(val):
        # Pins become: (MkPin inner_value)
        inner_idx = _intern(val.val, table, refs, _live)
        idx = len(table)
        table.append(('pin_app', inner_idx))
        refs[vid] = idx
        return idx

    if is_law(val):
        # Laws encode as the PLAN template: (P(1) (((0 (arity-1)) name) body))
        # where P(1) is the law-creation opcode pin (op 1 = create law).
        # op 1 takes x = A(A(A(0, arity-1), name), body) and returns L(arity, name, body).
        # P(1) itself needs MkPin, so has_pins must be True for this seed.
        #
        # Keep the template object alive in _live to prevent CPython from reusing
        # its memory address (which would cause false id() cache hits for later objects).
        template = A(P(1), A(A(A(0, val.arity - 1), val.name), val.body))
        if _live is not None:
            _live.append(template)
        idx = _intern(template, table, refs, _live)
        refs[vid] = idx  # cache law id → template idx so re-interning is O(1)
        return idx

    if is_app(val):
        fun_idx = _intern(val.fun, table, refs, _live)
        arg_idx = _intern(val.arg, table, refs, _live)
        idx = len(table)
        table.append(('app', fun_idx, arg_idx))
        refs[vid] = idx
        return idx

    raise ValueError(f"intern: unknown value type {type(val)}")


def save_seed(val):
    """
    Serialize a PLAN value to seed bytes.

    Args:
        val: The PLAN value to serialize.

    Returns:
        bytes: The seed file content.
    """
    # Internalize the value (may convert Laws to pin-containing templates).
    # _live keeps intermediate template objects alive so CPython does not reuse
    # their memory addresses, which would corrupt the id()-based dedup cache.
    table = []
    refs = {}
    _live = []
    _intern(val, table, refs, _live)

    # Determine if we need holes AFTER interning — _intern may introduce
    # pin_app entries (e.g. laws become A(P(1), ...) which needs MkPin)
    has_pins = any(entry[0] == 'pin_app' for entry in table)

    # Separate atoms and cells
    atoms = []  # (original_idx, nat_value)
    cells = []  # (original_idx, cell_data)
    n_holes = 1 if has_pins else 0

    for i, entry in enumerate(table):
        if entry[0] == 'atom':
            atoms.append((i, entry[1]))
        else:
            cells.append((i, entry))

    # Classify atoms by size
    byte_atoms = [(i, v) for i, v in atoms if 0 <= v <= 255]
    word_atoms = [(i, v) for i, v in atoms if 256 <= v < (1 << 64)]
    big_atoms = [(i, v) for i, v in atoms if v >= (1 << 64)]

    # Build index remapping: old table index -> scope table index
    remap = {}
    scope_idx = 0

    # Holes first
    if has_pins:
        # hole 0 = MkPin — not in our table, but occupies scope index 0
        scope_idx = 1

    # Bignats
    for old_idx, _ in big_atoms:
        remap[old_idx] = scope_idx
        scope_idx += 1

    # Words
    for old_idx, _ in word_atoms:
        remap[old_idx] = scope_idx
        scope_idx += 1

    # Bytes
    for old_idx, _ in byte_atoms:
        remap[old_idx] = scope_idx
        scope_idx += 1

    # Cells (in dependency order — we rely on table being in dependency order)
    for old_idx, _ in cells:
        remap[old_idx] = scope_idx
        scope_idx += 1

    # Write header
    n_bigs = len(big_atoms)
    n_words = len(word_atoms)
    n_bytes_count = len(byte_atoms)
    n_frags = len(cells)

    header = struct.pack('<QQQQQ', n_holes, n_bigs, n_words, n_bytes_count, n_frags)

    # Write atom table
    atom_data = bytearray()

    # Bignat sizes
    for _, v in big_atoms:
        word_count = (v.bit_length() + 63) // 64
        atom_data.extend(struct.pack('<Q', word_count))

    # Bignat data
    for _, v in big_atoms:
        word_count = (v.bit_length() + 63) // 64
        for w in range(word_count):
            atom_data.extend(struct.pack('<Q', (v >> (64 * w)) & ((1 << 64) - 1)))

    # Word values
    for _, v in word_atoms:
        atom_data.extend(struct.pack('<Q', v))

    # Byte values
    for _, v in byte_atoms:
        atom_data.append(v)

    # Write fragment bitstream
    writer = BitWriter()
    current_scope_size = n_holes + n_bigs + n_words + n_bytes_count

    for old_idx, entry in cells:
        kind = entry[0]

        if kind == 'app':
            # (f x) — 1 arg
            fun_scope = remap[entry[1]]
            arg_scope = remap[entry[2]]
            for b in encode_frag_size(1):
                writer.write_bit(b)
            ref_width = max(1, math.ceil(math.log2(current_scope_size))) if current_scope_size > 1 else 0
            if ref_width > 0:
                writer.write_bits(fun_scope, ref_width)
                writer.write_bits(arg_scope, ref_width)

        elif kind == 'pin_app':
            # (MkPin inner) — 1 arg, hole 0 applied to inner
            inner_scope = remap[entry[1]]
            for b in encode_frag_size(1):
                writer.write_bit(b)
            ref_width = max(1, math.ceil(math.log2(current_scope_size))) if current_scope_size > 1 else 0
            if ref_width > 0:
                writer.write_bits(0, ref_width)  # hole 0 = MkPin
                writer.write_bits(inner_scope, ref_width)

        current_scope_size += 1

    frag_data = writer.to_bytes()

    # Combine and pad to 8-byte boundary
    result = header + bytes(atom_data) + frag_data
    pad_len = (8 - len(result) % 8) % 8
    result += b'\x00' * pad_len

    return result


def _has_pins(val):
    """Check if a PLAN value contains any pins."""
    seen = set()

    def walk(v):
        vid = id(v)
        if vid in seen:
            return False
        seen.add(vid)
        if is_pin(v):
            return True
        if is_app(v):
            return walk(v.fun) or walk(v.arg)
        if is_law(v):
            return walk(v.body)
        return False

    return walk(val)
