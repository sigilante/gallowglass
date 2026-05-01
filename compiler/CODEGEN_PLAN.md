# M8.5 Codegen Implementation Plan

**File:** `compiler/src/Compiler.gls` — append as Section 24 after the existing ~1730 lines.
**Reference:** `bootstrap/codegen.py` — every function here maps to a Python method.
**Goal:** Produce PLAN values byte-identical to the Python bootstrap for the same input.

---

## The pred_env Bootstrap Limitation

The single most important constraint throughout this section. The bootstrap codegen
creates a fresh `pred_env` (arity=1, no locals) for arm[1+] when there are:
- Multiple App-bearing constructor arms in one `match`, OR
- Multiple nullary constructor arms compiled via nat-dispatch (handled in `_compile_adt_dispatch`)

**Rule:** Every `match` on an algebraic type must have **at most one App arm** whose body
references outer lambda parameters. Violate this and you get `unbound variable` errors.

**Universal workaround pattern** (already established in the parser):
1. Write a single-arm extractor helper (`arm_is_nat`, `expr_tag`, `planval_is_nat`, etc.)
2. Dispatch via `nat_eq` / `tok_is` chains
3. Each dispatch branch calls a single-arm helper that destructures exactly one constructor

This pattern is applied everywhere below. It is not optional.

---

## 1. CEnv Type and Accessors

The `CEnv` type is already declared at line 516. It encodes four fields nested in arity-≤2
constructors:
```
CEnv = MkCEnv (Pair globals locals) (Pair arity self_ref)
  globals  : List (Pair Nat PlanVal)   -- fq_name_nat → PlanVal
  locals   : List (Pair Nat Nat)       -- name_nat → de Bruijn index
  arity    : Nat                       -- current law's param count
  self_ref : Option Nat                -- own FQ name for self-recursion, or None
```

**Write first** (all are single-constructor matches — safe):
```
cenv_empty   : CEnv
cenv_make    : List (Pair Nat PlanVal) → List (Pair Nat Nat) → Nat → Option Nat → CEnv
cenv_globals : CEnv → List (Pair Nat PlanVal)
cenv_locals  : CEnv → List (Pair Nat Nat)
cenv_arity   : CEnv → Nat
cenv_self    : CEnv → Option Nat
cenv_set_arity      : CEnv → Nat → CEnv
cenv_set_self       : CEnv → Option Nat → CEnv
cenv_bind_local     : CEnv → Nat → Nat → CEnv
cenv_bind_global    : CEnv → Nat → PlanVal → CEnv
cenv_local_lookup   : CEnv → Nat → Option Nat
cenv_global_lookup  : CEnv → Nat → Option PlanVal
cenv_new_param      : CEnv → Nat → CEnv   -- add name at (arity+1), increment arity
```

`cenv_new_param` mirrors Python `bind_param`: appends `name → arity+1` to locals, sets
`arity = arity+1`. Existing locals are NOT shifted.

**ConTable:** `List (Pair Nat (Pair Nat Nat))` mapping `fq_name_nat → (tag, arity)`.
```
contab_empty  : List (Pair Nat (Pair Nat Nat))
contab_lookup : List (Pair Nat (Pair Nat Nat)) → Nat → Option (Pair Nat Nat)
contab_insert : List (Pair Nat (Pair Nat Nat)) → Nat → Pair Nat Nat
              → List (Pair Nat (Pair Nat Nat))
```

---

## 2. PlanVal Helpers and PLAN-Building Primitives

```
id_law     : PlanVal = PLaw 0 (MkPair 1 (PNat 1))      -- L(1, 0, N(1))
const2_law : PlanVal = PLaw 0 (MkPair 2 (PNat 1))      -- L(2, 0, N(1))
```

PlanVal classifiers (each: single App arm + wildcard — safe):
```
planval_is_nat : PlanVal → Nat     -- 1 if PNat
planval_get_nat : PlanVal → Nat    -- extract k from PNat k, else 0
planval_is_pin  : PlanVal → Nat    -- 1 if PPin
planval_is_app  : PlanVal → Nat    -- 1 if PApp
```

Application helpers:
```
cg_bapp  : PlanVal → PlanVal → PlanVal    -- A(A(N(0),f),x) — law-body apply
cg_apply : PlanVal → PlanVal → Nat → PlanVal  -- bapp if arity>0, PApp if 0
cg_ensure_pin : PlanVal → PlanVal         -- PPin v if not already PPin
```

