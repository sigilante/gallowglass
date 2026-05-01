# Feedback from a first-real-world-test of Gallowglass

I wrote `demos/urb_watcher.gls` as a Gallowglass replica of a Hoon Gall agent
(`groundwire/groundwire/app/urb-watcher.hoon`), targeting compilation through
the Python bootstrap to PLAN.  Here is what I learned along the way — both
delights and friction points.  This is one developer's report from one
session; treat the suggestions as data, not demands.

## Things that just worked

- The **bootstrap dialect** is genuinely usable for non-trivial code.  74
  bindings, multiple data types, recursive list utilities, a state-threaded
  block processor, and a deterministic mock-block generator all compiled
  the first time once I'd internalized two pitfalls (see below).
- The **Python harness evaluator** (`dev/harness/plan.py`) is excellent.  It
  let me iterate without ever invoking a planvm binary.  Test runtime was
  bounded by Python recursion, not by anything semantic.
- **Pattern A** (single-constructor "struct" + `Nat` tag for variants,
  payload nested in `Pair`) felt natural after looking at `Compiler.gls`.
  It avoided every multi-constructor codegen issue.
- `if-then-else` for non-recursive Bool dispatch and `match { | 0 → ... | k
  → ... }` on `(lte b a)` for recursive Bool dispatch are an idiom that
  works reliably; I think both deserve to be promoted in the docs.
- The error from the codegen at compile time was **specific and precise**
  ("codegen: unbound variable 'UrbWatcher.mod_go'") — that pointed me
  directly at the offending arm.

## Friction I hit (each one a candidate test case)

These are concrete reproductions; if useful I am happy to file them as
bootstrap test cases or DECISIONS.md entries.

### 1. Bool-constructor match in a recursive function silently mis-resolves self-reference

```gallowglass
let mod_go : Nat → Nat → Nat
  = λ a b → match (lte b a) {
      | False → a
      | True  → mod_go (sub a b) b
    }
```

Compiles, then **fails at compile time** with `unbound variable
'Module.mod_go'` from inside `_build_nat_dispatch`'s succ-arm
sub-law compilation.

The same logic with `match (lte b a) { | 0 → a | _k → mod_go (sub a b) b }`
compiles fine.  And the same logic with `if lte b a then mod_go (sub a b)
b else a` compiles, but the result loops at evaluation time (eager succ
in the desugared Case_).

I think there are two distinct issues here:
- **(a)** The constructor-match path through `_compile_con_match` for a
  Bool scrutinee in a recursive law doesn't propagate `self_ref_name` into
  the lambda-lifted succ arm.  Either fix the propagation, or detect the
  pattern and reject it with a clear error.
- **(b)** `if c then RECURSE else BASE` desugars in a way that puts
  `RECURSE` in the eager position (or fails to wrap it in a thunk law).
  That's surprising because every reader assumes laziness in branch
  position.  Even if it isn't a bug per se, a one-line note in
  `BOOTSTRAP.md`'s "if-then-else" entry would have saved me 20 minutes.

### 2. Wildcard predecessor binding `| _ → body` doesn't lambda-lift captures

```gallowglass
let mod_nat : Nat → Nat → Nat
  = λ a b → match b {
      | 0 → 0
      | _ → mod_go a b      -- 'a' and 'mod_go' captured from outer
    }
```

Compiles, then loops at evaluation time (Python recursion limit).

The same logic with `| _bpred → mod_go a b` compiles and runs.  The
`Compiler.gls` version uses `| bpred → ...` for the same reason.

The fix is one of:
- Treat `PatVar('_')` and `PatWild` identically in `_make_pred_succ_law`
  for the purposes of lambda-lifting captures (they have the same
  semantics — bind nothing usable, just dispatch on succ).
- Or: emit a warning at `_compile_con_match` time when a wildcard
  succ-arm captures non-self outer locals.

### 3. The "single-constructor struct + Nat tag" idiom should be promoted

The mixed-arity sum-type pitfall (M9.3 skipped tests) is a real footgun —
the obvious modelling for an enum-like type (`type Effect = | Point Ship
... | Dns Host | Insc Id | Xfer Ship`) hits it directly.  The `csv_table`
demo doesn't show the workaround; the calculator demo defines `Expr` with
exactly this shape and then *avoids using `eval`* for examples.

I'd love a third demo (or a section of `BOOTSTRAP.md`) that explicitly
documents:

> *In the current bootstrap, model variants as a single-constructor record
> with a Nat tag and a payload nest of Pairs.  Dispatch with `if (nat_eq
> tag K)` chains, never with multi-arm constructor matches that mix unary
> and binary at tag>0.*

I figured this out by reading `Compiler.gls`, but a beginner won't.

### 4. Demos can't `use` the prelude

This is the friction of working from `csv_table.gls`'s "redefine everything
inline" template.  My demo starts with 50+ lines of `add`, `mul`, `mod_nat`,
`length`, `map`, `foldl`, `foldr`, `append`, `concat_list`, `list_filter_map`
— all 1:1 copies of `Compiler.gls` definitions.  This is a tax in the
restricted dialect.

