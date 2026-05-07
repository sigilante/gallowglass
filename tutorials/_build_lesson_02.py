#!/usr/bin/env python3
"""
Build script for tutorials/02-typeclasses.ipynb. See
tutorials/_build_lesson_01.py for the pattern.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import nbformat
from nbformat import v4

from bootstrap.jupyter_kernel import GallowglassEvaluator


CELLS: list[tuple[str, str]] = [
    ('md', '''# Typeclasses

In Gallowglass, typeclasses describe a set of operations that can \
be implemented for different types. The compiler dispatches to the \
right implementation based on inferred types — same syntax in the \
caller, different code at runtime.

This notebook covers the built-in `Eq` (equality), `Ord` (ordering), \
and `Show` (rendering) classes, shows how to define instances for \
your own types, and explains the one mechanical wrinkle the \
bootstrap currently has around dispatch.

This lesson assumes you've worked through \
`01-hello-gallowglass.ipynb` — declarations, types, ADTs, pattern \
matching.'''),

    ('md', '## Built-in: `Eq`\n\n'
           'Bring `Eq` and the method `eq` into scope from `Core.Nat`:'),
    ('code', 'use Core.Nat unqualified { Eq, eq }'),

    ('md', 'To call a class method, define a **constrained wrapper**. '
           'In the bootstrap, class-method dispatch only fires when '
           'the call goes through a `let` whose type carries the '
           'constraint (`Eq a =>`); bare calls like `eq 5 5` surface '
           'as `unbound variable` codegen errors. The wrapper makes '
           'the dispatch fire:'),
    ('code', 'let same : ∀ a. Eq a => a → a → Bool = λ x y → eq x y'),
    ('code', 'same 5 5'),
    ('code', 'same 5 7'),

    ('md', '## Built-in: `Show`\n\n'
           'The kernel\'s type-driven renderer already gives nice '
           'output for `42` or `True` without going through `Show`. '
           'But if you want to call `show` explicitly — say, to '
           'embed a rendered value in a string — define a wrapper:'),
    ('code', 'use Core.Text { Show, show }'),
    ('code', 'let display : ∀ a. Show a => a → Text = λ x → show x'),
    ('code', 'display 42'),

    ('md', '`Show` instances exist in the prelude for `Nat`, `Bool`, '
           '`Text`, `Pair`, `Option`, `List`, and `Result`. The '
           'instances for compound types depend on a `Show` instance '
           'for their element type — that\'s the `Show a => Show '
           'List` shape.'),

    ('md', '## Custom instances\n\n'
           'Define your own type and give it an `Eq` instance. The '
           'instance body provides each method:'),
    ('code', 'type Coin = | Heads | Tails'),
    ('code', 'instance Eq Coin {\n'
            '  eq = λ x y → match x {\n'
            '    | Heads → match y { | Heads → True  | Tails → False }\n'
            '    | Tails → match y { | Heads → False | Tails → True  }\n'
            '  }\n'
            '}'),

    ('md', 'Now `same` works on `Coin` — the typechecker selects the '
           'instance we just defined:'),
    ('code', 'same Heads Heads'),
    ('code', 'same Heads Tails'),

    ('md', '## Built-in: `Ord`\n\n'
           '`Ord` extends `Eq` (it has `Eq` as a superclass). Bring '
           'it into scope and define a `less` wrapper:'),
    ('code', 'use Core.Nat unqualified { Ord, lt }'),
    ('code', 'let less : ∀ a. Ord a => a → a → Bool = λ x y → lt x y'),
    ('code', 'less 3 5'),
    ('code', 'less 5 3'),

    ('md', '## What\'s next\n\n'
           'The next notebook is about Glass IR — the typed '
           'intermediate representation the compiler emits, where '
           'every binding gets a BLAKE3-256 pin hash that '
           'content-addresses it.'),
]


def _render_outputs(text: str | None, html: str | None,
                    execution_count: int) -> list[Any]:
    if text is None and html is None:
        return []
    data: dict[str, Any] = {}
    if text is not None:
        data['text/plain'] = text
    if html is not None:
        data['text/html'] = html
    return [v4.new_output('execute_result', data=data,
                          execution_count=execution_count, metadata={})]


def main() -> None:
    nb = v4.new_notebook()
    nb.metadata['kernelspec'] = {
        'name': 'gallowglass',
        'display_name': 'Gallowglass',
        'language': 'gallowglass',
    }
    nb.metadata['language_info'] = {
        'name': 'gallowglass',
        'mimetype': 'text/x-gallowglass',
        'file_extension': '.gls',
        'pygments_lexer': 'haskell',
    }

    evaluator = GallowglassEvaluator()
    exec_count = 0

    for kind, body in CELLS:
        if kind == 'md':
            nb.cells.append(v4.new_markdown_cell(body, id=f"md-{len(nb.cells):02d}"))
            continue
        exec_count += 1
        result = evaluator.eval_cell(body)
        if result.error is not None:
            print(f'WARN: cell {exec_count} errored: {result.error}',
                  file=sys.stderr)
        outputs = _render_outputs(result.value_text, result.value_html,
                                  execution_count=exec_count)
        cell = v4.new_code_cell(source=body, outputs=outputs, id=f"code-{exec_count:02d}")
        cell['execution_count'] = exec_count
        nb.cells.append(cell)

    out_path = os.path.join(os.path.dirname(__file__),
                            '02-typeclasses.ipynb')
    with open(out_path, 'w') as f:
        nbformat.write(nb, f)
    print(f'wrote {out_path}')


if __name__ == '__main__':
    main()