Case_ (opcode 3) builders:
```
-- cg_build_case3_nat: (3 p l a z m scr) with bapp chain if arity>0
cg_build_case3_nat : PlanVal → PlanVal → PlanVal → PlanVal → PlanVal → PlanVal → Nat → PlanVal

-- cg_build_op2: (3 id id id z m scr) — nat iteration dispatch
cg_build_op2 : PlanVal → PlanVal → PlanVal → Nat → PlanVal

-- cg_build_reflect_dispatch: (3 id id app_fn z m scr) — constructor dispatch
cg_build_reflect_dispatch : PlanVal → PlanVal → PlanVal → PlanVal → Nat → PlanVal
```

Constructor body builder (mirrors Python `_build_constructor_body`):
```
cg_build_constructor_body : Nat → Nat → PlanVal  -- tag, n_fields → law body
cg_apply_params : PlanVal → Nat → Nat → PlanVal  -- acc, idx, max → bapp loop
```

Nat literal quoting (mirrors Python `_compile_nat_literal`):
```
cg_quote_nat : Nat → Nat → PlanVal   -- value, arity → PNat or PApp(PNat 0)(PNat value)
-- Quote form used when arity > 0 AND value ≤ arity (would collide with de Bruijn index)
```

---

## 3. AST Tag Extractors and Single-Arm Helpers

**These are the workaround machinery. Write all of them before any recursive function.**

### Expr helpers
```
expr_tag : Expr → Nat   -- 0=EVar 1=EApp 2=ELam 3=ELet 4=ENat 5=EIf 6=EMatch 7=EPin
-- SAFE: multiple App arms all return constant nats, no outer param references
```

Per-constructor accessors (each: single App arm + wildcard — safe):
```
cg_is_lam   : Expr → Nat       -- 1 if ELam
cg_lam_param : Expr → Nat      -- param from ELam, else 0
cg_lam_body  : Expr → Expr     -- body from ELam, else expr
cg_evar_name : Expr → Nat      -- name from EVar, else 0
cg_app_fun   : Expr → Expr     -- fun from EApp
cg_app_arg   : Expr → Expr     -- arg from EApp
```

### MatchArm helpers
```
arm_is_nat  : MatchArm → Nat   -- 1 if ArmNat
arm_is_con  : MatchArm → Nat   -- 1 if ArmCon
arm_is_var  : MatchArm → Nat   -- 1 if ArmVar
arm_get_nat_tag   : MatchArm → Nat
arm_get_nat_body  : MatchArm → Expr
arm_get_var_name  : MatchArm → Nat
arm_get_var_body  : MatchArm → Expr
arm_get_con_name  : MatchArm → Nat
arm_get_con_fields : MatchArm → List Nat
arm_get_con_body  : MatchArm → Expr
```

### Decl helpers
```
decl_is_let  : Decl → Nat   -- 1 if DLet
decl_is_type : Decl → Nat   -- 1 if DType
decl_is_ext  : Decl → Nat   -- 1 if DExt
decl_get_let_name  : Decl → Nat
decl_get_let_body  : Decl → Expr
decl_get_type_name : Decl → Nat
decl_get_type_cdefs : Decl → List ConDef
decl_get_ext_mod   : Decl → Nat
decl_get_ext_items : Decl → List Nat
```

---

## 4. Recursive AST Traversal

**Canonical pattern for all AST-recursive functions:**
```
let cg_collect_free : Expr → List Nat → List Nat → List Nat
  = λ expr bound acc →
      match (nat_eq (expr_tag expr) 0) {   -- EVar
        | 0 →
            match (nat_eq (expr_tag expr) 1) {   -- EApp
              | 0 → ...chain through all tags...
              | k → cg_free_app expr bound acc
            }
        | k → cg_free_var expr bound acc
      }
```

Each per-constructor helper does ONE match on that constructor — safe:
```
cg_free_var   : Expr → List Nat → List Nat → List Nat
cg_free_app   : Expr → List Nat → List Nat → List Nat
cg_free_lam   : Expr → List Nat → List Nat → List Nat  -- add param to bound
cg_free_let   : Expr → List Nat → List Nat → List Nat  -- add name to bound
cg_free_if    : Expr → List Nat → List Nat → List Nat
cg_free_match : Expr → List Nat → List Nat → List Nat
cg_free_pin   : Expr → List Nat → List Nat → List Nat
```

