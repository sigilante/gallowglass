"""
Glass IR debug renderer — RAW PLAN VALUE DUMP.

This module is the developer-facing debug dump.  It walks compiled
PLAN values (Pin/Law/App/Nat) and produces a textual representation
useful for tracing what the codegen built — *not* a spec-conforming
Glass IR fragment.  CLAUDE.md gospel: `Show` is for users, `Debug`
is for developers, never conflate them.  This module is the `Debug`
side; `bootstrap/glass_ir.py` is the `Show` (spec-conforming) side.

Public API:
    decode_name(n) -> str
        Decode a law-name nat (little-endian UTF-8) back to a string.

    debug_dump_plan_value(val, ...) -> str
        Render a single PLAN value as a parenthesised text dump.
        (Was `render_value` in glass_ir.py prior to AUDIT.md C5.)

    debug_dump_all(compiled) -> str
        Dump every value in a compiled dict, one `pin <fq> = …` per line.
        (Was `render` prior to C5.)

    debug_dump_entry(fq_name, val) -> str
        Dump a single named entry.  (Was `render_entry` prior to C5.)

If you need the spec-conforming Glass IR fragment that round-trips
back to PLAN, use `bootstrap.glass_ir.render_fragment` /
`render_decl` / `render_module` instead.
"""

from __future__ import annotations

from dev.harness.plan import is_nat, is_pin, is_law, is_app


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


def debug_dump_plan_value(val, indent: int = 0, depth: int = 0, max_depth: int = 12) -> str:
    """Render a PLAN value as a parenthesised debug-text dump.

    Not a Glass IR fragment per spec/01-glass-ir.md — that lives in
    `bootstrap/glass_ir.py`.  This dumps raw constructor structure
    (Pin/Law/App/Nat) with a depth cap to keep output bounded.
    """
    if depth > max_depth:
        return '...'

    if is_nat(val):
        return str(val)

    if is_pin(val):
        inner = debug_dump_plan_value(val.val, indent, depth + 1, max_depth)
        return f'<{inner}>'

    if is_law(val):
        name_str = decode_name(val.name) if is_nat(val.name) else repr(val.name)
        body_str = debug_dump_plan_value(val.body, indent + 1, depth + 1, max_depth)
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
        rendered = [debug_dump_plan_value(p, indent, depth + 1, max_depth) for p in parts]
        return f'({" ".join(rendered)})'

    return repr(val)


def debug_dump_all(compiled: dict) -> str:
    """Dump every value in a compiled dict for visual inspection.

    Format:
        -- Glass IR debug dump (raw PLAN values)
        pin Main.foo = <value>
        pin Main.bar = <value>
        ...
    """
    lines = [
        '-- Glass IR debug dump (raw PLAN values)',
        '-- spec-conforming output: bootstrap/glass_ir.py',
        '',
    ]

    for fq_name in sorted(compiled.keys()):
        val = compiled[fq_name]
        rendered = debug_dump_plan_value(val)
        lines.append(f'pin {fq_name} = {rendered}')

    return '\n'.join(lines) + '\n'


def debug_dump_entry(fq_name: str, val) -> str:
    """Dump a single compiled entry."""
    rendered = debug_dump_plan_value(val)
    return f'pin {fq_name} = {rendered}'
