"""
Type-driven runtime-value renderer.

Renders a runtime PLAN value (``P``/``L``/``A``/``N``) alongside its
inferred type to produce user-facing text. Used by the Jupyter kernel
as a fallback when the user-level ``Show`` typeclass either has no
instance for the result type or fails to fully reduce (the bootstrap
codegen's nested-dictionary insertion gap on constrained instances).

The renderer is type-driven: it walks the runtime value alongside its
type, looks up constructor names in the compiler's ``con_info``
table, looks up constructor field-type schemes in the typechecker's
``type_env``, and synthesises field types via type substitution at
the meta-level. This bypasses the constrained-instance codegen
limitation entirely — we're not invoking ``show`` on the value, we're
deriving a structural rendering from compiler-side metadata.

Public entry: :func:`render_typed`.

Limitations:

* Function types render as ``<λ : T>`` rather than literal source.
* Tuples are not yet handled (the kernel doesn't yet exercise them).
* Effect rows in the ``TComp`` head are stripped — we render the
  underlying value type.
* Recursion bounded by ``MAX_DEPTH`` so a malformed value tree can't
  blow the host stack while we're trying to *show* an error.
"""

from __future__ import annotations

from typing import Any

from bootstrap.typecheck import (
    TMeta, TCon, TArr, TApp, TTup, TBound, TComp, Scheme,
    pp_type,
)
from dev.harness.plan import is_nat, is_pin, is_law, is_app


MAX_DEPTH = 32


def render_typed(value: Any, ty: Any, *,
                 type_env: dict, con_info: dict,
                 depth: int = 0) -> str:
    """Render ``value`` as text, using ``ty`` to drive ADT
    constructor-name lookup and field-type substitution.

    ``type_env`` is the typechecker's FQ-name → :class:`Scheme` map
    (returns the constructor's full polymorphic signature).
    ``con_info`` is the compiler's FQ-name → :class:`bootstrap.codegen.ConInfo`
    map (gives tag, arity, type-name, FQ-name).

    Returns a best-effort string. On any rendering hiccup, falls back
    to a structural form (``(f arg)`` style) rather than raising —
    the caller is asking us to *display* the value, not validate it.
    """
    if depth > MAX_DEPTH:
        return '...'

    ty = _strip_meta_and_comp(ty)

    # Atomic built-ins.
    if isinstance(ty, TCon):
        rendered = _render_atomic(value, ty.name)
        if rendered is not None:
            return rendered

    # Function types.
    if isinstance(ty, TArr):
        return _render_function(value, ty)

    # Tuples (TTup) — not handled yet; structural fallback.
    if isinstance(ty, TTup):
        return _render_structural(value, depth)

    # ADT: TCon (nullary type) or TApp (parameterised type).
    head, type_args = _unapply(ty)
    if isinstance(head, TCon):
        ctors = _ctors_for_type(head, con_info)
        if ctors:
            return _render_adt(value, ty, ctors, type_env, con_info, depth)

    # Type unknown / not yet handled — fall back to structural.
    return _render_structural(value, depth)


# ---------------------------------------------------------------------------
# Atomic built-ins
# ---------------------------------------------------------------------------

def _render_atomic(value: Any, type_name: str) -> str | None:
    """Render ``value`` for a nullary built-in ``TCon``. Returns
    ``None`` if the type isn't one of the recognised atoms."""
    short = type_name.rsplit('.', 1)[-1]

    if short == 'Nat' and is_nat(value):
        return str(int(value))

    if short == 'Bool' and is_nat(value):
        return 'True' if int(value) != 0 else 'False'

    if short == 'Text':
        return _render_text(value)

    return None


