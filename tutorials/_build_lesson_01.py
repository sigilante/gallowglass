#!/usr/bin/env python3
"""
Build script for tutorials/01-hello-gallowglass.ipynb.

Notebooks are JSON; rather than hand-editing them (which Jupyter
clients then reformat in subtly different ways on save), we
synthesise the cell list here and write it through ``nbformat`` so
the on-disk JSON stays canonical.

Outputs are populated by running each cell through the kernel's
``GallowglassEvaluator`` and recording the cell-result text + html.
This keeps the committed ``.ipynb`` in sync with the kernel — when
the renderer changes, re-running this script regenerates the
notebook.

Usage:
    python3 tutorials/_build_lesson_01.py
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


# ---------------------------------------------------------------------------
# Lesson 1 cells
# ---------------------------------------------------------------------------
# Each entry is either ('md', text) for markdown or ('code', src) for
# a code cell. Code cells are run through the evaluator below to
# capture their outputs.

CELLS: list[tuple[str, str]] = [
    ('md', '''# Hello Gallowglass

Gallowglass is a typed functional language compiling to PLAN. \
This notebook walks through the core: declarations, types, \
algebraic types, and pattern matching. Cells run in this kernel \
sequentially, so later cells see earlier definitions.

You'll need the Gallowglass kernel installed:

```bash
python3 -m bootstrap.jupyter_kernel install
```

Each code cell below is one input to the kernel. Declaration cells \
echo a one-line summary (`name : Type`); expression cells echo the \
result.'''),

    ('md', '## Functions and types\n\n'
           'Define a function with `let`. Every binding has a type — '
           'the compiler infers types when you don\'t spell them out.'),
    ('code', 'let twice : Nat → Nat = λ n → n + n'),

    ('md', 'Apply the function. The cell\'s value is the displayed '
           'result; the renderer picks colours based on the type '
           '(numbers in cyan, constructors in bold blue, strings in '
           'green).'),
    ('code', 'twice 21'),

    ('md', 'When you omit the type annotation, the compiler infers '
           'one and the cell summary shows what it picked:'),
    ('code', 'let triple = λ n → n * 3'),
    ('code', 'triple 7'),

    ('md', '## Algebraic types\n\n'
           'Define your own data shapes with `type`. Each '
           'constructor is a value (or a constructor function, if '
           'it takes arguments).'),
    ('code', 'type Coin =\n  | Heads\n  | Tails'),
    ('code', 'Heads'),

    ('md', 'Bring a type from the prelude into scope with `use … '
           'unqualified { … }`. `Pair a b` is the standard product '
           'type — its single constructor is `MkPair`.'),
    ('code', 'use Core.Pair unqualified { Pair, MkPair }'),
    ('code', 'let pair_val : Pair Nat Nat = MkPair 3 7'),
    ('code', 'pair_val'),

    ('md', '## Pattern matching\n\n'
           '`match` dispatches on a constructor. Each arm names the '
           'constructor and binds its fields:'),
    ('code', 'let coin_value : Coin → Nat\n'
            '  = λ c → match c {\n'
            '      | Heads → 1\n'
            '      | Tails → 0\n'
            '    }'),
    ('code', 'coin_value Heads'),
    ('code', 'coin_value Tails'),

    ('md', 'Pattern arms can bind fields. Note: single-letter '
           'lowercase identifiers are reserved as type variables, '
           'so we use `aa` and `bb` here rather than `a` and `b`:'),
    ('code', 'let sum_pair : Pair Nat Nat → Nat\n'
            '  = λ pp → match pp { | MkPair aa bb → aa + bb }'),
    ('code', 'sum_pair pair_val'),

    ('md', '## What\'s next\n\n'
           'You\'ve seen the core: declarations, types, ADTs, and '
           'pattern matching. The follow-up notebooks cover '
           'effects, typeclasses, and the Glass IR (the typed '
           'intermediate representation the compiler emits, which '
           'is content-addressed by BLAKE3 hash).\n\n'
           'For the language reference, see `doc/phrasebook.md` — a '
           'dense list of canonical Gallowglass patterns suitable '
           'for inclusion in an LLM\'s context.'),
]


def _render_outputs(text: str | None, html: str | None,
                    execution_count: int) -> list[Any]:
    """Build a Jupyter ``execute_result`` outputs list.

    Empty / decls-only cells with no display content get ``[]``.
    Otherwise we emit a single ``execute_result`` carrying both
    MIME types so notebook renderers see the colourised form and
    JSON-only viewers fall back to plain.

    Uses ``v4.new_output`` so the returned objects are nbformat
    NotebookNode instances (with attribute access for
    ``output_type`` etc.) — raw dicts trip ``split_lines`` during
    serialisation.
    """
    if text is None and html is None:
        return []
    data: dict[str, Any] = {}
    if text is not None:
        data['text/plain'] = text
    if html is not None:
        data['text/html'] = html
    return [v4.new_output(
        'execute_result',
        data=data,
        execution_count=execution_count,
        metadata={},
    )]


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
        # kind == 'code'
        exec_count += 1
        result = evaluator.eval_cell(body)
        outputs = _render_outputs(result.value_text, result.value_html,
                                  execution_count=exec_count)
        cell = v4.new_code_cell(source=body, outputs=outputs, id=f"code-{exec_count:02d}")
        cell['execution_count'] = exec_count
        nb.cells.append(cell)

    out_path = os.path.join(os.path.dirname(__file__),
                            '01-hello-gallowglass.ipynb')
    with open(out_path, 'w') as f:
        nbformat.write(nb, f)
    print(f'wrote {out_path}')


if __name__ == '__main__':
    main()