The bootstrap test harness runs each demo with `module_env={}`, so even if
the prelude compiled, it wouldn't be available.  M12 ("Module system —
complete") landed; presumably the demo harness just needs to thread a
prebuilt `module_env` through.  A target like `make demos-with-prelude`
would unblock cleaner demos.

### 5. Error messages from `_compile_con_match`'s lambda-lifted arms point at the law, not the source

When (1) and (2) fired, the traceback was deep in `bootstrap/codegen.py`
with the only clue being `'UrbWatcher.mod_go'` in the message.  I had to
guess which `match` was the offender.  A `file:line:col` next to the
codegen error (matching the parser's diagnostic format) would close
that gap.

### 6. Recursion-limit guidance is unwritten lore

`test_calculator.py` bumps to `10_000`; `test_csv_table.py` does the same.
My demo needed `200_000` because `state_to_udiffs` over 3 nested folds.
With less code I'd have bisected to find the right number; an explicit
note ("if your demo evaluates anything bigger than 100 list cells, expect
to bump the limit ≥ 100k") would help.

The real fix is jets in `bplan.py` for `length`, `map`, `foldl`, `foldr`,
`append` — those would make the harness fast and bound the recursion to
the demo's algorithmic depth, not its allocation depth.

### 7. `eff` + `handle` returning constructor types — works, no test exercising it

I added a Tier 2 section to the demo: `eff RPC { rpc_get_block : Nat →
Block }` with a handler that interprets the op via `gen_block`.  This
works.  The handler returns a `Block` (a constructor App), and a
recursive function calls into the handler N times to process N blocks,
threading `UrbState` forward.  The Tier 2 tests pass with the same
counts as Tier 1.

What I worried about:  no existing demo or test exercises a handler
arm where the resumed continuation receives a constructor-App value
(every existing test has `kk : Nat → ...`).  In practice it works —
`kk val` is `App(kk, val)` and `val` is opaque to PLAN — but it was
not obvious from the test corpus.  Adding a test like

```python
def test_handle_returns_constructor():
    # `handle (op ()) { | return v → v | op _ k → k (MkPair 1 2) }` must
    # produce a value pattern-matchable as MkPair 1 2.
```

would settle the precedent and unlock more ambitious handler-style
demos.

## Things I'd put on the "easy wins" list

- Promote the **"single-constructor struct + Nat tag"** idiom in
  `BOOTSTRAP.md` § 2.1 with a small example.
- Add a **clear error** for wildcard `| _` succ-arms that capture outer
  locals other than self.
- Add **`length`, `append`, `concat_list`, `map`, `foldl`, `foldr`** as
  jets in `bplan.py`.  These are the bread-and-butter operations every
  demo uses; jetting them would convert all demo evaluation from
  recursion-limit-bound to instant.
- Show **how to `use` the prelude in a demo** (or document why it isn't
  yet supported).
- Add a top-level **`demos/README.md`** with a one-paragraph orientation
  and the "what idioms are safe in the bootstrap dialect" table.

## Things that delighted me

- **The error model.**  When I was wrong, I knew quickly.
- **`Compiler.gls` is excellent reference code.**  Reading lines 25–205
  taught me everything I needed.  It's the actual full prelude inlined
  + utilities; it answered every "how do I…?" question I had.
- **The `external mod Core.PLAN { inc : Nat → Nat }` bridge.**  Honest,
  visible, and exactly the right size of escape hatch.
- **Pattern matching on `match scrutinee { | Nil → ... | Cons h t → ...
  }` works for any `List a` regardless of `a`.**  This is "obvious" in a
  Hindley-Milner language but it's still a quiet pleasure when it just
  works for `List Effect`, `List (Pair Nat Point)`, etc., without any
  ceremony.
- **The harness/seed split.**  Being able to test entirely in Python
  without a planvm binary is the right call for a bootstrap.  It made
  this whole exercise feel like writing in a normal Python project.

## What I didn't try

- The **self-hosting compiler** (`compiler/src/Compiler.gls`).  I read
  it for reference but didn't compile or test against it.
- **Do-notation chains spanning multiple `eff` ops** (e.g., the Csv
  example with `Exn` and `State` interleaved).  My Tier 2 uses a
  single op type with the trivial `xx ← op args in pure xx` shape.
- **Glass IR emission**.  Mentioned in `SPEC.md` and the M17 milestone;
  no need for it in a demo, but it'd be nice if `make demo-glass-ir
  ARGS=urb_watcher.gls` printed the IR for inspection.

## Bottom line

Gallowglass-as-LLM-target works.  The bootstrap dialect has sharper edges
than the language spec implies (pitfalls 1, 2, and 3 above are the main
ones), but each is a learnable rule, not a fundamental limitation.  Once
internalized, the language is a clean place to write algorithmic
data-pipeline code.  The effect system, when it stabilizes, will close
the gap between this demo and the full Hoon original.

— Written 2026-04-29.