def _render_text(value: Any) -> str | None:
    """Decode a ``Text = A(byte_length, content_nat)`` to a quoted string.

    Returns ``None`` if the value isn't in the expected pair shape so
    the caller can fall through to structural rendering.
    """
    if not (is_app(value) and is_nat(value.fun) and is_nat(value.arg)):
        return None
    byte_length = int(value.fun)
    content = int(value.arg)
    if byte_length == 0:
        return '""'
    try:
        s = content.to_bytes(byte_length, 'little').decode('utf-8')
    except (OverflowError, UnicodeDecodeError):
        return None
    # No escape handling — embedded quotes/newlines render verbatim.
    # Matches the prelude's `show_text` so the two paths produce
    # equivalent output.
    return f'"{s}"'


def _render_function(value: Any, ty: TArr) -> str:
    """Render a function value. We don't have access to the source,
    so the best we can do is name + type — useful for "what did I
    just define" rather than "what does it compute"."""
    name = _value_name(value)
    type_str = pp_type(ty)
    if name:
        return f'<λ {name} : {type_str}>'
    return f'<λ : {type_str}>'


def _value_name(value: Any) -> str:
    """Best-effort: extract the name from a Law or Pin'd Law."""
    if is_pin(value):
        return _value_name(value.val)
    if is_law(value):
        return _decode_strnat(value.name)
    return ''


