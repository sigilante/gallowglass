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

Two output formats share the same walking logic via a :class:`Formatter`
abstraction:

* :func:`render_typed` — plain text. Used for ``text/plain`` Jupyter
  output, terminal REPL output, and any caller that needs a raw string.
* :func:`render_typed_html` — inline-styled HTML. Used for ``text/html``
  Jupyter output, where colour and structure improve readability of
  compound values, types, and pin hashes.

Limitations:

* Function types render as ``<λ : T>`` rather than literal source.
* Tuples are not yet handled (the kernel doesn't yet exercise them).
* Effect rows in the ``TComp`` head are stripped — we render the
  underlying value type.
* Recursion bounded by ``MAX_DEPTH`` so a malformed value tree can't
  blow the host stack while we're trying to *show* an error.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any, Callable

from bootstrap.typecheck import (
    TMeta, TCon, TArr, TApp, TTup, TBound, TComp, Scheme,
    pp_type,
)
from dev.harness.plan import is_nat, is_pin, is_law, is_app


MAX_DEPTH = 32


# ---------------------------------------------------------------------------
# Rendered fragment — text + parenthesisation hint
# ---------------------------------------------------------------------------

@dataclass
class Rendered:
    """A rendered fragment plus whether it's composite.

    ``composite`` is True iff the rendering is "head field1 field2…"
    shape — the parent then knows to wrap it in parens when it
    appears as a constructor argument. Atoms (nats, bools, bare
    constructor names, function labels, structural fallbacks)
    aren't composite and never need parens.
    """
    text: str
    composite: bool = False


# ---------------------------------------------------------------------------
# Formatters: text vs HTML
# ---------------------------------------------------------------------------

class Formatter:
    """How to wrap each kind of token. Both formatters return strings;
    the HTML formatter wraps with inline-styled spans."""

    # Atoms — return non-composite Rendered.
    def nat(self, n: int) -> Rendered:
        return Rendered(str(n))

    def bool_(self, b: bool) -> Rendered:
        return Rendered('True' if b else 'False')

    def text(self, s: str) -> Rendered:
        # No escape handling beyond HTML safety (in the HTML
        # subclass). Embedded quote/newline chars in the source
        # text render verbatim — matches the prelude's `show_text`.
        return Rendered(f'"{s}"')

    def ctor_name(self, name: str) -> Rendered:
        return Rendered(name)

    def lambda_(self, name: str, type_str: str) -> Rendered:
        if name:
            return Rendered(f'<λ {name} : {type_str}>')
        return Rendered(f'<λ : {type_str}>')

    # Composite — wraps a constructor application or fallback App.
    def ctor_application(self, head: str, fields: list[Rendered]) -> Rendered:
        if not fields:
            return Rendered(head)
        parts = [head]
        for f in fields:
            parts.append(self._paren_if(f))
        return Rendered(' '.join(parts), composite=True)

    def _paren_if(self, r: Rendered) -> str:
        if r.composite:
            return '(' + r.text + ')'
        return r.text

    # Structural fallback — used when type info doesn't help.
    def pin_fallback(self, inner: Rendered) -> Rendered:
        return Rendered(f'<pin {inner.text}>')

    def law_fallback(self, name: str, arity: int) -> Rendered:
        if name:
            return Rendered(f"<law arity={arity} name='{name}'>")
        return Rendered(f'<law arity={arity}>')

    def app_fallback(self, fn: Rendered, arg: Rendered) -> Rendered:
        return Rendered(f'({fn.text} {arg.text})')

    def truncated(self) -> Rendered:
        return Rendered('...')

    # Top-level wrap: text formatter just returns the string;
    # HTML wraps the whole thing in a `<code>` tag.
    def wrap_top(self, body: str) -> str:
        return body


class HtmlFormatter(Formatter):
    """Inline-styled HTML renderer. Uses ``style="…"`` attributes
    rather than a ``<style>`` block because Jupyter's display
    sandbox strips ``<style>`` from kernel output."""

    # Cross-theme-friendly palette (works on both light and dark
    # backgrounds without aggressively dark colours).
    _NAT     = 'color:#0097a7'                                # cyan
    _BOOL    = 'color:#7b1fa2'                                # purple
    _TEXT    = 'color:#388e3c'                                # green
    _CTOR    = 'color:#1976d2;font-weight:600'                # blue, bold
    _TYPE    = 'color:#666;font-style:italic'                 # muted italic
    _LAW     = 'color:#e65100;font-style:italic'              # orange italic
    _MUTED   = 'color:#999'                                   # gray

    def nat(self, n: int) -> Rendered:
        return Rendered(f'<span style="{self._NAT}">{n}</span>')

    def bool_(self, b: bool) -> Rendered:
        s = 'True' if b else 'False'
        return Rendered(f'<span style="{self._BOOL}">{s}</span>')

    def text(self, s: str) -> Rendered:
        escaped = html.escape(s, quote=False)
        return Rendered(
            f'<span style="{self._TEXT}">"{escaped}"</span>'
        )

    def ctor_name(self, name: str) -> Rendered:
        escaped = html.escape(name, quote=False)
        return Rendered(f'<span style="{self._CTOR}">{escaped}</span>')

    def lambda_(self, name: str, type_str: str) -> Rendered:
        type_html = html.escape(type_str, quote=False)
        if name:
            name_html = html.escape(name, quote=False)
            inner = (f'<span style="{self._LAW}">λ </span>'
                     f'<span style="{self._CTOR}">{name_html}</span>'
                     f'<span style="{self._MUTED}"> : </span>'
                     f'<span style="{self._TYPE}">{type_html}</span>')
        else:
            inner = (f'<span style="{self._LAW}">λ</span>'
                     f'<span style="{self._MUTED}"> : </span>'
                     f'<span style="{self._TYPE}">{type_html}</span>')
        return Rendered(f'<span style="{self._MUTED}">&lt;</span>'
                        f'{inner}'
                        f'<span style="{self._MUTED}">&gt;</span>')

    def ctor_application(self, head: str, fields: list[Rendered]) -> Rendered:
        # ``head`` here is already a span-wrapped name (produced by
        # ``ctor_name`` in the renderer).  Just glue it together.
        if not fields:
            return Rendered(head)
        parts = [head]
        for f in fields:
            parts.append(self._paren_if(f))
        return Rendered(' '.join(parts), composite=True)

    def _paren_if(self, r: Rendered) -> str:
        if r.composite:
            return (f'<span style="{self._MUTED}">(</span>'
                    f'{r.text}'
                    f'<span style="{self._MUTED}">)</span>')
        return r.text

    def pin_fallback(self, inner: Rendered) -> Rendered:
        return Rendered(
            f'<span style="{self._MUTED}">&lt;pin </span>'
            f'{inner.text}'
            f'<span style="{self._MUTED}">&gt;</span>'
        )

    def law_fallback(self, name: str, arity: int) -> Rendered:
        if name:
            name_html = html.escape(name, quote=False)
            inner = (f'<span style="{self._LAW}">law</span>'
                     f'<span style="{self._MUTED}"> arity={arity} name=</span>'
                     f"<span style=\"{self._TEXT}\">'{name_html}'</span>")
        else:
            inner = (f'<span style="{self._LAW}">law</span>'
                     f'<span style="{self._MUTED}"> arity={arity}</span>')
        return Rendered(f'<span style="{self._MUTED}">&lt;</span>'
                        f'{inner}'
                        f'<span style="{self._MUTED}">&gt;</span>')

    def app_fallback(self, fn: Rendered, arg: Rendered) -> Rendered:
        return Rendered(
            f'<span style="{self._MUTED}">(</span>'
            f'{fn.text} {arg.text}'
            f'<span style="{self._MUTED}">)</span>'
        )

    def truncated(self) -> Rendered:
        return Rendered(f'<span style="{self._MUTED}">…</span>')

    def wrap_top(self, body: str) -> str:
        # Monospace block. Margin tightening keeps cell output flush
        # with adjacent prose in JupyterLab.
        return (f'<code style="font-family:ui-monospace,'
                f'SFMono-Regular,Menlo,monospace;font-size:0.9em">'
                f'{body}</code>')


_TEXT_FORMATTER = Formatter()
_HTML_FORMATTER = HtmlFormatter()


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def render_typed(value: Any, ty: Any, *,
                 type_env: dict, con_info: dict,
                 depth: int = 0) -> str:
    """Render ``value`` as plain text using ``ty`` to drive ADT
    constructor-name lookup and field-type substitution.

    ``type_env`` is the typechecker's FQ-name → :class:`Scheme` map.
    ``con_info`` is the compiler's FQ-name → :class:`bootstrap.codegen.ConInfo`
    map.

    Returns a best-effort string. On any rendering hiccup, falls back
    to a structural form rather than raising.
    """
    return _render_with(value, ty, _TEXT_FORMATTER,
                        type_env=type_env, con_info=con_info, depth=depth)


def render_typed_html(value: Any, ty: Any, *,
                       type_env: dict, con_info: dict,
                       depth: int = 0) -> str:
    """Render ``value`` as inline-styled HTML.

    Output is a self-contained HTML fragment suitable for emitting
    as a Jupyter ``text/html`` MIME bundle. Wraps the whole rendering
    in a monospace ``<code>`` element; styling lives in inline
    ``style="…"`` attributes (Jupyter strips ``<style>`` blocks for
    sandboxing).
    """
    return _render_with(value, ty, _HTML_FORMATTER,
                        type_env=type_env, con_info=con_info, depth=depth)


def _render_with(value: Any, ty: Any, fmt: Formatter,
                 *, type_env: dict, con_info: dict, depth: int) -> str:
    rendered = _walk(value, ty, fmt, type_env=type_env,
                      con_info=con_info, depth=depth)
    return fmt.wrap_top(rendered.text)


# ---------------------------------------------------------------------------
# Walker — produces ``Rendered`` (text + composite flag)
# ---------------------------------------------------------------------------

def _walk(value: Any, ty: Any, fmt: Formatter,
          *, type_env: dict, con_info: dict, depth: int) -> Rendered:
    if depth > MAX_DEPTH:
        return fmt.truncated()

    ty = _strip_meta_and_comp(ty)

    # Atomic built-ins.
    if isinstance(ty, TCon):
        atom = _walk_atomic(value, ty.name, fmt)
        if atom is not None:
            return atom

    # Function types.
    if isinstance(ty, TArr):
        return fmt.lambda_(_value_name(value), pp_type(ty))

    # Tuples (TTup) — not handled yet; structural fallback.
    if isinstance(ty, TTup):
        return _walk_structural(value, fmt, depth)

    # ADT: TCon (nullary type) or TApp (parameterised type).
    head, _ = _unapply(ty)
    if isinstance(head, TCon):
        ctors = _ctors_for_type(head, con_info)
        if ctors:
            return _walk_adt(value, ty, ctors, fmt,
                             type_env=type_env, con_info=con_info,
                             depth=depth)

    return _walk_structural(value, fmt, depth)


# ---------------------------------------------------------------------------
# Atomic built-ins
# ---------------------------------------------------------------------------

def _walk_atomic(value: Any, type_name: str, fmt: Formatter):
    """Dispatch on ``Nat`` / ``Bool`` / ``Text``. Returns ``None`` if
    the type isn't one of the recognised atoms."""
    short = type_name.rsplit('.', 1)[-1]

    if short == 'Nat' and is_nat(value):
        return fmt.nat(int(value))

    if short == 'Bool' and is_nat(value):
        return fmt.bool_(int(value) != 0)

    if short == 'Text':
        decoded = _decode_text(value)
        if decoded is not None:
            return fmt.text(decoded)

    return None


def _decode_text(value: Any) -> str | None:
    """Decode a ``Text = A(byte_length, content_nat)`` to its Python
    str. Returns ``None`` if the value isn't in the expected pair
    shape."""
    if not (is_app(value) and is_nat(value.fun) and is_nat(value.arg)):
        return None
    byte_length = int(value.fun)
    content = int(value.arg)
    if byte_length == 0:
        return ''
    try:
        return content.to_bytes(byte_length, 'little').decode('utf-8')
    except (OverflowError, UnicodeDecodeError):
        return None


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
# ADT walking
# ---------------------------------------------------------------------------

def _ctors_for_type(head: TCon, con_info: dict) -> list:
    """Return the constructors for a type's head, sorted by tag.

    Match by short type name — ``ConInfo.type_name`` stores the
    short form (e.g. ``'Pair'``, ``'List'``) regardless of which
    module the type is declared in.
    """
    type_short = head.name.rsplit('.', 1)[-1]
    ctors = [info for info in con_info.values() if info.type_name == type_short]
    ctors.sort(key=lambda c: c.tag)
    return ctors


def _walk_adt(value: Any, outer_type: Any, ctors: list, fmt: Formatter,
              *, type_env: dict, con_info: dict, depth: int) -> Rendered:
    """Identify which constructor ``value`` represents, then render
    the constructor name with its field values."""
    # Unwrap a pinned scalar — nullary constructors live in the
    # compiler's globals as ``Pin(Nat)``. After ``bevaluate`` the
    # Pin survives, so we strip one layer here before checking for
    # the bare-Nat shape.
    if is_pin(value) and is_nat(value.val):
        value = value.val

    # Nullary case: value is a bare Nat (the tag).
    if is_nat(value):
        tag = int(value)
        ci = _find_ctor(ctors, tag, arity=0)
        if ci is not None:
            return fmt.ctor_name(_short_name(ci.fq_name))
        return _walk_structural(value, fmt, depth)

    # n-ary case: value is an App spine ending in the tag Nat.
    if is_app(value):
        spine_args, head = _unspine(value)
        # Same Pin-unwrap discipline at the spine head.
        if is_pin(head) and is_nat(head.val):
            head = head.val
        if is_nat(head):
            tag = int(head)
            ci = _find_ctor(ctors, tag, arity=len(spine_args))
            if ci is not None:
                return _walk_ctor_application(
                    ci, spine_args, outer_type, fmt,
                    type_env=type_env, con_info=con_info, depth=depth,
                )

    return _walk_structural(value, fmt, depth)


def _find_ctor(ctors: list, tag: int, *, arity: int):
    """Linear search for a constructor matching the observed tag and
    arity. A tag collision across different-arity constructors falls
    through to the structural fallback rather than silently rendering
    wrong."""
    for c in ctors:
        if c.tag == tag and c.arity == arity:
            return c
    return None


def _walk_ctor_application(ci, field_values: list, outer_type: Any,
                            fmt: Formatter,
                            *, type_env: dict, con_info: dict,
                            depth: int) -> Rendered:
    """Render ``Ctor field1 field2 …`` with field types substituted
    from the outer type's instantiation."""
    field_types = _instantiate_field_types(ci.fq_name, outer_type, type_env)
    if field_types is None or len(field_types) != len(field_values):
        rendered_fields = [_walk_structural(v, fmt, depth + 1)
                           for v in field_values]
    else:
        rendered_fields = []
        for fv, fty in zip(field_values, field_types):
            rendered_fields.append(_walk(fv, fty, fmt,
                                         type_env=type_env, con_info=con_info,
                                         depth=depth + 1))

    head_name = fmt.ctor_name(_short_name(ci.fq_name)).text
    return fmt.ctor_application(head_name, rendered_fields)


# ---------------------------------------------------------------------------
# Type-substitution machinery
# ---------------------------------------------------------------------------

def _instantiate_field_types(con_fq: str, outer_type: Any,
                              type_env: dict) -> list | None:
    """Look up the constructor's scheme, match its return type
    against ``outer_type``, return the substituted field types.

    Example: ``MkPair : ∀ a b. a → b → Pair a b`` against
    ``Pair Nat Nat`` returns ``[TCon('Nat'), TCon('Nat')]``.

    Returns ``None`` when the scheme isn't in ``type_env`` or when
    the match fails.
    """
    scheme = type_env.get(con_fq)
    if scheme is None or not isinstance(scheme, Scheme):
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
    """Compare ``TCon`` names, ignoring module qualification."""
    return a.rsplit('.', 1)[-1] == b.rsplit('.', 1)[-1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_meta_and_comp(ty: Any) -> Any:
    while isinstance(ty, TMeta) and ty.ref is not None:
        ty = ty.ref
    if isinstance(ty, TComp):
        ty = ty.ty
        while isinstance(ty, TMeta) and ty.ref is not None:
            ty = ty.ref
    return ty


def _unapply(ty: Any) -> tuple:
    args: list = []
    while isinstance(ty, TApp):
        args.insert(0, ty.arg)
        ty = ty.fun
    return ty, args


def _unspine(value: Any) -> tuple:
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

def _walk_structural(value: Any, fmt: Formatter, depth: int) -> Rendered:
    """Render a value without type-driven help. Same shape as the
    pre-renderer fallback but emits via the formatter so HTML mode
    gets styled spans."""
    if depth > MAX_DEPTH:
        return fmt.truncated()
    if is_nat(value):
        return fmt.nat(int(value))
    if is_pin(value):
        inner = _walk_structural(value.val, fmt, depth + 1)
        return fmt.pin_fallback(inner)
    if is_law(value):
        return fmt.law_fallback(_decode_strnat(value.name), int(value.arity))
    if is_app(value):
        return fmt.app_fallback(
            _walk_structural(value.fun, fmt, depth + 1),
            _walk_structural(value.arg, fmt, depth + 1),
        )
    return Rendered(repr(value))


# ---------------------------------------------------------------------------
# Decl-summary HTML rendering
# ---------------------------------------------------------------------------

def render_decl_summary_html(summary_lines: list[str]) -> str:
    """Convert plain-text decl summaries (``name : Type``,
    ``type T``, ``use M``) into an inline-styled HTML block.

    The text-form summaries are produced by the kernel; we just
    style them here so the HTML and plain-text outputs stay in
    sync. Each line is parsed lightly: a leading ``let foo`` /
    ``type Foo`` / ``use Foo`` keyword is styled, the rest of the
    line gets type-style formatting on whatever follows the colon.
    """
    if not summary_lines:
        return ''

    rendered_lines: list[str] = []
    for line in summary_lines:
        rendered_lines.append(_render_decl_line_html(line))

    body = '<br>'.join(rendered_lines)
    return (f'<div style="font-family:ui-monospace,'
            f'SFMono-Regular,Menlo,monospace;font-size:0.9em">'
            f'{body}</div>')


def _render_decl_line_html(line: str) -> str:
    """Style one decl-summary line.

    Three shapes the kernel emits:

    * ``name : Type`` — let-decl summary.
    * ``type Foo = A | B`` — type-decl summary.
    * ``use Mod.Path`` — use-import summary.
    """
    _NAME = HtmlFormatter._CTOR
    _TYPE = HtmlFormatter._TYPE
    _KW   = HtmlFormatter._LAW
    _MUTED = HtmlFormatter._MUTED

    if line.startswith('type '):
        rest = html.escape(line[5:], quote=False)
        # Split at first '=' for stylised keyword + body.
        eq = rest.find('=')
        if eq >= 0:
            name_part = rest[:eq].rstrip()
            ctors_part = rest[eq + 1:].lstrip()
            return (f'<span style="{_KW}">type</span> '
                    f'<span style="{_NAME}">{name_part}</span>'
                    f'<span style="{_MUTED}"> = </span>'
                    f'<span style="{_TYPE}">{ctors_part}</span>')
        return (f'<span style="{_KW}">type</span> '
                f'<span style="{_NAME}">{rest}</span>')

    if line.startswith('use '):
        rest = html.escape(line[4:], quote=False)
        return (f'<span style="{_KW}">use</span> '
                f'<span style="{_TYPE}">{rest}</span>')

    # Default: ``name : Type`` shape.
    if ' : ' in line:
        name, _, ty = line.partition(' : ')
        return (f'<span style="{_NAME}">{html.escape(name, quote=False)}</span>'
                f'<span style="{_MUTED}"> : </span>'
                f'<span style="{_TYPE}">{html.escape(ty, quote=False)}</span>')

    # Bare name (no type recorded).
    return f'<span style="{_NAME}">{html.escape(line, quote=False)}</span>'
