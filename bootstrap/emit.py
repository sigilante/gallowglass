"""
Gallowglass bootstrap emitter.

Serializes compiled PLAN values to the seed format.

Public API:
    emit(compiled, entry) -> bytes
        compiled: dict[str, Any]  (FQ name -> PLAN value)
        entry:    str              (FQ name of the entry point)
        returns:  seed bytes for the entry-point value

    emit_all(compiled) -> bytes
        Emit all top-level values as a single pinned record.
"""

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
