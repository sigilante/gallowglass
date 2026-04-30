"""
Plan Assembler text emitter.

Walks raw `dev/harness/plan` PLAN values (P/L/A/N) and emits the textual
Plan Assembler format that `vendor/reaver/src/hs/PlanAssembler.hs` parses.

This is the production output path for the gallowglass → Reaver pipeline.
The format is described in `spec/07-seed-format.md §13`; the canonical
spec is PlanAssembler.hs at the SHA pinned in vendor.lock.

Public API:
    emit_program(compiled, *, trailer=None) -> str
        Convert a `dict[fq_name -> PlanVal]` (the bootstrap/codegen output)
        to a Plan Assembler text document. One `(#bind sym expr)` per FQ
        name, in iteration order. Optional trailer is appended verbatim
        (used by tests to inject a `(Trace main 0)` driver after `@boot`).

    emit_top(v) -> str
        Emit a single top-level PLAN value. Used by the program emitter and
        directly by tests that want to round-trip a single value.

Naming convention:
    FQ names like `Module.Foo.bar` are sanitised to `Module_Foo_bar` since
    `.` is a runic infix character in Reaver's syntax. PlanAssembler.hs's
    `expand1 BIND` requires the bind-key to be a bare `N` nat (a symbol);
    quoted-string forms parse as `(1 nat)` atoms and fail `getNat`.
"""

from __future__ import annotations

from typing import Any

from dev.harness.plan import P, L, A, N, is_nat, is_pin, is_law, is_app


# ---------------------------------------------------------------------------
# Name sanitisation
# ---------------------------------------------------------------------------

def _fq_to_symbol(fq: str) -> str:
    """Sanitise a FQ name to a Reaver-legal bare symbol.

    Reaver symbols may contain alphanumerics, `_`, `-`, `#`, and bytes >127.
    `.` is a runic infix character — `Foo.bar` would be parsed as
    `(#juxt . Foo bar)`, not as a single symbol. Replace with `_`.
    """
    return fq.replace('.', '_')


# ---------------------------------------------------------------------------
# Top-level emission (outside law bodies)
# ---------------------------------------------------------------------------

def emit_top(v: Any) -> str:
    """Emit a single PLAN value at top level (not inside a law body)."""
    if is_nat(v):
        return str(int(v))
    if is_pin(v):
        # `(#pin 2)` is the canonical PLAN Elim opcode pin (6-arity dispatch).
        # Reaver's runtime hardcodes `arity (P _ _ _) = 1`, so the opcode-pin
        # form fires after only one arg. Translate to the BPLAN-named `Elim`
        # primitive — `boot.plan` binds it via the `bplan` macro and Reaver
        # dispatches via `op 66 ["Elim", ...]`. Programs must `@boot` to
        # bring Elim into scope.
        if is_nat(v.val) and v.val == 2:
            return 'Elim'
        return f'(#pin {emit_top(v.val)})'
    if is_law(v):
        return _emit_law(v)
    if is_app(v):
        return f'({emit_top(v.fun)} {emit_top(v.arg)})'
    raise TypeError(f'emit_top: unknown PLAN ctor {type(v).__name__}')


def _emit_law(law: L) -> str:
    """Emit `(#law "name_decimal" sig body)`."""
    name_dec = str(int(law.name))
    arity = int(law.arity)
    sig = '(' + ' '.join(f'_{i}' for i in range(arity + 1)) + ')'
    body = _emit_body(law.body, arity)
    return f'(#law "{name_dec}" {sig} {body})'


# ---------------------------------------------------------------------------
# Law-body context emission (de Bruijn slot refs active)
# ---------------------------------------------------------------------------

def _emit_body(v: Any, depth: int) -> str:
    """Emit a value inside a law body. `depth` is the current slot count
    (= arity + number of let-bindings emitted so far).

    Bare nats with `value <= depth` are de Bruijn slot references (`_value`).
    Bare nats with `value > depth` are constants — but per the always-quote-
    wrap discipline (PR #48), gallowglass codegen never emits these in body
    context; they should always arrive as the quote form `A(N(0), N(value))`.
    A bare-nat-with-large-value here is rendered as a decimal literal (which
    Reaver parses as `(1 nat)` atom-embed — i.e. the constant), with a
    comment-free fallback to keep the emitter total.
    """
    if is_nat(v):
        i = int(v)
        if i <= depth:
            return f'_{i}'
        # Defensive: emit as bare numeric literal (parses to atom-embed).
        return str(i)
    if is_pin(v):
        # Same Elim translation as in `emit_top`, applied in body context.
        # The bare `Elim` symbol resolves via global lookup at compile-time
        # in Reaver's `compileExpr`, becoming an embedded constant.
        if is_nat(v.val) and v.val == 2:
            return 'Elim'
        return f'(#pin {emit_top(v.val)})'
    if is_law(v):
        # Nested laws are top-level constructs; emit as such. (kal returns
        # them as-is during body evaluation, so the surrounding context is
        # responsible for whatever wrapping is appropriate.)
        return _emit_law(v)
    if is_app(v):
        f, x = v.fun, v.arg
        if is_app(f):
            inner = f.fun
            if is_nat(inner):
                if inner == 0:
                    # `(0 f x)` — body apply form: emit as `(f x)` after
                    # recursing into f's slot/quote substitutions.
                    return f'({_emit_body(f.arg, depth)} {_emit_body(x, depth)})'
                if inner == 1:
                    # `(1 rhs body)` — let-binding: rhs binds the next
                    # available slot; body is evaluated with depth+1.
                    d1 = depth + 1
                    rhs = _emit_body(f.arg, depth)
                    rest = _emit_body(x, d1)
                    return f'_{d1}({rhs})\n  {rest}'
        if is_nat(f) and f == 0:
            # `(0 x)` — quoted constant in body context.
            if is_nat(x):
                return str(int(x))
            return emit_top(x)
        # Fallback: a raw App that isn't in body-apply form. kal returns
        # this as-is at runtime; emit as a literal app spine.
        return f'({_emit_body(f, depth)} {_emit_body(x, depth)})'
    raise TypeError(f'_emit_body: unknown PLAN ctor {type(v).__name__}')


# ---------------------------------------------------------------------------
# Program emission
# ---------------------------------------------------------------------------

def emit_program(compiled: dict[str, Any], *,
                 prelude: str = '@boot',
                 trailer: str | None = None) -> str:
    """Emit a Plan Assembler text document for the given compiled output.

    `compiled` maps FQ names to PLAN values, as returned by
    `bootstrap.codegen.compile_program` and `bootstrap.build.build_modules`.

    `prelude` is prepended before the `(#bind ...)` lines. The default
    `@boot` brings in Reaver's standard primitives — including the BPLAN
    `Elim` named primitive that gallowglass-emitted pattern matches
    reference. Pass `prelude=''` to skip.

    `trailer` is appended verbatim after the last `(#bind ...)` line. Tests
    use it to inject `(Trace Module_main 0)` for end-to-end runs.
    """
    lines: list[str] = []
    if prelude:
        lines.append(prelude)
    for fq, val in compiled.items():
        sym = _fq_to_symbol(fq)
        lines.append(f'(#bind {sym} {emit_top(val)})')
    out = '\n'.join(lines) + '\n'
    if trailer is not None:
        if not trailer.startswith('\n'):
            out += '\n'
        out += trailer
        if not trailer.endswith('\n'):
            out += '\n'
    return out
