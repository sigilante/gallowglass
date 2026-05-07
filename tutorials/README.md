# Tutorials

Notebook-based walkthroughs for Gallowglass. Each lesson is a
self-contained `.ipynb` that runs in the Gallowglass Jupyter kernel.

## Running

Install the kernel once per environment:

```bash
python3 -m bootstrap.jupyter_kernel install
```

Then open a tutorial in JupyterLab or notebook:

```bash
jupyter lab tutorials/01-hello-gallowglass.ipynb
```

Re-run all cells (Run → Run All Cells) to evaluate against your
local kernel.

## Index

| File                                | Topic                                                                                                |
|-------------------------------------|------------------------------------------------------------------------------------------------------|
| `01-hello-gallowglass.ipynb`        | Declarations, types, ADTs, pattern matching.                                                         |
| `02-typeclasses.ipynb`              | `Eq`, `Ord`, `Show`; constrained wrappers for class-method dispatch; defining your own instances.    |
| `03-glass-ir.ipynb`                 | The typed intermediate representation, pin hashes, content addressing.                               |
| `04-effects-and-handlers.ipynb`     | Effect rows, the `eff` declaration, `handle` shape, the `External` and `Abort` invariants.           |

The lessons are sequential — each one assumes the previous.

## Authoring

Each lesson has a sibling `_build_lesson_NN.py` script that
synthesises the `.ipynb` from a Python list of `(kind, body)` cell
descriptors. The script runs each code cell through
`GallowglassEvaluator` to capture real outputs, so the committed
notebook always reflects what the kernel produces.

To edit a lesson:

1. Modify the `CELLS` list in `_build_lesson_NN.py`.
2. Run `python3 tutorials/_build_lesson_NN.py` to regenerate the
   notebook with fresh outputs.
3. Verify with `python3 -m pytest tests/bootstrap/test_tutorials.py`.

The test suite catches output drift: if the kernel's renderer
changes a cell's output, the tutorial test fails until you
regenerate the notebook.

## Companion: `doc/phrasebook.md`

A dense, LLM-context-friendly reference covering canonical
Gallowglass patterns, common pitfalls, and the boundaries of what
the bootstrap currently supports. Read alongside the tutorials for
the full picture.
