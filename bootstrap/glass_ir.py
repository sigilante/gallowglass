"""
Gallowglass Glass IR renderer.

Glass IR is a view over the compiled PLAN output that preserves:
- Fully-qualified names
- Law structure (arity, name nat → decoded string)
- Pin content-addressing (hash stub — we use repr for now)
- Application spine

This module provides a human-readable and machine-parseable rendering
of the compiled output.  It does NOT implement the full Glass IR formal
grammar (spec/01-glass-ir.md); that belongs in the self-hosting compiler.
This is a bootstrap debugging aid.

Public API:
    render(compiled) -> str
        compiled: dict[str, Any]  (FQ name -> PLAN value)
        returns:  Glass IR text
"""

from __future__ import annotations
from dev.harness.plan import P, L, A, N, is_nat, is_pin, is_law, is_app


# ---------------------------------------------------------------------------
# Name decoding
# ---------------------------------------------------------------------------

def decode_name(n: int) -> str:
    """Decode a law name nat (little-endian UTF-8) back to a string."""
    if n == 0:
        return '<anon>'
    b = []
    while n > 0:
        b.append(n & 0xFF)
        n >>= 8
    try:
        return bytes(b).decode('utf-8')
    except UnicodeDecodeError:
        return f'<#{bytes(b).hex()}>'


# ---------------------------------------------------------------------------
# PLAN value rendering
# ---------------------------------------------------------------------------

def render_value(val: any, indent: int = 0, depth: int = 0, max_depth: int = 12) -> str:
    """Render a PLAN value as a Glass IR fragment."""
    if depth > max_depth:
        return '...'
    pad = '  ' * indent

    if is_nat(val):
        return str(val)

    if is_pin(val):
        inner = render_value(val.val, indent, depth + 1, max_depth)
        return f'<{inner}>'

    if is_law(val):
        name_str = decode_name(val.name) if is_nat(val.name) else repr(val.name)
        body_str = render_value(val.body, indent + 1, depth + 1, max_depth)
        return f'{{"{name_str}" {val.arity} {body_str}}}'

    if is_app(val):
        # Collect the full spine for readability
        parts = []
        cur = val
        while is_app(cur):
            parts.append(cur.arg)
            cur = cur.fun
        parts.append(cur)
        parts.reverse()
        rendered = [render_value(p, indent, depth + 1, max_depth) for p in parts]
        return f'({" ".join(rendered)})'

    return repr(val)


# ---------------------------------------------------------------------------
# Glass IR document renderer
# ---------------------------------------------------------------------------

def render(compiled: dict) -> str:
    """
    Render all compiled values as a Glass IR document.

    Format:
        -- Glass IR  (bootstrap renderer, not full spec/01-glass-ir.md grammar)
        pin Main.foo = <value>
        pin Main.bar = <value>
        ...
    """
    lines = [
        '-- Glass IR (bootstrap render)',
        '-- spec: spec/01-glass-ir.md',
        '',
    ]

    for fq_name in sorted(compiled.keys()):
        val = compiled[fq_name]
        rendered = render_value(val)
        lines.append(f'pin {fq_name} = {rendered}')

    return '\n'.join(lines) + '\n'


def render_entry(fq_name: str, val: any) -> str:
    """Render a single compiled entry."""
    rendered = render_value(val)
    return f'pin {fq_name} = {rendered}'