def _decode_strnat(n: Any) -> str:
    if not is_nat(n) or n == 0:
        return ''
    n_int = int(n)
    try:
        raw = n_int.to_bytes((n_int.bit_length() + 7) // 8, 'little')
        return raw.decode('utf-8')
    except (UnicodeDecodeError, OverflowError):
        return ''


# ---------------------------------------------------------------------------
# ADT rendering
# ---------------------------------------------------------------------------

def _ctors_for_type(head: TCon, con_info: dict) -> list:
    """Return the constructors for a type's head, sorted by tag.

    Match by short type name — :class:`ConInfo.type_name` stores the
    short form (e.g. ``'Pair'``, ``'List'``) regardless of which
    module the type is declared in.
    """
    type_short = head.name.rsplit('.', 1)[-1]
    ctors = [info for info in con_info.values() if info.type_name == type_short]
    ctors.sort(key=lambda c: c.tag)
    return ctors


def _render_adt(value: Any, outer_type: Any, ctors: list,
                type_env: dict, con_info: dict, depth: int) -> str:
    """Identify which constructor ``value`` represents, then render
    the constructor name with its field values."""
    # Unwrap a pinned scalar — nullary constructors live in the
    # compiler's globals as `Pin(Nat)` (the Pin discipline keeps
    # `True`/`False`/`Nil`/`None` from being misinterpreted in body
    # context). After ``bevaluate`` the Pin survives, so we strip
    # one layer here before checking for the bare-Nat shape.
    if is_pin(value) and is_nat(value.val):
        value = value.val

    # Nullary case: value is a bare Nat (the tag).
    if is_nat(value):
        tag = int(value)
        ci = _find_ctor(ctors, tag, arity=0)
        if ci is not None:
            return _short_name(ci.fq_name)
        return _render_structural(value, depth)

    # n-ary case: value is an App spine ending in the tag Nat.
    if is_app(value):
        spine_args, head = _unspine(value)
        # Same Pin-unwrap discipline at the spine head — the tag
        # may be Pin(Nat) when the constructor was hoisted out as
        # a global.
        if is_pin(head) and is_nat(head.val):
            head = head.val
        if is_nat(head):
            tag = int(head)
            ci = _find_ctor(ctors, tag, arity=len(spine_args))
            if ci is not None:
                return _render_ctor_application(
                    ci, spine_args, outer_type, type_env, con_info, depth,
                )

    return _render_structural(value, depth)


def _find_ctor(ctors: list, tag: int, *, arity: int):
    """Linear search for a constructor matching the observed tag and
    arity. Both must agree — a tag collision across different-arity
    constructors falls through to the structural fallback rather than
    silently rendering wrong."""
    for c in ctors:
        if c.tag == tag and c.arity == arity:
            return c
    return None


def _render_ctor_application(ci, field_values: list, outer_type: Any,
                              type_env: dict, con_info: dict,
                              depth: int) -> str:
    """Render ``Ctor field1 field2 …`` with field types substituted
    from the outer type's instantiation."""
    field_types = _instantiate_field_types(ci.fq_name, outer_type, type_env)
    if field_types is None or len(field_types) != len(field_values):
        # Substitution failed (e.g. the constructor's scheme isn't in
        # type_env, or the outer type doesn't match the scheme's
        # return). Render the constructor name with structural fields.
        rendered_fields = [_render_structural(v, depth + 1) for v in field_values]
    else:
        rendered_fields = []
        for fv, fty in zip(field_values, field_types):
            child = render_typed(fv, fty, type_env=type_env,
                                  con_info=con_info, depth=depth + 1)
            rendered_fields.append(child)

    return _format_application(_short_name(ci.fq_name), rendered_fields)


def _format_application(head: str, fields: list[str]) -> str:
    """Format ``head field1 field2 …`` with parens around any field
    that is itself an application (contains a space and isn't already
    parenthesised, quoted, or a structural-fallback form)."""
    if not fields:
        return head
    out = [head]
    for f in fields:
        out.append(_paren_if_needed(f))
    return ' '.join(out)


def _paren_if_needed(s: str) -> str:
    if not s:
        return s
    if ' ' not in s:
        return s
    if s[0] in '("<' or s[0].isdigit():
        # Already parenthesised, quoted, structural, or a leading
        # bare numeric literal (which can't contain a space, so
        # unreachable in practice — defensive).
        return s
    return f'({s})'


# ---------------------------------------------------------------------------
# Type-substitution machinery
# ---------------------------------------------------------------------------

def _instantiate_field_types(con_fq: str, outer_type: Any,
                              type_env: dict) -> list | None:
    """Look up the constructor's scheme, match its return type
    against ``outer_type``, return the substituted field types.

    Example: ``MkPair : ∀ a b. a → b → Pair a b`` against
    ``Pair Nat Nat`` returns ``[TCon('Nat'), TCon('Nat')]``.

    Returns ``None`` when the scheme isn't in ``type_env`` (e.g.
    primitive constructor without a recorded scheme) or when the
    match fails (shouldn't happen if typecheck succeeded, but
    handled defensively).
    """
    scheme = type_env.get(con_fq)
    if scheme is None:
        return None
    if not isinstance(scheme, Scheme):
        return None

    body = scheme.body
    fields = []
    while isinstance(body, TArr):
        fields.append(body.dom)
        body = body.cod
    return_ty = body

    subst: dict[str, Any] = {}
    if not _match_types(return_ty, outer_type, subst, set(scheme.vars)):
        return None
    return [_apply_subst(f, subst) for f in fields]


def _match_types(scheme_ty: Any, target_ty: Any,
                 subst: dict[str, Any], vars_set: set) -> bool:
    """Match ``scheme_ty`` (with ``TBound`` variables) against
    ``target_ty`` (concrete), populating ``subst``.

    Conservative — succeeds only on shapes the renderer explicitly
    handles. Refusing returns ``False``, which routes the caller to
    a structural fallback rather than producing a wrong rendering.
    """
    target_ty = _strip_meta_and_comp(target_ty)
    scheme_ty = _strip_meta_and_comp(scheme_ty)

    if isinstance(scheme_ty, TBound) and scheme_ty.name in vars_set:
        existing = subst.get(scheme_ty.name)
        if existing is None:
            subst[scheme_ty.name] = target_ty
            return True
        return _types_equal(existing, target_ty)

    if isinstance(scheme_ty, TCon) and isinstance(target_ty, TCon):
        return _tcon_names_match(scheme_ty.name, target_ty.name)

    if isinstance(scheme_ty, TApp) and isinstance(target_ty, TApp):
        return (_match_types(scheme_ty.fun, target_ty.fun, subst, vars_set)
                and _match_types(scheme_ty.arg, target_ty.arg, subst, vars_set))

    if isinstance(scheme_ty, TArr) and isinstance(target_ty, TArr):
        return (_match_types(scheme_ty.dom, target_ty.dom, subst, vars_set)
                and _match_types(scheme_ty.cod, target_ty.cod, subst, vars_set))

    return False


def _apply_subst(ty: Any, subst: dict[str, Any]) -> Any:
    ty = _strip_meta_and_comp(ty)
    if isinstance(ty, TBound):
        return subst.get(ty.name, ty)
    if isinstance(ty, TApp):
        return TApp(_apply_subst(ty.fun, subst), _apply_subst(ty.arg, subst))
    if isinstance(ty, TArr):
        return TArr(_apply_subst(ty.dom, subst), _apply_subst(ty.cod, subst))
    return ty


def _types_equal(a: Any, b: Any) -> bool:
    a = _strip_meta_and_comp(a)
    b = _strip_meta_and_comp(b)
    if isinstance(a, TCon) and isinstance(b, TCon):
        return _tcon_names_match(a.name, b.name)
    if isinstance(a, TApp) and isinstance(b, TApp):
        return _types_equal(a.fun, b.fun) and _types_equal(a.arg, b.arg)
    if isinstance(a, TArr) and isinstance(b, TArr):
        return _types_equal(a.dom, b.dom) and _types_equal(a.cod, b.cod)
    if isinstance(a, TBound) and isinstance(b, TBound):
        return a.name == b.name
    return False


def _tcon_names_match(a: str, b: str) -> bool:
    """Compare ``TCon`` names, ignoring module qualification.

    The typechecker may carry FQ names in some places and short names
    in others; this normalises both sides to the trailing component
    so ``Core.Pair.Pair`` and ``Pair`` match.
    """
    return a.rsplit('.', 1)[-1] == b.rsplit('.', 1)[-1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_meta_and_comp(ty: Any) -> Any:
    """Resolve ``TMeta`` chains and unwrap ``TComp`` envelopes so the
    main render switch sees the underlying value type."""
    while isinstance(ty, TMeta) and ty.ref is not None:
        ty = ty.ref
    if isinstance(ty, TComp):
        ty = ty.ty
        while isinstance(ty, TMeta) and ty.ref is not None:
            ty = ty.ref
    return ty


def _unapply(ty: Any) -> tuple:
    """``TApp(TApp(TCon 'Pair', a), b)`` → ``(TCon 'Pair', [a, b])``."""
    args: list = []
    while isinstance(ty, TApp):
        args.insert(0, ty.arg)
        ty = ty.fun
    return ty, args


def _unspine(value: Any) -> tuple:
    """``App(App(N(tag), x), y)`` → ``([x, y], N(tag))``."""
    args: list = [value.arg]
    node = value.fun
    while is_app(node):
        args.insert(0, node.arg)
        node = node.fun
    return args, node


def _short_name(fq: str) -> str:
    return fq.rsplit('.', 1)[-1]


# ---------------------------------------------------------------------------
# Structural fallback (no type info)
# ---------------------------------------------------------------------------

def _render_structural(value: Any, depth: int) -> str:
    """Render a value without type-driven help.

    Matches the previous kernel ``_render`` shape so the fallback
    behaviour from before this module is unchanged.
    """
    if depth > MAX_DEPTH:
        return '...'
    if is_nat(value):
        return str(int(value))
    if is_pin(value):
        return f'<pin {_render_structural(value.val, depth + 1)}>'
    if is_law(value):
        name = _decode_strnat(value.name)
        if name:
            return f"<law arity={value.arity} name='{name}'>"
        return f'<law arity={value.arity}>'
    if is_app(value):
        return (f'({_render_structural(value.fun, depth + 1)} '
                f'{_render_structural(value.arg, depth + 1)})')
    return repr(value)
