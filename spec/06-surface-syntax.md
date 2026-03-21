# Gallowglass Surface Syntax

**Spec version:** 0.1
**Depends on:** SPEC.md

This document is the authoritative formal grammar for Gallowglass surface syntax. The bootstrap compiler's lexer and parser implement this grammar exactly. The self-hosting compiler's lexer and parser must produce identical token streams and ASTs for any valid input.

The grammar is written in PEG (Parsing Expression Grammar) notation:
- `'x'` — literal string
- `[x-y]` — character class
- `x / y` — ordered choice (try x first, then y)
- `x*` — zero or more
- `x+` — one or more
- `[x]` — optional (zero or one)
- `!x` — negative lookahead
- `&x` — positive lookahead
- `~` — any single character

---

## 1. Lexical Grammar

The lexer runs first and produces a token stream. All subsequent passes operate on tokens, never on raw characters.

### 1.1 Whitespace and Comments

```peg
WS      ← (Blank / LineComment / BlockComment)*
Blank   ← [ \t\n\r]+
LineComment  ← '--' (!'\n' ~)* '\n'
BlockComment ← '{-' (!'-}' ~)* '-}'
```

Whitespace is not significant. Indentation carries no syntactic meaning. Block comments do not nest.

### 1.2 Unicode Normalization

The lexer normalizes ASCII alternative operators to their canonical Unicode forms **before** producing tokens. This normalization is applied character-by-character during scanning, before any token boundary decisions.

```
ASCII input   →  Unicode canonical
-----------      ----------------
->            →  →
fn            →  λ   (only when followed by identifier or _)
forall        →  ∀
exists        →  ∃
<-            →  ←
/=            →  ≠
<=            →  ≤   (context: not part of <-)
>=            →  ≥
/             →  ÷   (true division)
//            →  /   (integer division)
```

**Note:** `fn` normalization only applies at expression position (where a lambda is valid). `fn` as an identifier (e.g., a function named `fn_helper`) is not normalized.

**Note:** `/` normalizes to `÷` (true division) and `//` normalizes to `/` (integer division). The lexer scans `//` first (longest match); a lone `/` becomes `÷`. After normalization, `÷` is always true division and `/` is always integer division.

After normalization, all subsequent passes see only canonical Unicode. No ASCII alternative ever appears past the lexer boundary.

### 1.3 Identifiers and Keywords

```peg
LowerChar  ← [a-z]
UpperChar  ← [A-Z]
DigitChar  ← [0-9]
HexChar    ← [0-9a-fA-F]
NameChar   ← LowerChar / UpperChar / DigitChar / '_'

-- snake_case: functions, values, effect operations, row variables, type variables
SnakeName  ← (LowerChar / '_') NameChar*

-- PascalCase: types, effects, modules, constructors
PascalName ← UpperChar NameChar*

-- Qualified module path
QualMod    ← PascalName ('.' PascalName)*

-- Qualified name: module path then snake_case identifier
QualName   ← PascalName ('.' PascalName)* '.' SnakeName

-- Single-character type variables a-q
TypeVar    ← [a-q] !NameChar

-- Single-character row variables r-z
RowVar     ← [r-z] !NameChar
```

**Naming convention enforcement** occurs during name resolution (Pass 3), not in the lexer. The lexer produces `TName` tokens for all identifiers; the resolver checks category-appropriate form and rejects violations with targeted error messages.

### 1.4 Keywords

The following identifiers are reserved and cannot be used as names:

```
mod       let       type      eff       class     instance
match     handle    return    once      fix
pre       post      inv       law
if        then      else
forall    exists
external  use       as        at        with      unqualified
export    depends   modules   version   package
import    from      macro
True      False     Unit      Never
```

Additionally these symbol sequences are reserved:
- `\` — reserved unassigned; compiler error "reserved symbol, not yet assigned"

### 1.5 Literals

```peg
-- Natural number literals
NatLit    ← DecLit / HexLit_
DecLit    ← DigitChar+
HexLit_   ← '0x' HexChar+

