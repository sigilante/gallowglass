"""
Pin manifest and PinId computation.

PinIds are content-addressed hashes (BLAKE3-256) of a PLAN value's seed
serialization. This module provides:

- compute_pin_id(plan_value) -> str: hash a PLAN value via save_seed + BLAKE3
- build_manifest(compiled, module) -> dict: map FQ names to PinId hex strings
- save_manifest(manifest, path): write manifest JSON
- load_manifest(path) -> dict: read manifest JSON

Requires the `blake3` pip package for spec-compliant hashing. Falls back to
SHA-256 with a warning if blake3 is unavailable (not spec-compliant).
"""

from __future__ import annotations

import json
import warnings
from typing import Any

from dev.harness.seed import save_seed

try:
    import blake3 as _blake3

    def _hash_bytes(data: bytes) -> str:
        return _blake3.blake3(data).hexdigest()

except ImportError:
    import hashlib
    warnings.warn(
        "blake3 package not available; falling back to SHA-256. "
        "Install blake3 for spec-compliant PinIds: pip install blake3",
        stacklevel=2,
    )

    def _hash_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()


def compute_pin_id(plan_value) -> str:
    """Compute the PinId (BLAKE3-256 hex digest) of a PLAN value.

    Serializes the value to seed format, then hashes the seed bytes.
    """
    seed_bytes = save_seed(plan_value)
    return _hash_bytes(seed_bytes)


def build_manifest(compiled: dict[str, Any], module: str) -> dict:
    """Build a pin manifest for a module's compiled output.

    Args:
        compiled: FQ name -> PLAN value dict (may contain entries from
                  multiple modules; only entries starting with `module.`
                  are included).
        module: Module name (e.g. 'Core.Nat').

    Returns:
        Dict with 'module' and 'pins' keys.
    """
    prefix = module + '.'
    pins = {}
    for fq_name, plan_value in sorted(compiled.items()):
        if fq_name.startswith(prefix):
            pins[fq_name] = compute_pin_id(plan_value)
    return {'module': module, 'pins': pins}


def save_manifest(manifest: dict, path: str) -> None:
    """Write a manifest to a JSON file."""
    with open(path, 'w') as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write('\n')


def load_manifest(path: str) -> dict:
    """Read a manifest from a JSON file."""
    with open(path) as f:
        return json.load(f)