Wrap into:
```
cg_free_vars : Expr → List Nat → CEnv → List Nat
  -- body, bound_by_lam_params, env → free locals (names in env.locals not in bound)
```

Self-reference detection (same pattern):
```
cg_has_var     : Expr → Nat → Nat    -- does expr contain EVar name?
cg_body_uses_self : Expr → CEnv → Nat
```

Lambda flattening:
```
cg_flatten_lam : Expr → Pair (List Nat) Expr
  -- DANGER: match expr { | ELam p b → ... | _ → expr } — TWO App arms, UNSAFE
  -- Workaround: use cg_is_lam + cg_lam_param + cg_lam_body helpers
```

---

## 5. Nat Match Compilation

Mirrors Python `_build_nat_dispatch`.

```
cg_extract_nat_arms : List MatchArm → List (Pair Nat Expr)
  -- collect all ArmNat arms, sorted by tag (use insertion sort)

cg_sort_nat_pairs   : List (Pair Nat Expr) → List (Pair Nat Expr)
cg_insert_sorted_pair : Pair Nat Expr → List (Pair Nat Expr) → List (Pair Nat Expr)

cg_extract_wild_arm : List MatchArm → Option (Pair (Option Nat) Expr)
  -- ArmVar n e → Some (MkPair (Some n) e); no arm → None
```

Main dispatcher:
```
cg_build_nat_dispatch : List (Pair Nat Expr) → Option (Pair (Option Nat) Expr)
                       → PlanVal → CEnv → ConTable → Nat → PlanVal
  -- nat_arms (sorted), wild, scrutinee, env, ctab, hint → PlanVal
```

Succ-law builder (the key part that mirrors the pred_env pattern in Python):
```
cg_make_succ_fn : List (Pair Nat Expr) → Option (Pair (Option Nat) Expr)
                 → CEnv → ConTable → Nat → PlanVal
  -- When rest_arms is non-empty: build L(1,0,inner) with fresh pred_env (arity=1,no locals)
  -- When rest_arms is empty: build const2 wrapping wild body

cg_make_wild_succ : Option (Pair (Option Nat) Expr) → CEnv → ConTable → Nat → PlanVal
  -- Handles None (unreachable), anonymous wildcard (const2), PatVar (pred_succ_law)

cg_make_pred_succ_law : Expr → Nat → CEnv → ConTable → Nat → PlanVal
  -- Lambda-lift body, bind var_name at index (n_free+1), return PPin(PLaw(...))
  -- partially applied to free vars
```

---

## 6. Constructor Match Compilation

Mirrors Python `_compile_con_match` and `_compile_adt_dispatch`.

```
cg_compile_con_match : PlanVal → List MatchArm → CEnv → ConTable → Nat → PlanVal
  -- scrutinee, arms, env, ctab, hint → PlanVal
  -- Separates nullary arms (nat dispatch) from field arms (app handler)
```

**All-nullary path** (all matched constructors arity 0):
- Tag IS the value; delegate to `cg_build_nat_dispatch` on tags

**Mixed/field path** (one or more constructors have fields):
Use `cg_build_reflect_dispatch` (opcode 3) with:
- `z_body`: nullary arm at tag 0, or wild body, or 0
- `m_body`: succ law dispatching on remaining nullary arms (compiled in fresh pred_env)
- `app_fn`: the app handler law

```
cg_build_field_arm_law : List (Pair Nat (Pair (List Nat) Expr))
                      → Option (Pair (Option Nat) Expr)
                      → CEnv → ConTable → Nat → PlanVal
  -- field_arms = [(tag, (field_names, body))], wild, env, ctab, hint → handler PlanVal
  -- Finds free locals in field arm bodies, lambda-lifts, partially applies
```

**pred_env note for `cg_build_field_arm_law`:** The "free_locals from field arm bodies only"
rule (not wild_body) is the same limitation as in Python. The handler law only captures
variables that appear in field arm bodies. Wild body must not reference locals that
don't appear in any field arm body.

