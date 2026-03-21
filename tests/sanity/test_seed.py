#!/usr/bin/env python3
"""
Sanity tests for seed serialization and deserialization.

Run: python3 -m pytest tests/sanity/test_seed.py -v
  or: python3 tests/sanity/test_seed.py
"""

import sys
import os
import struct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dev.harness.plan import P, L, A, N, is_nat, is_pin, is_law, is_app
from dev.harness.seed import load_seed, save_seed, BitReader, BitWriter, encode_frag_size, decode_frag_size


# ============================================================
# Bit stream
# ============================================================

def test_bit_roundtrip():
    """Bits written and read back are identical."""
    w = BitWriter()
    bits = [1, 0, 1, 1, 0, 0, 1, 0, 1]
    for b in bits:
        w.write_bit(b)
    data = w.to_bytes()
    r = BitReader(data)
    for expected in bits:
        assert r.read_bit() == expected

def test_bits_value_roundtrip():
    """Multi-bit values round-trip correctly."""
    w = BitWriter()
    w.write_bits(0b10110, 5)
    r = BitReader(w.to_bytes())
    assert r.read_bits(5) == 0b10110


# ============================================================
# Fragment size encoding
# ============================================================

def test_frag_size_zero():
    """Size 0 encodes as single 1 bit."""
    bits = encode_frag_size(0)
    assert bits == [1]

def test_frag_size_one():
    """Size 1 encodes as 01."""
    bits = encode_frag_size(1)
    assert bits == [0, 1]

def test_frag_size_two():
    """Size 2 encodes as 001.0 (4 bits)."""
    bits = encode_frag_size(2)
    assert bits == [0, 0, 1, 0]

def test_frag_size_three():
    """Size 3 encodes as 001.1 (4 bits)."""
    bits = encode_frag_size(3)
    assert bits == [0, 0, 1, 1]

def test_frag_size_roundtrip():
    """All sizes 0-15 round-trip through encode/decode."""
    for n in range(16):
        w = BitWriter()
        for b in encode_frag_size(n):
            w.write_bit(b)
        r = BitReader(w.to_bytes())
        decoded = decode_frag_size(r)
        assert decoded == n, f"Size {n}: decoded as {decoded}"


# ============================================================
# Header structure
# ============================================================

def test_header_nat_only():
    """A simple nat seed has correct header: n_bytes=1, rest=0."""
    data = save_seed(42)
    assert len(data) >= 40
    n_holes, n_bigs, n_words, n_bytes, n_frags = struct.unpack_from('<QQQQQ', data, 0)
    assert n_holes == 0
    assert n_bigs == 0
    assert n_words == 0
    assert n_bytes == 1    # 42 fits in a byte
    assert n_frags == 0    # pure nat: atom IS the root, no fragments needed

def test_header_word():
    """A word-sized nat (>255) goes in n_words."""
    data = save_seed(256)
    n_holes, n_bigs, n_words, n_bytes, n_frags = struct.unpack_from('<QQQQQ', data, 0)
    assert n_words == 1
    assert n_bytes == 0

def test_header_bignat():
    """A bignat (>2^64) goes in n_bigs."""
    big = (1 << 64) + 1
    data = save_seed(big)
    n_holes, n_bigs, n_words, n_bytes, n_frags = struct.unpack_from('<QQQQQ', data, 0)
    assert n_bigs == 1

def test_header_aligned():
    """Seed is padded to 8-byte boundary."""
    for val in [0, 1, 42, 255, 256, 1000]:
        data = save_seed(val)
        assert len(data) % 8 == 0, f"seed({val}) not 8-byte aligned: {len(data)} bytes"


# ============================================================
# Nat round-trips
# ============================================================

def test_roundtrip_zero():
    assert load_seed(save_seed(0)) == 0

def test_roundtrip_byte():
    for v in [0, 1, 127, 128, 255]:
        result = load_seed(save_seed(v))
        assert result == v, f"Failed for {v}: got {result}"

def test_roundtrip_word():
    for v in [256, 1000, 65535, 65536, (1 << 32), (1 << 63), (1 << 64) - 1]:
        result = load_seed(save_seed(v))
        assert result == v, f"Failed for {v}: got {result}"

def test_roundtrip_bignat():
    for v in [(1 << 64), (1 << 64) + 1, (1 << 128), (1 << 256) - 1]:
        result = load_seed(save_seed(v))
        assert result == v, f"Failed for bignat: got {result}"


# ============================================================
# App round-trips
# ============================================================

def test_roundtrip_simple_app():
    """(1 2) round-trips."""
    val = A(1, 2)
    result = load_seed(save_seed(val))
    assert is_app(result)
    assert result.fun == 1
    assert result.arg == 2

def test_roundtrip_nested_app():
    """((1 2) 3) round-trips."""
    val = A(A(1, 2), 3)
    result = load_seed(save_seed(val))
    assert is_app(result)
    assert is_app(result.fun)
    assert result.fun.fun == 1
    assert result.fun.arg == 2
    assert result.arg == 3

def test_roundtrip_shared_subterm():
    """Shared subterms are preserved (DAG sharing)."""
    inner = A(1, 2)
    # inner appears twice in the outer value
    val = A(inner, inner)
    data = save_seed(val)
    # Should have n_frags=2: one for inner, one for outer
    n_holes, n_bigs, n_words, n_bytes, n_frags = struct.unpack_from('<QQQQQ', data, 0)
    # inner is shared so should be emitted once
    result = load_seed(data)
    assert is_app(result)
    assert is_app(result.fun)
    assert is_app(result.arg)


# ============================================================
# Law round-trips
# ============================================================

def test_roundtrip_law():
    """Identity law round-trips."""
    l = L(1, 1, 1)
    result = load_seed(save_seed(l))
    # After loading, the template (1 arity name body) is evaluated
    # to produce a law
    assert is_law(result), f"Expected law, got {result}"

def test_roundtrip_law_preserves_arity():
    """Law arity is preserved through seed round-trip."""
    for ar in [1, 2, 3, 5]:
        l = L(ar, ar, 1)
        result = load_seed(save_seed(l))
        assert is_law(result), f"arity={ar}: not a law: {result}"
        assert result.arity == ar, f"arity={ar}: got {result.arity}"


# ============================================================
# Run as script
# ============================================================

if __name__ == '__main__':
    tests = [(name, obj) for name, obj in globals().items()
             if name.startswith('test_') and callable(obj)]
    passed = failed = 0
    for name, fn in sorted(tests):
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except (AssertionError, Exception) as e:
            print(f"  FAIL  {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
