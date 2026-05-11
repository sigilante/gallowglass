#!/usr/bin/env python3
"""
Build script for tutorials/06-quantum-states.ipynb. See
tutorials/_build_lesson_02.py for the pattern.
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
    # ------------------------------------------------------------------
    # Introduction
    # ------------------------------------------------------------------
    ('md', '''# Quantum State Simulation

Microsoft's [Quantum Katas](https://github.com/microsoft/quantumkatas)
open with a simple observation: quantum computing begins with complex
arithmetic and linear algebra.  A qubit is not a classical bit; it lives
in a two-dimensional complex vector space, and quantum gates are unitary
transformations on that space.

This notebook builds a quantum state simulator from first principles
in Gallowglass.  Because Gallowglass computes over `Nat`, we need
two new ideas before we can represent qubit amplitudes:

- **Signed fixed-point numbers** (`SFP`): a sign bit plus a scaled
  natural number, so we can represent negative and fractional values.
- **A fixed-point scale**: one constant at the top of the notebook that
  controls the decimal precision everywhere.

From those building blocks we derive:

1. `SFP` — signed fixed-point arithmetic (add, mul, negate, square)
2. `QState` — a qubit state α|0⟩ + β|1⟩ with `SFP` amplitudes
3. Measurement probabilities — |α|² and |β|²
4. `QGate` — a typeclass for quantum gates
5. Pauli-X, Pauli-Z, and Hadamard gate instances
6. Gate composition and self-inverse verification (H² ≈ I)
7. Bloch sphere coordinates — a concrete geometric picture

Assumes `05-interval-arithmetic.ipynb` — fixed-point display helpers.'''),

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------
    ('md', '## Imports\n\n'
           'We need all of `Core.Nat`\'s arithmetic: addition, '
           'multiplication, saturating subtraction, comparisons, '
           'integer division, and modulo.  `Core.Text` supplies the '
           '`Show` class and string utilities:'),
    ('code',
     'use Core.Nat unqualified { add, mul, sub, nat_lte, nat_lt, div_nat, mod_nat }\n'
     'use Core.Text { Show, show, show_nat, text_concat }'),

    # ------------------------------------------------------------------
    # Fixed-point scale
    # ------------------------------------------------------------------
    ('md', '## Fixed-Point Scale\n\n'
           'The irrational constant 1/√2 ≈ 0.7071… appears in every\n'
           'Hadamard gate calculation.  We need at least three decimal\n'
           'places of precision to track rounding errors honestly.  With\n'
           '`scale = 1000` we represent 0.707 as the `Nat` value `707`\n'
           'and display it as `"0.707"`.  All arithmetic stays in `Nat`;\n'
           'the scale factor is the only floating-point–like thing in scope.'),
    ('code', 'let scale : Nat = 1000'),
    ('code', '-- floor(1000 / √2) = floor(707.106...) = 707\n'
             'let inv_sqrt2_nat : Nat = 707'),

    ('md', 'The same display helpers as lesson 05, now with three decimal '
           'places.  `count_digits` counts decimal digits; `frac_digits` '
           'gives the number of places implied by `scale`; `zeros` pads '
           'leading zeros; `show_fixed` assembles the decimal string:'),
    ('code',
     'let count_digits : Nat → Nat\n'
     '  = λ nn →\n'
     '      if nat_lt nn 10\n'
     '      then 1\n'
     '      else add 1 (count_digits (div_nat nn 10))'),
    ('code',
     'let frac_digits : Nat → Nat\n'
     '  = λ sc → sub (count_digits sc) 1'),
    ('code',
     'let zeros : Nat → Text\n'
     '  = λ nn → match nn {\n'
     '      | 0  → ""\n'
     '      | kk → text_concat "0" (zeros kk)\n'
     '    }'),
    ('code',
     'let show_fixed : Nat → Nat → Text\n'
     '  = λ sc nn →\n'
     '      let whole = div_nat nn sc in\n'
     '      let frac  = mod_nat nn sc in\n'
     '      let pad   = sub (frac_digits sc) (count_digits frac) in\n'
     '      text_concat (show_nat whole)\n'
     '        (text_concat "."\n'
     '          (text_concat (zeros pad) (show_nat frac)))'),

    ('md', '`707` at `scale = 1000` renders as `"0.707"` — three '
           'decimal places with correct zero-padding:'),
    ('code', 'show_fixed scale inv_sqrt2_nat'),

    # ------------------------------------------------------------------
    # Signed fixed-point type
    # ------------------------------------------------------------------
    ('md', '## Signed Fixed-Point Numbers (`SFP`)\n\n'
           'A `Nat` can only represent zero or positive values.  Quantum\n'
           'amplitudes can be negative (e.g. the |−⟩ state has β = −1/√2).\n'
           'We introduce a two-constructor `Sign` type to carry the sign\n'
           'bit, then pack it with a `Nat` magnitude into `SFP`.\n\n'
           '`MkSFP Pos 707` represents +0.707.  `MkSFP Neg 707` represents\n'
           '−0.707.  The `Nat` field is always the *magnitude* — never\n'
           'a two\'s-complement encoding.'),
    ('code', 'type Sign = | Pos | Neg'),
    ('code', 'type SFP = | MkSFP Sign Nat'),

    ('md', 'Three fundamental constants.  `sfp_zero` is 0.000; `sfp_one`\n'
           'is 1.000 (i.e. one `scale` unit); `inv_sqrt2` is +0.707:'),
    ('code', 'let sfp_zero : SFP = MkSFP Pos 0'),
    ('code', 'let sfp_one  : SFP = MkSFP Pos scale'),
    ('code', 'let inv_sqrt2 : SFP = MkSFP Pos inv_sqrt2_nat'),

    # ------------------------------------------------------------------
    # SFP arithmetic
    # ------------------------------------------------------------------
    ('md', '## SFP Arithmetic\n\n'
           '`sfp_neg` flips the sign bit.  Negating `Pos 0` yields\n'
           '`Neg 0` — a signed zero — which compares as zero in all\n'
           'subsequent arithmetic:'),
    ('code',
     'let sfp_neg : SFP → SFP\n'
     '  = λ ss →\n'
     '      match ss {\n'
     '        | MkSFP sg mm →\n'
     '            match sg {\n'
     '              | Pos → MkSFP Neg mm\n'
     '              | Neg → MkSFP Pos mm\n'
     '            }\n'
     '      }'),

    ('md', '`sfp_mul_sign` encodes the sign-of-product rule:\n'
           '(+)(+) = (+); (+)(−) = (−); (−)(−) = (+):'),
    ('code',
     'let sfp_mul_sign : Sign → Sign → Sign\n'
     '  = λ sa sb →\n'
     '      match sa {\n'
     '        | Pos → match sb { | Pos → Pos | Neg → Neg }\n'
     '        | Neg → match sb { | Pos → Neg | Neg → Pos }\n'
     '      }'),

    ('md', '`sfp_add` implements sign-magnitude addition.  When the signs\n'
           'agree, add the magnitudes and keep the sign.  When they differ,\n'
           'subtract the smaller from the larger and take the sign of the\n'
           'larger:\n\n'
           '```\n'
           '(+a) + (+b) = +(a + b)\n'
           '(+a) + (−b) = +(a − b) if b ≤ a, else −(b − a)\n'
           '(−a) + (+b) = +(b − a) if a ≤ b, else −(a − b)\n'
           '(−a) + (−b) = −(a + b)\n'
           '```'),
    ('code',
     'let sfp_add : SFP → SFP → SFP\n'
     '  = λ aa bb →\n'
     '      match aa {\n'
     '        | MkSFP sa ma →\n'
     '            match bb {\n'
     '              | MkSFP sb mb →\n'
     '                  match sa {\n'
     '                    | Pos →\n'
     '                        match sb {\n'
     '                          | Pos → MkSFP Pos (add ma mb)\n'
     '                          | Neg →\n'
     '                              if nat_lte mb ma\n'
     '                              then MkSFP Pos (sub ma mb)\n'
     '                              else MkSFP Neg (sub mb ma)\n'
     '                        }\n'
     '                    | Neg →\n'
     '                        match sb {\n'
     '                          | Pos →\n'
     '                              if nat_lte ma mb\n'
     '                              then MkSFP Pos (sub mb ma)\n'
     '                              else MkSFP Neg (sub ma mb)\n'
     '                          | Neg → MkSFP Neg (add ma mb)\n'
     '                        }\n'
     '                  }\n'
     '            }\n'
     '      }'),

    ('md', '`sfp_mul` multiplies two fixed-point values.  Multiplying two\n'
           'scale-1000 numbers gives a scale-1000000 product; dividing by\n'
           '`scale` brings it back to scale-1000:\n\n'
           '```\n'
           '(a/1000) × (b/1000) = (a×b)/1000000 = (a×b÷1000)/1000\n'
           '```'),
    ('code',
     'let sfp_mul : SFP → SFP → SFP\n'
     '  = λ aa bb →\n'
     '      match aa {\n'
     '        | MkSFP sa ma →\n'
     '            match bb {\n'
     '              | MkSFP sb mb →\n'
     '                  MkSFP (sfp_mul_sign sa sb)\n'
     '                        (div_nat (mul ma mb) scale)\n'
     '            }\n'
     '      }'),

    ('md', '`sfp_sq` squares an `SFP`.  The result is always positive\n'
           '(sign² = +), so we can skip the sign dispatch:'),
    ('code',
     'let sfp_sq : SFP → SFP\n'
     '  = λ ss →\n'
     '      match ss {\n'
     '        | MkSFP sg mm → MkSFP Pos (div_nat (mul mm mm) scale)\n'
     '      }'),

    # ------------------------------------------------------------------
    # Display for SFP
    # ------------------------------------------------------------------
    ('md', '## Displaying `SFP` Values\n\n'
           '`show_sfp` renders the sign as `"+"` or `"-"` and appends\n'
           'the fixed-point decimal.  Positive values carry an explicit\n'
           '`"+"` so every amplitude in a state vector has a visible sign:'),
    ('code',
     'let show_sfp : SFP → Text\n'
     '  = λ ss →\n'
     '      match ss {\n'
     '        | MkSFP sg mm →\n'
     '            let sign_str =\n'
     '              match sg { | Pos → "+" | Neg → "-" } in\n'
     '            text_concat sign_str (show_fixed scale mm)\n'
     '      }'),

    ('md', 'A quick sanity check — zero, one, and the two signs of 1/√2:'),
    ('code', 'show_sfp sfp_zero'),
    ('code', 'show_sfp sfp_one'),
    ('code', 'show_sfp inv_sqrt2'),
    ('code', 'show_sfp (sfp_neg inv_sqrt2)'),

    # ------------------------------------------------------------------
    # Qubit state
    # ------------------------------------------------------------------
    ('md', '## Qubit State — α|0⟩ + β|1⟩\n\n'
           'A pure qubit state is a superposition of the two computational\n'
           'basis states |0⟩ and |1⟩ with complex amplitudes α and β\n'
           'satisfying |α|² + |β|² = 1.\n\n'
           'We restrict to *real* amplitudes in this tutorial — complex\n'
           'amplitudes require two `SFP` components per amplitude and are\n'
           'the natural extension once you want the Y gate or arbitrary\n'
           'phase gates.  Real amplitudes are enough for X, Z, and H.\n\n'
           '`MkQState alpha beta` holds the amplitude pair:'),
    ('code', 'type QState = | MkQState SFP SFP'),

    ('md', 'The four named states.  `ket_zero` and `ket_one` are the\n'
           'computational basis; `ket_plus` and `ket_minus` are the\n'
           'diagonal basis (eigenstates of the Hadamard gate):'),
    ('code', 'let ket_zero  : QState = MkQState sfp_one sfp_zero'),
    ('code', 'let ket_one   : QState = MkQState sfp_zero sfp_one'),
    ('code', 'let ket_plus  : QState = MkQState inv_sqrt2 inv_sqrt2'),
    ('code', 'let ket_minus : QState = MkQState inv_sqrt2 (sfp_neg inv_sqrt2)'),

    ('md', '`show_qs` renders a state as `±A.AAA|0⟩ + ±B.BBB|1⟩`:'),
    ('code',
     'let show_qs : QState → Text\n'
     '  = λ qs →\n'
     '      match qs {\n'
     '        | MkQState alpha beta →\n'
     '            text_concat (show_sfp alpha)\n'
     '              (text_concat "|0⟩ + "\n'
     '                (text_concat (show_sfp beta) "|1⟩"))\n'
     '      }'),
    ('code', 'show_qs ket_zero'),
    ('code', 'show_qs ket_one'),
    ('code', 'show_qs ket_plus'),
    ('code', 'show_qs ket_minus'),

    # ------------------------------------------------------------------
    # Measurement probabilities
    # ------------------------------------------------------------------
    ('md', '## Measurement Probabilities\n\n'
           'Measuring a qubit in state α|0⟩ + β|1⟩ yields |0⟩ with\n'
           'probability |α|² and |1⟩ with probability |β|².  In our\n'
           'scale-1000 representation, `prob_zero` and `prob_one` return\n'
           'values in [0, 1000] where 1000 means certainty (100.0 %)\n'
           'and 499 means ≈ 49.9 %.\n\n'
           '`sfp_prob` extracts the magnitude and squares it (sign is\n'
           'irrelevant — probability is always non-negative):'),
    ('code',
     'let sfp_prob : SFP → Nat\n'
     '  = λ ss →\n'
     '      match ss {\n'
     '        | MkSFP sg mm → div_nat (mul mm mm) scale\n'
     '      }'),
    ('code',
     'let prob_zero : QState → Nat\n'
     '  = λ qs → match qs { | MkQState alpha beta → sfp_prob alpha }'),
    ('code',
     'let prob_one : QState → Nat\n'
     '  = λ qs → match qs { | MkQState alpha beta → sfp_prob beta }'),

    ('md', '`ket_zero` gives 100.0 % probability of measuring |0⟩.\n'
           '`ket_plus` gives ≈ 49.9 % — the fixed-point error from\n'
           '707² ÷ 1000 = 499.849 → 499 is 0.1 %:'),
    ('code', 'prob_zero ket_zero'),
    ('code', 'prob_zero ket_plus'),

    ('md', 'The Born rule requires |α|² + |β|² = 1.  At scale 1000 the\n'
           'two probabilities sum to 998 rather than 1000 — the 0.2 %\n'
           'shortfall from rounding 1/√2 to three decimal places.  This\n'
           'is the honest price of fixed-point simulation:'),
    ('code', 'add (prob_zero ket_plus) (prob_one ket_plus)'),

    # ------------------------------------------------------------------
    # QGate typeclass
    # ------------------------------------------------------------------
    ('md', '## The `QGate` Typeclass\n\n'
           'A quantum gate is any type `g` that can transform a `QState`.\n'
           '`QGate` captures exactly that contract:\n\n'
           '```\n'
           'class QGate g {\n'
           '  apply_gate : g → QState → QState\n'
           '}\n'
           '```\n\n'
           'Three instances follow for the Pauli-X, Pauli-Z, and Hadamard\n'
           'gates — the simplest complete set from the Quantum Katas\n'
           'introductory module.'),
    ('code',
     'class QGate g {\n'
     '  apply_gate : g → QState → QState\n'
     '}'),

    # ------------------------------------------------------------------
    # Pauli-X (bit flip)
    # ------------------------------------------------------------------
    ('md', '## Pauli-X Gate (Bit Flip)\n\n'
           'X|0⟩ = |1⟩, X|1⟩ = |0⟩.  In the amplitude representation,\n'
           'X simply swaps α and β.  The matrix is:\n\n'
           '```\n'
           'X = | 0  1 |\n'
           '    | 1  0 |\n'
           '```'),
    ('code', 'type GateX = | GateX'),
    ('code',
     'instance QGate GateX {\n'
     '  apply_gate = λ gg qs →\n'
     '      match qs {\n'
     '        | MkQState alpha beta → MkQState beta alpha\n'
     '      }\n'
     '}'),

    # ------------------------------------------------------------------
    # Pauli-Z (phase flip)
    # ------------------------------------------------------------------
    ('md', '## Pauli-Z Gate (Phase Flip)\n\n'
           'Z|0⟩ = |0⟩, Z|1⟩ = −|1⟩.  Z leaves the |0⟩ amplitude\n'
           'unchanged and negates the |1⟩ amplitude:\n\n'
           '```\n'
           'Z = | 1   0 |\n'
           '    | 0  −1 |\n'
           '```\n\n'
           'Z turns |+⟩ into |−⟩ and vice versa — it is the bit-flip\n'
           'gate in the *diagonal* (Hadamard) basis.'),
    ('code', 'type GateZ = | GateZ'),
    ('code',
     'instance QGate GateZ {\n'
     '  apply_gate = λ gg qs →\n'
     '      match qs {\n'
     '        | MkQState alpha beta → MkQState alpha (sfp_neg beta)\n'
     '      }\n'
     '}'),

    # ------------------------------------------------------------------
    # Hadamard
    # ------------------------------------------------------------------
    ('md', '## Hadamard Gate (Superposition)\n\n'
           'The Hadamard gate creates — or collapses — superposition.\n'
           'H|0⟩ = |+⟩, H|1⟩ = |−⟩, H|+⟩ ≈ |0⟩, H|−⟩ ≈ |1⟩.\n\n'
           '```\n'
           'H = (1/√2) | 1   1 |\n'
           '            | 1  −1 |\n'
           '```\n\n'
           'Applied to α|0⟩ + β|1⟩:\n\n'
           '```\n'
           'α′ = (α + β) / √2\n'
           'β′ = (α − β) / √2\n'
           '```\n\n'
           'In `SFP`: compute `sum = α + β` and `diff = α − β`, then\n'
           'multiply each by `inv_sqrt2` (= 0.707).  Fixed-point\n'
           'multiplication loses ≈ 0.1 % per application — two\n'
           'applications accumulate to H² ≈ I with 0.1 % residual:'),
    ('code', 'type GateH = | GateH'),
    ('code',
     'instance QGate GateH {\n'
     '  apply_gate = λ gg qs →\n'
     '      match qs {\n'
     '        | MkQState alpha beta →\n'
     '            let sum  = sfp_add alpha beta in\n'
     '            let diff = sfp_add alpha (sfp_neg beta) in\n'
     '            MkQState (sfp_mul sum  inv_sqrt2)\n'
     '                     (sfp_mul diff inv_sqrt2)\n'
     '      }\n'
     '}'),

    # ------------------------------------------------------------------
    # Constrained wrapper
    # ------------------------------------------------------------------
    ('md', 'The constrained wrapper routes through the typeclass dictionary.\n'
           'The `∀ g. QGate g =>` quantifier is what triggers dictionary\n'
           'insertion at compile time:'),
    ('code',
     'let run_gate : ∀ g. QGate g => g → QState → QState\n'
     '  = λ gg qs → apply_gate gg qs'),

    # ------------------------------------------------------------------
    # Gate demonstrations
    # ------------------------------------------------------------------
    ('md', '## Gate Demonstrations\n\n'
           '`X|0⟩` flips the bit — we get |1⟩:'),
    ('code', 'show_qs (run_gate GateX ket_zero)'),

    ('md', '`H|0⟩` creates equal superposition — we get |+⟩:'),
    ('code', 'show_qs (run_gate GateH ket_zero)'),

    ('md', '`H|1⟩` creates |−⟩ (equal superposition, negative β):'),
    ('code', 'show_qs (run_gate GateH ket_one)'),

    ('md', '`Z|+⟩` flips the phase — we get |−⟩.  This is the Pauli-X\n'
           'gate in the Hadamard basis: just as X flips |0⟩ ↔ |1⟩, Z\n'
           'flips |+⟩ ↔ |−⟩:'),
    ('code', 'show_qs (run_gate GateZ ket_plus)'),

    # ------------------------------------------------------------------
    # Self-inverse verification
    # ------------------------------------------------------------------
    ('md', '## Self-Inverse Verification\n\n'
           'All three gates are their own inverses: G² = I.\n\n'
           'X² is exact — swapping twice is a no-op:'),
    ('code', 'show_qs (run_gate GateX (run_gate GateX ket_plus))'),

    ('md', 'Z² is exact — negating twice is a no-op:'),
    ('code', 'show_qs (run_gate GateZ (run_gate GateZ ket_plus))'),

    ('md', 'H² accumulates fixed-point rounding: `707 × 707 ÷ 1000 = 499`,\n'
           'and `(707 + 707) × 707 ÷ 1000 = 999`.  The α amplitude of\n'
           'H(H|0⟩) is `+0.999`, not `+1.000`.  The 0.1 % error is the\n'
           'honest cost of approximating 1/√2 as 0.707:'),
    ('code', 'show_qs (run_gate GateH (run_gate GateH ket_zero))'),

    # ------------------------------------------------------------------
    # Bloch sphere
    # ------------------------------------------------------------------
    ('md', '## Bloch Sphere Coordinates\n\n'
           'Every pure qubit state corresponds to a point on the unit\n'
           'sphere (the Bloch sphere).  For real amplitudes α, β:\n\n'
           '```\n'
           'x = 2αβ          (longitude)\n'
           'z = α² − β²      (latitude: +1 = north pole = |0⟩,\n'
           '                            −1 = south pole = |1⟩)\n'
           '```\n\n'
           '|0⟩ sits at the north pole (x=0, z=+1); |1⟩ at the south\n'
           'pole (x=0, z=−1); |+⟩ and |−⟩ on the equator at ±x.\n\n'
           '`sfp_two` is the fixed-point representation of 2.000:'),
    ('code', 'let sfp_two : SFP = MkSFP Pos (mul 2 scale)'),

    ('code',
     'let bloch_z : QState → SFP\n'
     '  = λ qs →\n'
     '      match qs {\n'
     '        | MkQState alpha beta →\n'
     '            sfp_add (sfp_sq alpha) (sfp_neg (sfp_sq beta))\n'
     '      }'),
    ('code',
     'let bloch_x : QState → SFP\n'
     '  = λ qs →\n'
     '      match qs {\n'
     '        | MkQState alpha beta →\n'
     '            sfp_mul sfp_two (sfp_mul alpha beta)\n'
     '      }'),

    ('md', 'The four basis states at their expected Bloch sphere positions:'),
    ('code',
     'text_concat "ket_zero  z=" (text_concat (show_sfp (bloch_z ket_zero))\n'
     '  (text_concat "  x=" (show_sfp (bloch_x ket_zero))))'),
    ('code',
     'text_concat "ket_one   z=" (text_concat (show_sfp (bloch_z ket_one))\n'
     '  (text_concat "  x=" (show_sfp (bloch_x ket_one))))'),
    ('code',
     'text_concat "ket_plus  z=" (text_concat (show_sfp (bloch_z ket_plus))\n'
     '  (text_concat "  x=" (show_sfp (bloch_x ket_plus))))'),
    ('code',
     'text_concat "ket_minus z=" (text_concat (show_sfp (bloch_z ket_minus))\n'
     '  (text_concat "  x=" (show_sfp (bloch_x ket_minus))))'),

    ('md', '`ket_plus` has x ≈ +0.998 (should be +1.000): the 0.2 %\n'
           'shortfall comes from 707² ÷ 1000 = 499 rather than 500.\n'
           'Bloch coordinates are quadratic in the amplitudes, so the\n'
           'fixed-point error is doubled vs. the amplitude error.\n\n'
           'Applying a gate rotates the point on the sphere.  X is a π\n'
           'rotation about the X-axis (north ↔ south); Z is a π rotation\n'
           'about the Z-axis (|+⟩ ↔ |−⟩); H is a π/2 rotation that\n'
           'maps |0⟩ → |+⟩:'),
    ('code',
     'text_concat "H|0⟩  z=" (text_concat (show_sfp (bloch_z (run_gate GateH ket_zero)))\n'
     '  (text_concat "  x=" (show_sfp (bloch_x (run_gate GateH ket_zero)))))'),
    ('code',
     'text_concat "X|0⟩  z=" (text_concat (show_sfp (bloch_z (run_gate GateX ket_zero)))\n'
     '  (text_concat "  x=" (show_sfp (bloch_x (run_gate GateX ket_zero)))))'),

    # ------------------------------------------------------------------
    # Dominant measurement outcome
    # ------------------------------------------------------------------
    ('md', '## Dominant Measurement Outcome\n\n'
           'Given a state, which basis state is more likely to be measured?\n'
           '`more_zero` returns `True` when P(|0⟩) > P(|1⟩).\n'
           'For |+⟩, the probabilities are equal (499 = 499) so\n'
           '`more_zero` returns `False` — not *strictly* more likely:'),
    ('code',
     'let more_zero : QState → Bool\n'
     '  = λ qs →\n'
     '      match qs {\n'
     '        | MkQState alpha beta →\n'
     '            nat_lt (sfp_prob beta) (sfp_prob alpha)\n'
     '      }'),
    ('code', 'more_zero ket_zero'),
    ('code', 'more_zero ket_one'),
    ('code', 'more_zero ket_plus'),

    ('md', 'After an H gate the computational basis state |0⟩ becomes\n'
           'an equal superposition — so even |0⟩, which starts with\n'
           '100 % probability of measuring |0⟩, loses that certainty\n'
           'the moment it enters a Hadamard:'),
    ('code', 'more_zero (run_gate GateH ket_zero)'),

    ('md', '''## Summary

We built a single-qubit quantum state simulator from scratch in
Gallowglass using only `Nat` arithmetic.

| Component | What it is |
|-----------|------------|
| `SFP` | Signed fixed-point number: sign bit + scaled `Nat` |
| `QState` | Qubit state: two `SFP` amplitudes α, β |
| `QGate` | Typeclass: any type that can transform a `QState` |
| `GateX` | Pauli-X (bit flip): α ↔ β |
| `GateZ` | Pauli-Z (phase flip): β ↦ −β |
| `GateH` | Hadamard: α′ = (α+β)/√2, β′ = (α−β)/√2 |

The fixed-point rounding errors (0.1 % per H application) are not
a flaw to be hidden — they are the honest signature of finite precision,
and understanding them is a prerequisite for reasoning about numerical
quantum simulation.  A scale of 10 000 would cut the error tenfold;
exact rational arithmetic would eliminate it entirely.

Natural extensions:
- **Complex amplitudes**: add a second `SFP` field per amplitude
  (`MkComplex SFP SFP`) to support the Y gate and arbitrary phase rotations.
- **Two-qubit states**: tensor-product representation as four `SFP`
  amplitudes for |00⟩, |01⟩, |10⟩, |11⟩; CNOT becomes a 4×4 gate.
- **Grover's algorithm**: an oracle gate instance plus the diffusion
  operator, both as `QGate` instances on a two-qubit `QState`.'''),
]


# ---------------------------------------------------------------------------
# Build machinery (identical across all lesson scripts)
# ---------------------------------------------------------------------------

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
                            '06-quantum-states.ipynb')
    with open(out_path, 'w') as f:
        nbformat.write(nb, f)
    print(f'wrote {out_path}')


if __name__ == '__main__':
    main()