```
cg_build_tag_chain :
    List (Pair Nat PlanVal) → Option (Pair (Option Nat) Expr)
    → PlanVal → CEnv → ConTable → Nat → PlanVal
  -- Already-compiled field arm values, wild, scrutinee, env, ctab, hint
  -- Used inside app handler where field arms are pre-compiled
```

---

## 7. Lambda Lifting

Mirrors Python `_compile_lam_as_law` and `_compile_lam_lifted`.

```
cg_bind_params : List Nat → CEnv → CEnv
  -- fold cenv_new_param over param list

cg_compile_lam_as_law : List Nat → Expr → CEnv → ConTable → Nat → PlanVal
  -- Build fresh body env (empty locals, self_ref preserved), compile body, return PLaw

cg_compile_lam_lifted : List Nat → Expr → CEnv → ConTable → Nat → PlanVal
  -- Find free vars, build lifted env (free_vars || params), compile body,
  -- pin lifted law, partially apply to free vars in outer env

cg_make_lifted_env : List Nat → List Nat → List (Pair Nat PlanVal) → Option Nat → CEnv
  -- free_var_names, param_names, globals, self_ref → env with free_vars at 1..n, params at n+1..

cg_apply_free_vars : PlanVal → List Nat → CEnv → PlanVal
  -- partial application: fold cg_apply over free vars
```

---

## 8. Per-Expression Compilers (Layer 8)

Each takes `Expr → CEnv → ConTable → Nat → PlanVal`. Each does ONE match on one Expr
constructor — all safe.

```
cg_compile_var   -- EVar: self-ref check, local lookup, global lookup with quoting
cg_compile_app   -- EApp: compile fun, compile arg, cg_apply
cg_compile_enat  -- ENat: cg_quote_nat
cg_compile_elet  -- ELet: compile rhs, extend env, compile body
                 --   arity>0: PLAN let form PApp(PApp(PNat 1, rhs), body)
                 --   arity=0: add to globals, compile body
cg_compile_if    -- EIf: Case_(id,id,id, else, const2(then), cond)
cg_compile_match -- EMatch: classify arms, dispatch to nat/con match
cg_compile_lam   -- ELam: flatten params, dispatch as_law vs lifted
cg_compile_pin   -- EPin: compile rhs, ensure_pin, bind global, compile body
```

Main dispatch:
```
cg_compile_expr : Expr → CEnv → ConTable → Nat → PlanVal
  -- Uses expr_tag + nat_eq chain to route to per-constructor helpers
  -- MUST NOT use a single match with multiple Expr arms
```

---

## 9. Program-Level Passes

### Builtin Registration
```
cg_register_builtins : CEnv → CEnv
  -- Adds False=PNat 0, True=PNat 1, Unit=PNat 0 to globals

cg_register_builtin_contab : List (Pair Nat (Pair Nat Nat)) → List (Pair Nat (Pair Nat Nat))
  -- Adds False=(0,0), True=(1,0), Unit=(0,0) to contab
```

Pre-compute name-nats (UTF-8 LE packed):
```
name_nat_False : Nat = 0x65736C6146   -- "False"
name_nat_True  : Nat = 0x657572540    -- "True"
name_nat_Unit  : Nat = 0x74696E55    -- "Unit"
```
(Verify these with `name_encode "False"` etc. using the existing nat_byte_len/shift encoding.)

### Opcode Table
```
cg_core_plan_opcodes : List (Pair Nat Nat)
  -- [(name_nat_Core_PLAN_pin, 0), (name_nat_Core_PLAN_mk_law, 1),
  --  (name_nat_Core_PLAN_inc, 2), (name_nat_Core_PLAN_reflect, 3),
  --  (name_nat_Core_PLAN_force, 4)]
```

### Name Concatenation
```
name_concat_dot : Nat → Nat → Nat   -- "mod" ++ "." ++ "short" as nat
  -- Uses nat_byte_len + bit_or + shift_left (all already in Compiler.gls)
```

