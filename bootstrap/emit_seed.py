"""
Gallowglass bootstrap emitter — LEGACY BINARY SEED FORMAT.

This is the xocore-era emitter that serializes compiled PLAN values
to the binary seed format (`spec/07-seed-format.md`).  It is **not**
the production output path.  The Reaver pipeline goes through
`bootstrap/emit_pla.py`, which produces Plan Assembler text.

This module is preserved for:
  - test infrastructure (`tests/bootstrap/test_pin_wrap.py`,
    `tests/prelude/test_*.py`, etc.) that round-trips compiled
    values through binary seeds for unit-level checks;
  - `bootstrap/build_prelude.py`, which writes pin manifests via
    `emit_pinned`;
  - the historical demo invocation in CLAUDE.md "Build and Test."

If you are emitting output that runs under Reaver, use
`bootstrap.emit_pla.emit_program` instead.  See AUDIT.md C1.

Public API:
    emit(compiled, entry) -> bytes
        compiled: dict[str, Any]  (FQ name -> PLAN value)
        entry:    str              (FQ name of the entry point)
        returns:  seed bytes for the entry-point value

    emit_all(compiled) -> bytes
        Emit all top-level values as a single pinned record.

    emit_pinned(compiled, module) -> bytes
        Emit per-binding pinned manifest (used by build_prelude.py).
"""

import os

from bootstrap.pin import build_manifest, save_manifest
from dev.harness.plan import P, A, N, is_pin
from dev.harness.seed import save_seed


def emit(compiled: dict, entry: str) -> bytes:
    """
    Serialize the PLAN value for `entry` to a seed file.

    Args:
        compiled: dict mapping FQ name -> PLAN value
        entry:    FQ name of the entry point

    Returns:
        bytes — seed file content
    """
    if entry not in compiled:
        raise KeyError(f'emit: entry point {entry!r} not found in compiled output')
    val = compiled[entry]
    return save_seed(val)


def emit_all(compiled: dict) -> bytes:
    """
    Serialize all compiled values as a left-associative pin spine.

    The seed represents  A(A(...A(v0, v1)..., vN-1), vN)  where
    the values are in lexicographic FQ-name order.

    This is useful for debugging; production code uses emit() with a
    specific entry point.
    """
    if not compiled:
        return save_seed(N(0))

    keys = sorted(compiled.keys())
    vals = [compiled[k] for k in keys]
    result = vals[0]
    for v in vals[1:]:
        result = A(result, v)
    return save_seed(result)


def emit_pinned(compiled: dict, module: str, out_dir: str) -> dict:
    """
    Emit each definition as a separate seed file plus a manifest.

    Args:
        compiled: dict mapping FQ name -> PLAN value
        module:   module name (e.g. 'Core.Nat')
        out_dir:  output directory for seed files and manifest

    Returns:
        The manifest dict (also saved to out_dir/manifest.json).
    """
    os.makedirs(out_dir, exist_ok=True)

    prefix = module + '.'
    for fq_name, val in sorted(compiled.items()):
        if fq_name.startswith(prefix):
            # Use FQ name as filename (dots replaced with underscores)
            safe_name = fq_name.replace('.', '_')
            seed_path = os.path.join(out_dir, f'{safe_name}.seed')
            with open(seed_path, 'wb') as f:
                f.write(save_seed(val))

    manifest = build_manifest(compiled, module)
    save_manifest(manifest, os.path.join(out_dir, 'manifest.json'))
    return manifest
