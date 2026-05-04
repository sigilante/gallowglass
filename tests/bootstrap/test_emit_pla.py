#!/usr/bin/env python3
"""
emit_pla bind-symbol dedup: cross-binding references emit as bare symbols
instead of inlining the referenced PLAN tree.

Without this, a body that calls a top-level function inlines that
function's entire law body — and transitively all of its dependencies —
producing exponential text blowup on real modules. With dedup, each
binding's transitive closure is referenced by name.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program
from bootstrap.emit_pla import emit_program


def _emit(src: str, module: str = 'Demo') -> str:
    prog = parse(lex(src, '<test>'), '<test>')
    resolved, _ = resolve(prog, module, {}, '<test>')
    compiled = compile_program(resolved, module)
    return emit_program(compiled)


def test_cross_binding_reference_uses_symbol():
    """A function whose body calls another top-level function emits the
    bare symbol, not the inlined law."""
    src = '''
let helper : Nat -> Nat
  = λ x → x

let user : Nat -> Nat
  = λ n → helper n
'''
    out = _emit(src)
    # `user`'s body should reference `Demo_helper` by name.
    assert 'Demo_helper' in out, f'expected Demo_helper in output:\n{out}'
    # `helper`'s law body appears once at its own bind site, not inlined
    # into `user`. Count the law-body opener — `(#law "125780003415400"`
    # is helper's name nat — and require it to appear at most once.
    assert out.count('"125780003415400"') == 1, (
        f'helper law inlined into user instead of referenced by symbol:\n{out}'
    )


def test_self_reference_does_not_collapse():
    """The `(#bind sym body)` line for a binding does not collapse its own
    body to its own symbol — it would emit `(#bind helper helper)` and
    Reaver couldn't resolve it. The skip_id mechanism prevents this."""
    src = '''
let helper : Nat -> Nat
  = λ x → x
'''
    out = _emit(src)
    # The bind line should emit the law structurally, not as
    # `(#bind Demo_helper Demo_helper)`.
    assert '(#bind Demo_helper Demo_helper)' not in out, (
        f'binding self-collapsed:\n{out}'
    )
    assert '(#bind Demo_helper (#law' in out, (
        f'expected structural law emission:\n{out}'
    )


def test_constructor_tags_not_deduped():
    """Nullary constructors are bare Nats whose Python identity is
    interned with every other `N(k)` (slot refs, literal numerals,
    quoted constants).  The dedup must skip Nat-typed top-level values
    or every `_1` slot ref collapses into `Module_GreenConstructor`."""
    src = '''
type Color = | Red | Green | Blue

let pick : Nat -> Color
  = λ n → match n {
      | 0 → Red
      | 1 → Green
      | _ → Blue
    }

let main : Nat
  = match (pick 1) {
      | Red   → 1000
      | Green → 2000
      | Blue  → 3000
    }
'''
    out = _emit(src)
    # Slot references inside law bodies must remain `_1`, not collapse
    # to `Demo_Green` (which has nat value 1 — the same Python int that
    # `_1` is rendered from).
    assert '_1' in out, f'slot refs lost — Nat dedup misfired:\n{out[:600]}'
    # Constructor tag literals (1000, 2000, 3000) must remain literals,
    # not be replaced by symbol names.
    assert '1000' in out and '2000' in out and '3000' in out, (
        f'constructor body literals lost:\n{out[:600]}'
    )


def test_alias_first_wins_for_forward_safety():
    """When two top-level FQ names point to the same Law object, the
    earliest FQ in iteration order wins as the dedup symbol.  This
    matters for cross-binding references: the symbol must be one Reaver
    has already seen by the time the referencing bind is compiled."""
    # Build a compiled dict where two keys alias the same Law value, in
    # this exact order (insertion-ordered dict). Then verify any
    # downstream reference resolves to the *earlier* key's symbol.
    from dev.harness.plan import L, A, N
    shared_law = L(arity=1, name=N(99), body=N(1))  # λx → x
    other_law = L(arity=1, name=N(100), body=A(N(0), shared_law))  # body refs shared_law
    compiled = {
        'Demo.early_alias': shared_law,
        'Demo.late_alias': shared_law,    # same object as early_alias
        'Demo.user': other_law,
    }
    out = emit_program(compiled, prelude='')
    # `user`'s body references `shared_law`; first-wins picks
    # `Demo_early_alias`, not `Demo_late_alias`.
    assert 'Demo_early_alias' in out, f'expected early alias:\n{out}'
    # The user binding's body specifically should use the early symbol.
    user_line = [ln for ln in out.splitlines() if ln.startswith('(#bind Demo_user')][0]
    assert 'Demo_early_alias' in user_line, (
        f'user body did not use early alias:\n{user_line}'
    )


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