### Three-Pass Compilation
```
cg_pass1 : List Decl → Nat → CEnv → ConTable → List (Pair Nat PlanVal)
         → Pair CEnv (Pair ConTable (List (Pair Nat PlanVal)))
  -- For each DType: register constructors (tag 0,1,2,...), build PLaw bodies,
  --                 add to globals and compiled list
  -- Uses decl_is_type + decl_get_type_* dispatch (NOT multi-arm constructor match)

cg_pass2 : List Decl → Nat → CEnv → List (Pair Nat PlanVal)
         → Pair CEnv (List (Pair Nat PlanVal))
  -- For each DExt: look up in cg_core_plan_opcodes, build PPin(PNat opcode)
  --                or opaque sentinel; add to globals and compiled list

cg_pass3 : List Decl → Nat → CEnv → ConTable → List (Pair Nat PlanVal)
         → List (Pair Nat PlanVal)
  -- For each DLet: build fq_env (empty locals, self_ref=fq_nat),
  --                compile body, add to globals (for subsequent defs) and result

compile_program : List Decl → Nat → List (Pair Nat PlanVal)
  -- Wire passes: builtin init → pass1 → pass2 → pass3
```

**pred_env note for all passes:** Use `decl_is_let`/`decl_is_type`/`decl_is_ext` + nat_eq
dispatch chains. Never `match decl { | DLet → ... | DType → ... | DExt → ... }` — all
three are App arms.

---

## 10. Implementation Order (Bottom-Up, Strict)

Write in this order to satisfy the no-forward-reference constraint:

**Layer 1: Constants + PlanVal primitives**
1. `id_law`, `const2_law`
2. Pre-computed name-nats for builtins and Core.PLAN opcodes
3. `planval_is_nat`, `planval_get_nat`, `planval_is_pin`, `planval_is_app`
4. `cenv_empty`, all `cenv_*` accessors/constructors/updaters (12 functions)
5. `contab_empty`, `contab_lookup`, `contab_insert`

**Layer 2: PLAN value builders**
6. `cg_quote_nat`, `cg_bapp`, `cg_apply`, `cg_ensure_pin`
7. `cg_build_case3_nat`, `cg_build_op2`, `cg_build_reflect_dispatch`
8. `cg_build_constructor_body`, `cg_apply_params`

**Layer 3: AST tag extractors and single-arm helpers**
9. `expr_tag`
10. `cg_is_lam`, `cg_lam_param`, `cg_lam_body`, `cg_evar_name`, `cg_app_fun`, `cg_app_arg`
11. `arm_is_nat`, `arm_is_con`, `arm_is_var` + all `arm_get_*` accessors (9 functions)
12. `decl_is_let`, `decl_is_type`, `decl_is_ext` + all `decl_get_*` accessors (9 functions)

**Layer 4: Recursive AST traversal**
13. `cg_free_var`, `cg_free_app`, `cg_free_lam`, `cg_free_let`, `cg_free_if`,
    `cg_free_match`, `cg_free_pin`
14. `cg_collect_free` (dispatch via expr_tag)
15. `cg_free_vars`
16. `cg_has_var_*` (per-constructor), `cg_has_var`, `cg_body_uses_self`
17. `cg_flatten_lam`

**Layer 5: Sorting utilities**
18. `cg_insert_sorted_pair`, `cg_sort_nat_pairs`
19. `cg_extract_nat_arms`, `cg_extract_wild_arm`

**Layer 6: Nat match compilation**
20. `cg_make_pred_succ_law` (needs cg_free_vars, cg_body_uses_self — mutual with cg_compile_expr)

> **Critical ordering issue:** `cg_make_pred_succ_law` calls `cg_compile_expr`, which
> hasn't been written yet. And `cg_compile_expr` calls `cg_make_pred_succ_law` (indirectly
> via `cg_compile_nat_match` → `cg_make_succ_fn` → `cg_make_wild_succ`).
> **Solution:** Forward-declare `cg_compile_expr` as a top-level `let` that calls itself
> recursively (N(0) self-reference), and pass it as a parameter to functions that need it.
> Use a single `cg_compile_expr` that references itself as N(0) — this is the standard
> restricted dialect recursion pattern.

21. `cg_make_wild_succ`, `cg_make_succ_fn`
22. `cg_build_nat_dispatch`, `cg_compile_nat_match`

**Layer 7: Constructor match compilation**
23. `cg_build_field_arm_law`, `cg_build_tag_chain`
24. `cg_compile_case3_reflect`, `cg_compile_con_match`
25. `cg_compile_fallback_match`