-- Text literals (UTF-8, interpolation via #{...})
TextLit   ← '"' TextChar* '"'
TextChar  ← '\\' EscSeq
           / '#{' WS Expr WS '}'   -- interpolation: requires Show instance
           / !'"' !'\\' ~

-- Raw text literals (no interpolation, no escape processing)
RawText   ← 'r"' RawChar* '"'
RawChar   ← !'"' ~

-- Byte literals (raw bytes)
BytesLit  ← 'b"' ByteChar* '"'
ByteChar  ← '\\' EscSeq / !'"' ~

-- Hex byte literals (space-separated byte pairs)
HexBytes  ← 'x"' (WS HexChar HexChar)* WS '"'

-- Escape sequences
EscSeq    ← 'n' / 'r' / 't' / '\\' / '"' / '0'
           / 'x' HexChar HexChar
```

### 1.6 Operators and Punctuation

```peg
-- Unicode canonical operators (after normalization)
Arrow     ← '→'
Lambda    ← 'λ'
Forall    ← '∀'
Exists    ← '∃'
Bind      ← '←'
Compose   ← '·'
Sum       ← '⊕'
Product   ← '⊗'
Top       ← '⊤'
Bottom    ← '⊥'
Empty     ← '∅'
NEq       ← '≠'
LEq       ← '≤'
GEq       ← '≥'
In        ← '∈'
NotIn     ← '∉'
Subset    ← '⊆'
Quote_    ← '`'
Unquote   ← ','   -- only meaningful inside a quotation

-- Numeric operators
Plus      ← '+'
Minus     ← '-'
Times     ← '*'
TrueDiv   ← '÷'    -- true division (ASCII: /)
IntDiv    ← '/'    -- integer division (ASCII: //)
Pow       ← '^'
Pipe      ← '|>'
Concat    ← '++'   -- concatenation (Text, List)

-- Comparison
LT        ← '<'
GT        ← '>'
EqEq      ← '='   -- equality predicate (in contracts)

-- Structural punctuation
Eq_       ← '='   -- spec/impl separator, binding operator
Bar       ← '|'   -- constructors, match arms, contract clauses, handler arms
Colon     ← ':'   -- type annotation
Comma     ← ','
Dot       ← '.'   -- field access, composition (ASCII alt for ·)
DotDot    ← '..'  -- reserved
At        ← '@'   -- pin binding
AtBang    ← '@!'  -- compiler pin (Glass IR only)
Hash      ← '#'   -- attribute prefix
Cons      ← '::'  -- list cons (patterns and expressions)
LParen    ← '('
RParen    ← ')'
LBrace    ← '{'
RBrace    ← '}'
LBrack    ← '['
RBrack    ← ']'
Backtick  ← '`'
Semicolon ← ';'   -- reserved
Question  ← '?'   -- reserved
Bang      ← '!'   -- reserved (appears in @! but not standalone)
FatArrow  ← '=>'  -- typeclass constraints
```

### 1.7 Pin Literals

```peg
-- PinId literal: only valid in Glass IR and debugger output
-- Not valid in source programs
PinLit    ← 'pin#' HexChar+
```

---

## 2. Token Types

The lexer produces the following token types. All subsequent passes pattern-match on these.

```gallowglass
Token =
  | TSnake    Text          -- snake_case identifier (values, functions)
  | TPascal   Text          -- PascalCase identifier (types, effects, modules)
  | TTypeVar  Char          -- single char a-q (type variable)
  | TRowVar   Char          -- single char r-z (row variable)
  | TKeyword  Keyword       -- reserved word (see 1.4)
  | TNat      Nat           -- numeric literal
  | TText     Text          -- text literal (interpolation already resolved)
  | TRawText  Text          -- raw text literal
  | TBytes    Bytes         -- byte literal
  | THexBytes Bytes         -- hex byte literal
  | TOp       UnicodeOp     -- canonical Unicode operator
  | TPunct    Punct         -- structural punctuation
  | TPinLit   Text          -- pin#hexhash (Glass IR only)
  | TEOF                    -- end of input
```

The `TText` token produced by the lexer has interpolation fragments pre-resolved to a sequence of `(literal_text, expr_token_stream)` pairs. The parser handles the joining.

---

## 3. Top-Level Structure

```peg
Program     ← WS (TopDecl WS)* EOF

TopDecl     ← ModDecl
            / PackageDecl
            / LetDecl
            / TypeDecl
            / EffDecl
            / ClassDecl
            / InstanceDecl
            / ExternalMod
            / UseDecl
            / ExportDecl
            / MacroDecl
```

---

## 4. Module Grammar

```peg
ModDecl     ← 'mod' WS QualMod WS '{' WS ModBody WS '}'

ModBody     ← (ModItem WS)*

ModItem     ← ExportDecl
            / UseDecl
            / LetDecl
            / TypeDecl
            / EffDecl
            / ClassDecl
            / InstanceDecl
            / ExternalMod
            / MacroDecl

ExportDecl  ← 'export' WS '{' WS ExportList WS '}'

ExportList  ← ExportItem (WS ',' WS ExportItem)*

ExportItem  ← 'module' WS QualMod              -- re-export whole module
            / 'instance' WS PascalName WS AtomType+ -- named instance
            / 'instances'                          -- all instances in module
            / QualName                             -- specific definition
            / PascalName                           -- type or effect

UseDecl     ← 'use' WS QualMod WS UseSpec

UseSpec     ← 'unqualified' WS '{' WS UseList WS '}'  -- unqualified import
            / '{' WS UseList WS '}'                    -- qualified import
            / ε                                        -- import module name only

UseList     ← UseItem (WS ',' WS UseItem)*

UseItem     ← 'instance' WS PascalName WS AtomType+  -- explicit instance import
            / 'instances'                               -- all instances
            / '(' WS InfixOp WS ')'                   -- operator
            / SnakeName                                -- value
            / PascalName                               -- type or effect

PackageDecl ← 'package' WS QualMod WS '{' WS PackageBody WS '}'

PackageBody ← (PackageField WS)*

PackageField ← 'version'  WS '=' WS TextLit
             / 'depends'  WS '{' WS DepList  WS '}'
             / 'modules'  WS '{' WS ModList  WS '}'

DepList     ← DepItem (WS ',' WS DepItem)*
DepItem     ← QualMod WS 'at' WS PinLit

ModList     ← QualMod (WS ',' WS QualMod)*
```

---

## 5. Type Grammar

Types are parsed at four precedence levels, from lowest to highest binding:
- `ForallType` / `ConstrainedType` — quantifiers and constraints bind loosest
- `FunType` — function arrows
- `SumProdType` — `⊕` and `⊗` binary type operators
- `AppType` — type application (juxtaposition)
- `AtomType` — atomic types (parenthesized, constructors, variables, records)

```peg
Type        ← ForallType
            / ConstrainedType
            / FunType

-- Universally quantified type: ∀ a b. Type
ForallType  ← Forall WS TVarList '.' WS Type
TVarList    ← TypeVar (WS TypeVar)*

-- Constrained type: Ord a => Type  (typeclass constraints in signatures)
ConstrainedType ← Constraint (WS ',' WS Constraint)* WS '=>' WS Type
Constraint      ← PascalName (WS AtomType)+

-- Function type: right-associative
FunType     ← EffType (WS Arrow WS EffType)*

-- Effect-annotated type: {row} ReturnType
EffType     ← '{' WS EffRow WS '}' WS SumProdType
            / SumProdType

EffRow      ← EffEntry (WS ',' WS EffEntry)* (WS '|' WS RowVar)?
            / RowVar
            / ε                                -- empty row: pure

EffEntry    ← PascalName (WS AtomType)*       -- effect with type args

-- Sum and product type operators (left-associative)
SumProdType ← AppType (WS (Sum / Product) WS AppType)*

-- Type application: left-associative juxtaposition (e.g., List a, Map k v)
AppType     ← AtomType (WS !Arrow !'=>' AtomType)*

AtomType    ← '(' WS Type (WS ',' WS Type)+ WS ')'   -- tuple type: (a, b, ...)
            / '(' WS Type WS ')'                       -- parenthesized type
            / RecordType                                -- { field : Type, ... }
            / RefinedType                               -- (name : Type | Pred)
            / DictType                                  -- Glass IR only
            / Top                                       -- ⊤
            / Bottom                                    -- ⊥
            / Empty                                     -- ∅ (empty row/collection)
            / PascalName                                -- type constructor
            / QualName                                  -- qualified type
            / TypeVar                                   -- type variable

-- Tuple type: (A, B) is syntactic sugar for A ⊗ B
-- (A, B, C) is syntactic sugar for A ⊗ B ⊗ C (left-associated)

-- Refined type: (name : Type | Predicate)
RefinedType ← '(' WS SnakeName WS ':' WS Type WS '|' WS Pred WS ')'

-- Record type: { field : Type, ... }
RecordType  ← '{' WS RecordFields WS '}'
RecordFields ← RecordField (WS ',' WS RecordField)*
RecordField  ← SnakeName WS ':' WS Type

-- Explicit dictionary type (Glass IR elaboration of typeclass constraint)
-- Not valid in source programs
DictType    ← '(' WS SnakeName WS ':' WS PascalName WS AtomType* WS ')'
```

The `AppType` negative lookahead `!Arrow !'=>'` prevents consuming `→` or `=>` as a type argument, ensuring `A B → C` parses as `(A B) → C` and `Ord a => ...` is not consumed as application.

---

## 6. Expression Grammar

Expressions are listed from lowest to highest precedence.

```peg
Expr        ← LetExpr
            / PinExpr
            / LambdaExpr
            / FixExpr
            / DoExpr
            / IfExpr
            / AnnExpr

-- Let: sequential binding in body
LetExpr     ← 'let' WS Pattern WS [':' WS Type WS] '=' WS Expr WS Expr

-- Programmer pin: computed once, referenced in body
-- Pin bindings have scope (like let): @name = value  continuation
PinExpr     ← '@' SnakeName
              (WS ':' WS Type)?
              WS '=' WS Expr WS Expr
            / '@' SnakeName WS Bind WS Expr WS Expr
            -- effectful pin bind: @name ← effectful_expr  continuation

-- Lambda
LambdaExpr  ← Lambda WS ArgPat+ WS Arrow WS Expr
ArgPat      ← AtomPat

-- Anonymous recursion: fix λ self args... → body
FixExpr     ← 'fix' WS LambdaExpr

-- Effectful bind: name ← effectful_expr  rest_expr
DoExpr      ← SnakeName WS Bind WS Expr WS Expr

-- Conditional
IfExpr      ← 'if' WS Expr WS 'then' WS Expr WS 'else' WS Expr

-- Type annotation
AnnExpr     ← OpExpr (WS ':' WS Type)?

-- Binary operators (left-associative at each level)
-- Precedence from lowest to highest:

OpExpr      ← ComposeExpr (WS Pipe WS ComposeExpr)*           -- |>

ComposeExpr ← OrExpr (WS Compose WS OrExpr)*                  -- ·

OrExpr      ← AndExpr (WS '∨' WS AndExpr)*

AndExpr     ← CmpExpr (WS '∧' WS CmpExpr)*

CmpExpr     ← ConcatExpr (WS CmpOp WS ConcatExpr)*
CmpOp       ← '=' / NEq / LEq / GEq / '<' / '>' / In / NotIn / Subset

ConcatExpr  ← AddExpr (WS Concat WS AddExpr)*                 -- ++

AddExpr     ← MulExpr (WS AddOp WS MulExpr)*
AddOp       ← Plus / Minus

MulExpr     ← ConsExpr (WS MulOp WS ConsExpr)*
MulOp       ← Times / TrueDiv / IntDiv / 'mod' / Pow

-- Cons: right-associative
ConsExpr    ← UnaryExpr (WS Cons WS ConsExpr)?                -- ::

UnaryExpr   ← '-' WS UnaryExpr                                -- negation
            / '¬' WS UnaryExpr                                -- logical not
            / WithExpr

-- Explicit dictionary override: expr with (dict) args...
WithExpr    ← AppExpr (WS 'with' WS '(' WS Expr WS ')' WS AppExpr*)?

-- Function application (left-associative, highest binary precedence)
-- HandleExpr and MatchExpr are at this level because they are
-- brace-delimited (self-terminating) and can be applied directly:
--   match x { ... } y
--   handle comp { ... } initial_state
AppExpr     ← AppHead (WS AppArg)*

AppHead     ← HandleExpr
            / MatchExpr
            / AtomExpr

AppArg      ← DictArg               -- [dict] (Glass IR only)
            / HandleExpr
            / MatchExpr
            / AtomExpr

-- Effect handler
HandleExpr  ← 'handle' WS Expr WS '{' WS HandlerArm+ WS '}'

HandlerArm  ← '|' WS 'return' WS Pattern WS Arrow WS Expr WS
            / '|' WS 'once'? WS SnakeName WS AtomPat* WS SnakeName WS Arrow WS Expr WS
            -- op_name pattern-args... continuation → body

-- Match
MatchExpr   ← 'match' WS Expr WS '{' WS MatchArm+ WS '}'

MatchArm    ← '|' WS Pattern (WS 'if' WS AppExpr)? WS Arrow WS Expr WS

-- Explicit dictionary application (Glass IR elaboration)
DictArg     ← '[' WS Expr WS ']'

-- Atomic expressions
AtomExpr    ← '(' WS Expr (WS ',' WS Expr)+ WS ')'   -- tuple: (a, b, ...)
            / '(' WS Expr WS ')'                       -- parenthesized expression
            / '(' WS ')'                                -- unit value
            / ListExpr                                  -- [a, b, c]
            / RecordExpr
            / RecordUpdate
            / CompilerPin                 -- Glass IR only
            / PinRef                      -- Glass IR only
            / QuoteExpr
            / NatLit
            / TextLit
            / RawText
            / BytesLit
            / HexBytes
            / Top                         -- ⊤ as the unit value
            / QualName                    -- must try before SnakeName/PascalName
            / SnakeName
            / PascalName                  -- constructor

-- Tuple expression: (a, b) is syntactic sugar for (a, b) product value
-- (a, b, c) is syntactic sugar for nested pairs, left-associated

-- List literal: desugars to nested Cons/Nil
ListExpr    ← '[' WS ']'                                    -- empty list: Nil
            / '[' WS Expr (WS ',' WS Expr)* WS ']'         -- [a, b, c]

-- Record construction
RecordExpr  ← '{' WS RecordInit (WS ',' WS RecordInit)* WS '}'
RecordInit  ← SnakeName WS '=' WS Expr

-- Functional update
RecordUpdate ← AtomExpr WS '{' WS RecordInit (WS ',' WS RecordInit)* WS '}'

-- Compiler-introduced pin (Glass IR only, not valid in source)
CompilerPin ← '@!' '[' PinLit ']' WS SnakeName
              (WS ':' WS Type)?
              WS '=' WS Expr

-- Pin reference by hash (Glass IR only)
PinRef      ← PinLit

-- Homoiconic quotation
QuoteExpr   ← Backtick '(' WS QuoteBody WS ')'
QuoteBody   ← UnquoteSplice / QuoteAtom*
UnquoteSplice ← Unquote WS AtomExpr
QuoteAtom   ← '(' WS QuoteBody WS ')' / !RParen ~
```

### 6.1 PinExpr vs AtomExpr Disambiguation

Pin expressions (`@name = expr  body`) bind a name with a continuation scope, like `let`. They are parsed at the `Expr` level, not `AtomExpr`, because they introduce a binding scope over the subsequent expression.

The `@` token in `AtomExpr` position is always an error — pin bindings require a body. In Glass IR, `@!` (compiler pins) appear in declaration position (`CompilerPin` in Glass IR extensions), not as standalone atomic expressions.

### 6.2 HandleExpr and MatchExpr at AppExpr

`handle` and `match` expressions are brace-delimited and self-terminating. Placing them at the `AppExpr` level (rather than the top `Expr` level) means they can appear as function arguments and can be applied directly:

```gallowglass
-- Handler applied to initial state
handle computation { | return x → λ s → (x, s) | ... } s₀

-- Match result applied to a function
f (match x { | Some v → v | None → 0 })
```

This follows the same principle as Haskell's `do`-in-argument and Rust's block expressions.

---

## 7. Pattern Grammar

```peg
Pattern     ← OrPat

OrPat       ← AsPat (WS '|' WS AsPat)*

AsPat       ← ConsPat (WS 'as' WS SnakeName)?

-- Constructor pattern: Pascal-leading means constructor application
ConsPat     ← PascalName (WS AtomPat)*        -- Constructor patterns
            / AtomPat

AtomPat     ← '(' WS Pattern (WS ',' WS Pattern)+ WS ')'   -- tuple pattern
            / '(' WS Pattern WS ')'            -- parenthesized pattern
            / ListPat                           -- [p1, p2, ...]
            / RecordPat
            / WildPat
            / LitPat
            / VarPat

WildPat     ← '_' !NameChar

LitPat      ← NatLit
            / TextLit
            / BytesLit

-- Variable pattern: a snake_case name that is not a keyword
VarPat      ← SnakeName

ListPat     ← '[' WS ']'                                    -- empty list: Nil
            / '[' WS Pattern (WS ',' WS Pattern)* WS ']'   -- [p1, p2, p3]

RecordPat   ← '{' WS RecordPatField (WS ',' WS RecordPatField)* WS '}'
RecordPatField ← SnakeName (WS '=' WS Pattern)?   -- omit = for punning
```

### 7.1 VarPat Disambiguation

`VarPat` is simply a `SnakeName`. The disambiguation between variable patterns and constructor patterns is structural: constructors are `PascalName` (uppercase-leading), variables are `SnakeName` (lowercase-leading). No lookahead is needed because the two identifier classes are lexically disjoint.

A `VarPat` that shadows an in-scope binding produces a warning. A `VarPat` that matches a keyword is rejected at the keyword check (§1.4), not here.

### 7.2 Cons Pattern

Cons patterns (`h :: t`) are parsed via `ConsPat` using the `::` operator embedded in pattern syntax:

```peg
ConsPatInfix ← AtomPat WS '::' WS Pattern    -- right-associative
```

This is handled as sugar: `h :: t` desugars to `Cons h t`. The `ConsPat` rule in §7 handles this when the constructor `Cons` is written explicitly. The infix `::` form is resolved during desugaring.

---

## 8. Declaration Grammar

```peg
-- Function / value definition
LetDecl     ← 'let' WS SnakeName WS ':' WS Type WS
              ContractClause*
              '=' WS Expr

ContractClause ← '|' WS ContractKind WS ProofStatus WS '(' WS Pred WS ')' WS

ContractKind ← 'pre' / 'post' / 'inv' / 'law'

ProofStatus  ← 'Proven'
             / 'Deferred' WS '(' WS DeferralReason WS ')'
             / 'Refuted'
             / 'Checked'
             / 'Violated'

DeferralReason ← 'NonLinear' / 'HigherOrder' / 'Recursive'
               / 'NoSolver'  / 'SolverTimeout' / 'OutsideTheory'
               / 'Guard' / 'InfiniteType' / 'AbstractType' / 'OutOfBounds'

-- Algebraic type declaration
TypeDecl    ← 'type' WS PascalName (WS TypeVar)* WS '='
              WS Constructor (WS '|' WS Constructor)*
            / 'type' WS PascalName (WS TypeVar)* WS ':' WS 'builtin'  -- builtin type
            / 'type' WS PascalName (WS TypeVar)* WS '=' WS Type       -- alias

Constructor ← PascalName (WS AtomType)*

-- Record type declaration
RecordDecl  ← 'type' WS PascalName (WS TypeVar)* WS '='
              WS '{' WS RecordField (WS ',' WS RecordField)* WS '}'

-- Effect declaration
EffDecl     ← 'eff' WS PascalName (WS TypeVar)* WS '{' WS EffOp* WS '}'

EffOp       ← SnakeName WS ':' WS Type WS

-- Typeclass declaration
ClassDecl   ← 'class' WS ClassConstraint? PascalName WS TypeVar+ WS
              '{' WS ClassMember* WS '}'

ClassConstraint ← Constraint (WS ',' WS Constraint)* WS '=>' WS
Constraint      ← PascalName (WS AtomType)+

ClassMember ← SnakeName WS ':' WS Type WS
              ContractClause*
              ('=' WS Expr WS)?               -- optional default implementation
            / 'law' WS SnakeName WS ':' WS Pred WS

-- Instance declaration
InstanceDecl ← 'instance' WS ClassConstraint? PascalName WS AtomType+ WS
               '{' WS InstanceMember* WS '}'

InstanceMember ← SnakeName WS '=' WS Expr WS

-- External module (FFI)
ExternalMod ← 'external' WS 'mod' WS PascalName ('.' PascalName)* WS
              '{' WS ExtOp* WS '}'

ExtOp       ← 'type' WS PascalName WS ':' WS ExtTypeSpec WS  -- opaque type
            / SnakeName WS ':' WS Type WS                      -- operation

ExtTypeSpec ← 'Opaque' ('[' Variance ']')?
            / 'builtin'
Variance    ← '+' / '-' / '~'

-- Macro declaration
MacroDecl   ← 'macro' WS SnakeName WS '(' WS MacroParams WS ')'
              (WS ':' WS EffSpec)?                             -- declared effect footprint
              WS '=' WS QuoteExpr

MacroParams ← SnakeName (WS SnakeName)*
EffSpec     ← '{' WS EffRow WS '}'
```

---

## 9. Predicate Grammar

The contract predicate language targets the Tier 0/1 discharge procedures. The grammar encodes the decidability boundary: predicates in `LinArithPred` and `BoolPred` are candidates for Tier 1 static discharge; everything else falls to `ExprPred` and becomes `Deferred`.

```peg
Pred        ← QuantPred
            / CompoundPred
            / LinArithPred
            / BoolPred
            / ExprPred            -- fallback: arbitrary expr → always Deferred

-- Quantified predicates
QuantPred   ← Forall WS QuantVarList '.' WS Pred
            / Exists WS QuantVarList '.' WS Pred
QuantVarList ← SnakeName (WS ',' WS SnakeName)*

-- Compound predicates (precedence-climbing to avoid left recursion)
CompoundPred ← ImplPred

ImplPred    ← DisjPred (WS '=>' WS DisjPred)*

DisjPred    ← ConjPred (WS '∨' WS ConjPred)*

ConjPred    ← NegPred (WS '∧' WS NegPred)*

NegPred     ← '¬' WS NegPred
            / AtomPred

AtomPred    ← '(' WS Pred WS ')'
            / LinArithPred
            / BoolPred

-- Linear arithmetic predicates (Tier 1 decidable)
LinArithPred ← LinExpr WS ArithRel WS LinExpr

ArithRel    ← '=' / NEq / '<' / LEq / '>' / GEq

LinExpr     ← LinTerm (WS ('+' / '-') WS LinTerm)*

LinTerm     ← NatLit
            / SnakeName
            / NatLit WS '*' WS SnakeName
            / 'length'       WS SnakeName    -- list/text/bytes length
            / 'byte_length'  WS SnakeName    -- bytes/text byte length
            / 'codepoint_count' WS SnakeName

-- Boolean predicates (Tier 0/1 decidable)
BoolPred    ← 'True' / 'False'
            / SnakeName                      -- boolean variable
            / '(' WS BoolPred WS ')'

-- Arbitrary expression (always Deferred)
ExprPred    ← Expr
```

---

## 10. Infix Operators

Operators in backtick form (infix application):

```peg
InfixOp     ← '`' SnakeName '`'     -- e.g., x `elem` xs
            / '(' WS UnicodeOp WS ')'

UnicodeOp   ← Arrow / Lambda / Forall / Exists / Bind / Compose
            / Sum / Product / Top / Bottom / Empty
            / NEq / LEq / GEq / In / NotIn / Subset
            / Plus / Minus / Times / TrueDiv / IntDiv / Pow
            / Pipe / EqEq / LT / GT / Concat / Cons
```

---

## 11. Attribute Syntax

Attributes annotate declarations with metadata. They appear immediately before the declaration they annotate.

```peg
Attribute   ← '#[' WS AttrContent WS ']'

AttrContent ← 'jet' WS ':' WS JetSpec         -- jet registration
            / 'pin'                             -- force pinning
            / 'deprecated' (WS ':' WS TextLit)?
            / SnakeName (WS ':' WS AttrVal)?   -- generic attribute

JetSpec     ← 'registry' WS '=' WS TextLit
              (',' WS 'version' WS '=' WS NatLit)?

AttrVal     ← TextLit / NatLit / SnakeName
```

---

## 12. Glass IR Extensions

The following constructs are valid in Glass IR but not in source programs. The parser must accept them when operating in Glass IR mode. In source mode, these are parse errors.

```peg
-- Fragment metadata header
FragmentMeta ← '--' WS 'Snapshot:' WS PinLit WS
               '--' WS 'Source:' WS QualName ':' NatLit ':' NatLit WS
               ('--' WS 'Budget:' WS NatLit WS 'tokens' WS)?

-- Compiler-introduced pin (distinguishes compiler from programmer pins)
CompilerPin ← '@!' '[' PinLit ']' WS SnakeName
              (WS ':' WS Type)?
              WS '=' WS Expr

-- Pin reference by hash
PinRef      ← PinLit

-- Explicit dictionary application
DictArg     ← '[' WS Expr WS ']'

-- Grouped pin block (mutually recursive SCC)
GroupedPin  ← '@!' '[' PinLit ']' WS '{' WS
              (TypeDecl / LetDecl)+ WS
              '}'

-- Pending effect at boundary
PendingDecl ← 'let' WS SnakeName WS ':' WS PendingType WS '=' WS PendingLit
PendingType ← 'Pending' WS AtomType WS AtomType
PendingLit  ← '{' WS
              'effect' WS '=' WS Expr WS ',' WS
              'cont'   WS '=' WS Expr WS
              '}'

-- Traced value with reduction history
TraceDecl   ← 'let' WS SnakeName WS ':' WS TraceType WS '=' WS TraceLit
TraceType   ← 'Trace' WS AtomType
TraceLit    ← '{' WS
              'value'  WS '=' WS Expr  WS ',' WS
              'steps'  WS '=' WS '[' WS (Reduction (',' WS Reduction)*)? WS ']' WS ',' WS
              'pin'    WS '=' WS PinLit WS ',' WS
              'source' WS '=' WS SourceSpan WS
              '}'

Reduction   ← '{' WS
              'from' WS '=' WS Expr WS ',' WS
              'to'   WS '=' WS Expr WS ',' WS
              'rule' WS '=' WS ReductionRule WS
              '}'

ReductionRule ← 'Beta' / 'Delta' / 'Iota' / 'Handler' / 'Contract' / 'Pin'

SourceSpan  ← QualName ':' NatLit ':' NatLit (':' NatLit ':' NatLit)?
```

---

## 13. Well-Formedness Constraints

These constraints are checked during name resolution and type checking, not by the PEG grammar.

### 13.1 Naming

- Every function/value name must match `[a-z_][a-z0-9_]*`
- Every type/effect/constructor name must match `[A-Z][A-Za-z0-9]*`
- `TypeVar` is exactly one character in `[a-q]`
- `RowVar` is exactly one character in `[r-z]`
- Violations produce a specific error naming the offending identifier and the expected form

### 13.2 Contracts

- `pre` clauses must precede `post` clauses
- `inv` expands to both a `pre` and `post` clause with the same predicate
- `Proven` status is only valid if the predicate falls within `LinArithPred` or `BoolPred` and was discharged by Tier 0/1 procedures
- `Refuted` status is a compile error — no executable code follows
- A contract that is a syntactic recapitulation of the implementation body should trigger the tautology detector warning

### 13.3 Effects

- `Abort` must never appear in an effect row
- `External` must appear in the row of any function whose body calls an `external mod` operation
- Row variables must be distinct from type variables in the same quantifier scope
- Handler arms must cover the `return` case and at least one operation case

### 13.4 Exhaustiveness

Pattern matches must be exhaustive (see `spec/03-exhaustiveness.md`). When the checker cannot verify exhaustiveness, a catch-all `| _ →` arm is required. The compiler error names the specific `DeferralReason`.

### 13.5 Modules

- Module dependency graph must be acyclic
- All names in a module body must be in scope (defined locally, imported via `use`, or from `external mod`)
- Mutual recursion is bounded by module — no cross-module mutual recursion
- Export list must only name definitions that exist in the module

### 13.6 Macros

- Macros must declare an effect signature if their expansion introduces effects
- Macro expansion is always visible in Glass IR — unexpanded macros never appear in compiled output
- Macro names follow `snake_case` convention

### 13.7 Glass IR Mode

In Glass IR mode (activated by the `FragmentMeta` header or explicit flag):

- `CompilerPin`, `PinRef`, `DictArg`, `GroupedPin`, `PendingDecl`, `TraceDecl` are valid
- All names must be fully qualified — no bare unqualified names from external modules
- No `use` directives permitted
- All typeclass constraints must appear as explicit `DictType` arguments

---

## 14. Operator Precedence Table

From lowest to highest:

| Level | Operators | Associativity |
|-------|-----------|---------------|
| 1     | `\|>`     | Left          |
| 2     | `·`       | Right         |
| 3     | `∨`       | Left          |
| 4     | `∧`       | Left          |
| 5     | `= ≠ < ≤ > ≥ ∈ ∉ ⊆` | Non-associative |
| 6     | `++`      | Right         |
| 7     | `+ -`     | Left          |
| 8     | `* ÷ / mod ^` | Left      |
| 9     | `::`      | Right         |
| 10    | Unary `- ¬` | —           |
| 11    | `with`    | —             |
| 12    | Function application | Left |

Parentheses override precedence at all levels. The `=` in expression position (comparison) and the `=` as the spec/implementation separator are disambiguated by syntactic position — the separator `=` never appears inside an expression context.

---

## 15. Complete Example

A well-formed Gallowglass module demonstrating the major syntactic forms:

```gallowglass
mod Data.Csv {

  use Core.Types  { List, Result, Option }
  use Core.Text   { Text }
  use Core.Bytes  { Bytes }
  use Core.IO     { IO }
  use Core.Exn    { Exn }
  use Core.Eq     { Eq }

  export {
    CsvError, Row, Table,
    parse_row, load, try_load,
    instance Show CsvError
  }

  -- Types

  type CsvError =
    | ParseError  Text
    | SchemaError Text
    | IOError     Text

  type Row   = List Text
  type Table = List Row

  -- Typeclass instances

  instance Show CsvError {
    show = λ e → match e {
      | ParseError  msg → "parse error: " ++ msg
      | SchemaError msg → "schema error: " ++ msg
      | IOError     msg → "io error: " ++ msg
    }
  }

  -- Pure parser

  let parse_row : Text → {Exn CsvError | r} Row
    | pre  Proven   (byte_length text > 0)
    | post Deferred(NoSolver) (length result ≥ 1)
    = λ text →
        @fields = Core.Text.split "," text
        map Core.Text.trim fields

  -- Effectful loader

  let load : (path : Text | byte_length path > 0)
           → {IO, Exn CsvError | r} Table
    | post Deferred(NoSolver) (length result ≥ 0)
    = λ path →
        @raw ← Core.IO.read_file path
        @lines = Core.Text.split "\n" (Core.Text.from_bytes raw)
        map parse_row lines

  -- Handler: convert Exn to Result

  let try_load : (path : Text | byte_length path > 0)
               → {IO | r} (Result Table CsvError)
    = λ path →
        handle (load path) {
          | return t   → Ok t
          | raise  e k → Err e
        }

  -- Explicit dictionary override

  let sort_desc : Ord a => List a → List a
    = λ xs → sort with (Ord.reverse) xs

  -- Anonymous recursion with fix

  let count_down : Nat → List Nat
    = λ n →
        fix λ self m → match m {
          | 0 → [0]
          | k → k :: self (k - 1)
        } n

  -- Tuple values

  let swap : ∀ a b. (a, b) → (b, a)
    = λ (x, y) → (y, x)

  -- Macro

  macro expect_columns (table n) : {Exn CsvError | r}
    = `(match ,table {
          | []        → Exn.raise (SchemaError "empty table")
          | (r :: _)  → if length r ≠ ,n
                         then Exn.raise (SchemaError "wrong column count")
                         else ,table
        })

}
```

This example is also a test case for the bootstrap compiler. It must parse without error, type-check without error (under the restricted dialect), and produce correct PLAN output.

---

## 16. Revision Log

| Issue | Resolution |
|-------|------------|
| Code block fencing lost after §1.6 | All grammar rules now in fenced `peg` or `gallowglass` blocks |
| Type grammar missing `AppType` level | Inserted `SumProdType` and `AppType` between `EffType` and `AtomType` |
| `ProgrammerPin` at `AtomExpr` level | Moved to `Expr` level as `PinExpr` — pin bindings have scope like `let` |
| `RecordType` not reachable from `AtomType` | Added `RecordType` to `AtomType` alternatives |
| `VarPat` wrong `!WS` lookahead | Removed; disambiguation is structural (PascalCase vs snake_case) |
| `macro` and `at` missing from keywords | Added to keyword list (§1.4) |
| Normalization table missing `/` → `÷` rule | Added with explanation of longest-match `//` vs `/` |
| No `TupleExpr` for `(a, b)` values | Added tuple syntax to `AtomExpr` and `AtomType` |
| No `ConstrainedType` for `Ord a =>` | Added `ConstrainedType` to type grammar (§5) |
| `HandleExpr`/`MatchExpr` at `Expr` level | Moved to `AppExpr` level — brace-delimited, can be applied directly |
| No `FixExpr` for `fix λ …` | Added `FixExpr` at `Expr` level |
| No `WithDict` for explicit dictionary override | Added `WithExpr` between `UnaryExpr` and `AppExpr` |
| No `ListExpr` for `[a, b, c]` literals | Added `ListExpr` to `AtomExpr`, `ListPat` to `AtomPat` |
| No `Concat` operator (`++`) | Added `++` token and `ConcatExpr` precedence level |
| No `Cons` operator (`::`) | Added `::` token and `ConsExpr` precedence level |
| `CompoundPred` left-recursive | Rewritten as precedence-climbing (`ImplPred` → `DisjPred` → `ConjPred` → `NegPred`) |
| `ProductType`/`SumType` left-recursive through `AtomType` | Replaced with `SumProdType` level using iterative parse |
| Operator precedence table missing levels | Added `++` (level 6), `::` (level 9), `with` (level 11); renumbered |
| `FatArrow` (`=>`) not in punctuation | Added to §1.6 |
| `builtin` type declaration form missing | Added `type Name : builtin` alternative to `TypeDecl` |
| Precedence table was unformatted plain text | Rendered as proper markdown table |
| Well-formedness §13 subsections unformatted | Restored markdown heading and list formatting |