**Layer 8: Lambda lifting**
26. `cg_bind_params`, `cg_make_lifted_env`, `cg_apply_free_vars`
27. `cg_compile_lam_as_law`, `cg_compile_lam_lifted`

**Layer 9: Per-expression compilers**
28. `cg_global_ref`, `cg_var_lookup`, `cg_compile_var`
29. `cg_compile_app`, `cg_compile_enat`, `cg_compile_elet`
30. `cg_build_if`, `cg_compile_if`
31. `cg_compile_match`, `cg_compile_lam`, `cg_compile_pin`
32. `cg_compile_complex` (handles ELam/ELet/EIf/EMatch/EPin via expr_tag chain)
33. `cg_compile_expr` (routes EVar→cg_compile_var, ENat→cg_compile_enat, EApp→cg_compile_app,
                       else→cg_compile_complex)

**Layer 10: Program passes**
34. `name_concat_dot`, `cg_core_plan_opcodes`
35. `cg_register_builtins`, `cg_register_builtin_contab`
36. `cg_register_type_one` (single ConDef), `cg_register_type` (iterates cdefs with tag counter)
37. `cg_pass1` (iterates decls, skips non-DType)
38. `cg_register_ext_one` (single ext item), `cg_register_ext` (iterates items)
39. `cg_pass2`
40. `cg_compile_let_one`, `cg_pass3`
41. `compile_program`

---

## 11. pred_env Danger Zone Summary

| Function | Pattern | Workaround |
|---|---|---|
| Any dispatch on `Expr` | 8 App arms, all bodies use `env`/`ctab`/`hint` | `expr_tag` + nat_eq chain + per-constructor helpers |
| `cg_global_ref` | `match val { \| PNat → ... \| PPin → ... \| _ → ... }` | `planval_is_nat` + `planval_is_pin` chain |
| `cg_flatten_lam` | `match expr { \| ELam → ... \| _ → expr }` — 2 App arms, `expr` in `_` | `cg_is_lam` + `cg_lam_param` + `cg_lam_body` |
| All AST-recursive functions | Multi-arm match on Expr variants | `expr_tag` dispatch + single-arm helpers |
| `arms_have_nat/con` | `match arm { \| ArmNat → True \| _ → recurse(rest) }` | `arm_is_nat` helper |
| Pass 1/2/3 iteration | `match decl { \| DLet \| DType \| DExt }` | `decl_is_*` helpers + nat_eq chain |
| Constructor match build | `match arm_type { multiple con variants }` | Single-arm accessor helpers |
| Nullary-arm nat dispatch | Multiple nullary constructors, bodies ref outer vars | `is_*_stop` nat_eq helper (same pattern as `is_arity_stop`) |

---

## 12. Estimated Size

| Layer | Functions | Lines |
|---|---|---|
| CEnv + PlanVal + constants | ~25 | ~200 |
| PLAN builders + quote/bapp | ~10 | ~120 |
| AST tag extractors + helpers | ~30 | ~250 |
| Recursive AST traversal | ~15 | ~180 |
| Nat match compilation | ~10 | ~160 |
| Con match compilation | ~10 | ~200 |
| Lambda lifting | ~7 | ~100 |
| Per-expression compilers | ~15 | ~220 |
| Program passes | ~12 | ~150 |
| **Total** | **~134** | **~1580** |

Current file: ~1730 lines. After M8.5: ~3310 lines.

---

## 13. Test Strategy (tests/compiler/test_codegen.py)

For each prelude definition, compare `compile_program` output between Python bootstrap and
Gallowglass codegen. Key correctness assertions:

1. `PLaw n (MkPair arity body)` ↔ Python's `L(arity, n, body)`
2. Self-recursive functions have `PNat 0` in the body (not a pin)
3. Nat literal `k ≤ arity` inside law body → quote form `PApp (PNat 0) (PNat k)`
4. Nat globals inside law bodies → quote form
5. Lambda-lifted laws: free vars applied in `env.locals` list order
6. Constructor with n fields: `PLaw name (MkPair n body)` where body = bapp chain
7. All-nullary con match → nat-dispatch tree matching Python's `_build_nat_dispatch`
8. `compile_program` output list has same names and same count as Python

Start with simple definitions (nullary constructors, `pred`, `is_zero`) and add complexity
incrementally. Self-hosting validation (M8.8) is the ultimate end-to-end test.
