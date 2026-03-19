# Gallowglass Surface Syntax

**Spec version:** 0.1
**Depends on:** SPEC.md

This document is the authoritative formal grammar for Gallowglass surface syntax. The bootstrap compiler's lexer and parser implement this grammar exactly. The self-hosting compiler's lexer and parser must produce identical token streams and ASTs for any valid input.

The grammar is written in PEG (Parsing Expression Grammar) notation:
- `'x'` \u2014 literal string
- `[x-y]` \u2014 character class
- `x / y` \u2014 ordered choice (try x first, then y)
- `x*` \u2014 zero or more
- `x+` \u2014 one or more
- `[x]` \u2014 optional (zero or one)
- `!x` \u2014 negative lookahead
- `&x` \u2014 positive lookahead
- `~` \u2014 any single character

---

## 1. Lexical Grammar

The lexer runs first and produces a token stream. All subsequent passes operate on tokens, never on raw characters.

### 1.1 Whitespace and Comments

```peg
WS      \u2190 (Blank / LineComment / BlockComment)*
Blank   \u2190 [ \t\n\r]+
LineComment  \u2190 '--' (!'\n' ~)* '\n'
BlockComment \u2190 '{-' (!'-}' ~)* '-}'
```

Whitespace is not significant. Indentation carries no syntactic meaning. Block comments do not nest.

### 1.2 Unicode Normalization

The lexer normalizes ASCII alternative operators to their canonical Unicode forms **before** producing tokens. This normalization is applied character-by-character during scanning, before any token boundary decisions.

```
ASCII input   \u2192  Unicode canonical
-----------      ----------------
->            \u2192  \u2192
fn            \u2192  \u03bb   (only when followed by identifier or _)
forall        \u2192  \u2200
exists        \u2192  \u2203
<-            \u2192  \u2190
/=            \u2192  \u2260
<=            \u2192  \u2264   (context: not part of <-)
>=            \u2192  \u2265
//            \u2192  /   (integer division; distinct from / true division)
```

**Note:** `fn` normalization only applies at expression position (where a lambda is valid). `fn` as an identifier (e.g., a function named `fn_helper`) is not normalized.

After normalization, all subsequent passes see only canonical Unicode. No ASCII alternative ever appears past the lexer boundary.

### 1.3 Identifiers and Keywords

```peg
LowerChar  \u2190 [a-z]
UpperChar  \u2190 [A-Z]
DigitChar  \u2190 [0-9]
HexChar    \u2190 [0-9a-fA-F]
NameChar   \u2190 LowerChar / UpperChar / DigitChar / '_'

-- snake_case: functions, values, effect operations, row variables, type variables
SnakeName  \u2190 (LowerChar / '_') NameChar*

-- PascalCase: types, effects, modules, constructors
PascalName \u2190 UpperChar NameChar*

-- Qualified module path
QualMod    \u2190 PascalName ('.' PascalName)*

-- Qualified name: module path then snake_case identifier
QualName   \u2190 PascalName ('.' PascalName)* '.' SnakeName

-- Single-character type variables a-q
TypeVar    \u2190 [a-q] !NameChar

-- Single-character row variables r-z
RowVar     \u2190 [r-z] !NameChar
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
external  use       as        with      unqualified
export    depends   modules   version   package
import    from
True      False     Unit      Never
```

Additionally these symbol sequences are reserved:
- `\` \u2014 reserved unassigned; compiler error "reserved symbol, not yet assigned"

### 1.5 Literals

```peg
-- Natural number literals
NatLit    \u2190 DecLit / HexLit_
DecLit    \u2190 DigitChar+
HexLit_   \u2190 '0x' HexChar+

-- Text literals (UTF-8, interpolation via #{...})
TextLit   \u2190 '"' TextChar* '"'
TextChar  \u2190 '\\' EscSeq
           / '#{' WS Expr WS '}'   -- interpolation: requires Show instance
           / !'"' !'\\' ~

-- Raw text literals (no interpolation, no escape processing)
RawText   \u2190 'r"' RawChar* '"'
RawChar   \u2190 !'"' ~

-- Byte literals (raw bytes)
BytesLit  \u2190 'b"' ByteChar* '"'
ByteChar  \u2190 '\\' EscSeq / !'"' ~

-- Hex byte literals (space-separated byte pairs)
HexBytes  \u2190 'x"' (WS HexChar HexChar)* WS '"'

-- Escape sequences
EscSeq    \u2190 'n' / 'r' / 't' / '\\' / '"' / '0'
           / 'x' HexChar HexChar
```

### 1.6 Operators and Punctuation

```peg
-- Unicode canonical operators (after normalization)
Arrow     \u2190 '\u2192'
Lambda    \u2190 '\u03bb'
Forall    \u2190 '\u2200'
Exists    \u2190 '\u2203'
Bind      \u2190 '\u2190'
Compose   \u2190 '\u00b7'
Sum       \u2190 '\u2295'
Product   \u2190 '\u2297'
Top       \u2190 '\u22a4'
Bottom    \u2190 '\u22a5'
Empty     \u2190 '\u2205'
NEq       \u2190 '\u2260'
LEq       \u2190 '\u2264'
GEq       \u2190 '\u2265'
In        \u2190 '\u2208'
NotIn     \u2190 '\u2209'
Subset    \u2190 '\u2286'
Quote_    \u2190 '`'
Unquote   \u2190 ','   -- only meaningful inside a quotation

-- Numeric operators
Plus      \u2190 '+'
Minus     \u2190 '-'
Times     \u2190 '*'
TrueDiv   \u2190 '\u00f7'    -- true division (ASCII: /)
IntDiv    \u2190 '/'    -- integer division (was ASCII: //)
Pow       \u2190 '^'
Pipe      \u2190 '|>'

-- Comparison
LT        \u2190 '<'
GT        \u2190 '>'
EqEq      \u2190 '='   -- equality predicate (in contracts)

-- Structural punctuation
Eq_       \u2190 '='   -- spec/impl separator, binding operator
Bar       \u2190 '|'   -- constructors, match arms, contract clauses, handler arms
Colon     \u2190 ':'   -- type annotation
Comma     \u2190 ','
Dot       ← '.'   -- field access, composition (ASCII alt for ·)
DotDot    ← '..'  -- reserved
At        ← '@'   -- pin binding
AtBang    ← '@!'  -- compiler pin (Glass IR only)
Hash      ← '#'   -- attribute prefix
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

1.7 Pin Literals
-- PinId literal: only valid in Glass IR and debugger output
-- Not valid in source programs
PinLit    ← 'pin#' HexChar+
2. Token Types
The lexer produces the following token types. All subsequent passes pattern-match on these.
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
The TText token produced by the lexer has interpolation fragments pre-resolved to a sequence of (literal_text, expr_token_stream) pairs. The parser handles the joining.
3. Top-Level Structure
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
4. Module Grammar
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
5. Type Grammar
Type        ← ForallType
            / FunType

ForallType  ← Forall WS TVarList '.' WS Type
TVarList    ← TypeVar (WS TypeVar)*

FunType     ← EffType (WS Arrow WS EffType)*

-- Effect-annotated type: {row} ReturnType
EffType     ← '{' WS EffRow WS '}' WS AtomType
            / AtomType

EffRow      ← EffEntry (WS ',' WS EffEntry)* (WS '|' WS RowVar)?
            / RowVar
            / ε                                -- empty row: pure

EffEntry    ← PascalName (WS AtomType)*       -- effect with type args

AppType     ← AtomType (WS AtomType)*

AtomType    ← '(' WS Type WS ')'
            / ProductType
            / SumType
            / RefinedType
            / DictType                         -- Glass IR only
            / Top                              -- ⊤
            / Bottom                           -- ⊥
            / Empty                            -- ∅ (empty row/collection)
            / PascalName                       -- type constructor
            / QualName                         -- qualified type
            / TypeVar                          -- type variable

ProductType ← AtomType WS Product WS AtomType

SumType     ← AtomType WS Sum WS AtomType

-- Refined type: (name : Type | Predicate)
RefinedType ← '(' WS SnakeName WS ':' WS Type WS '|' WS Pred WS ')'

-- Explicit dictionary type (Glass IR elaboration of typeclass constraint)
-- Not valid in source programs
DictType    ← '(' WS SnakeName WS ':' WS PascalName WS AtomType* WS ')'

-- Record type
RecordType  ← '{' WS RecordFields WS '}'
RecordFields ← RecordField (WS ',' WS RecordField)*
RecordField  ← SnakeName WS ':' WS Type
6. Expression Grammar
Expressions are listed from lowest to highest precedence.
Expr        ← LetExpr
            / LambdaExpr
            / HandleExpr
            / MatchExpr
            / DoExpr
            / IfExpr
            / AnnExpr

-- Let: sequential binding in body
LetExpr     ← 'let' WS Pattern WS [':' WS Type WS] '=' WS Expr WS Expr

-- Lambda
LambdaExpr  ← Lambda WS ArgPat+ WS Arrow WS Expr
ArgPat      ← AtomPat

-- Effect handler
HandleExpr  ← 'handle' WS Expr WS '{' WS HandlerArm+ WS '}'

HandlerArm  ← '|' WS 'return' WS Pattern WS Arrow WS Expr WS
            / '|' WS 'once'? WS SnakeName WS AtomPat* WS SnakeName WS Arrow WS Expr WS
            -- op_name pattern-args... continuation → body

-- Match
MatchExpr   ← 'match' WS Expr WS '{' WS MatchArm+ WS '}'

MatchArm    ← '|' WS Pattern (WS 'if' WS AppExpr)? WS Arrow WS Expr WS

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

CmpExpr     ← AddExpr (WS CmpOp WS AddExpr)*
CmpOp       ← '=' / NEq / LEq / GEq / '<' / '>' / In / NotIn / Subset

AddExpr     ← MulExpr (WS AddOp WS MulExpr)*
AddOp       ← Plus / Minus

MulExpr     ← UnaryExpr (WS MulOp WS UnaryExpr)*
MulOp       ← Times / TrueDiv / IntDiv / 'mod' / Pow

UnaryExpr   ← '-' WS UnaryExpr                                -- negation
            / '¬' WS UnaryExpr                                -- logical not
            / AppExpr

-- Function application (left-associative, highest binary precedence)
AppExpr     ← AtomExpr (WS DictArg / WS AtomExpr)*

-- Explicit dictionary application (Glass IR elaboration)
DictArg     ← '[' WS Expr WS ']'

-- Atomic expressions
AtomExpr    ← '(' WS Expr WS ')'
            / RecordExpr
            / RecordUpdate
            / ProgrammerPin
            / CompilerPin                 -- Glass IR only
            / PinRef                      -- Glass IR only
            / QuoteExpr
            / NatLit
            / TextLit
            / RawText
            / BytesLit
            / HexBytes
            / Top                         -- ⊤ as the unit value ()
            / SnakeName
            / PascalName                  -- constructor or type
            / QualName

-- Record construction
RecordExpr  ← '{' WS RecordInit (WS ',' WS RecordInit)* WS '}'
RecordInit  ← SnakeName WS '=' WS Expr

-- Functional update
RecordUpdate ← AtomExpr WS '{' WS RecordInit (WS ',' WS RecordInit)* WS '}'

-- Programmer pin
ProgrammerPin ← '@' SnakeName
                (WS ':' WS Type)?
                WS '=' WS Expr

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
7. Pattern Grammar
Pattern     ← OrPat

OrPat       ← AsPat (WS '|' WS AsPat)*

AsPat       ← AtomPat (WS 'as' WS SnakeName)?

AtomPat     ← '(' WS Pattern WS ')'
            / WildPat
            / LitPat
            / VarPat
            / ConsPat
            / TuplePat
            / RecordPat

WildPat     ← '_'

LitPat      ← NatLit
            / TextLit
            / BytesLit

VarPat      ← SnakeName !WS                   -- no following whitespace+identifier

ConsPat     ← PascalName (WS AtomPat)*        -- Constructor patterns

TuplePat    ← '(' WS Pattern (WS ',' WS Pattern)+ WS ')'

RecordPat   ← '{' WS RecordPatField (WS ',' WS RecordPatField)* WS '}'
RecordPatField ← SnakeName (WS '=' WS Pattern)?   -- omit = for punning
8. Declaration Grammar
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
            / 'type' WS PascalName (WS TypeVar)* WS '=' WS Type  -- alias

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

ClassConstraint ← QualPascal WS AtomType+ WS '=>' WS
QualPascal  ← PascalName ('.' PascalName)*

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
Variance    ← '+' / '-' / '~'

-- Macro declaration
MacroDecl   ← 'macro' WS SnakeName WS '(' WS MacroParams WS ')'
              (WS ':' WS EffSpec)?                             -- declared effect footprint
              WS '=' WS QuoteExpr

MacroParams ← SnakeName (WS SnakeName)*
EffSpec     ← '{' WS EffRow WS '}'
9. Predicate Grammar
The contract predicate language targets the Tier 0/1 discharge procedures. The grammar encodes the decidability boundary: predicates in LinArithPred and BoolPred are candidates for Tier 1 static discharge; everything else falls to ExprPred and becomes Deferred.
Pred        ← QuantPred
            / CompoundPred
            / LinArithPred
            / BoolPred
            / ExprPred            -- fallback: arbitrary expr → always Deferred

-- Quantified predicates
QuantPred   ← Forall WS QuantVarList '.' WS Pred
            / Exists WS QuantVarList '.' WS Pred
QuantVarList ← SnakeName (WS ',' WS SnakeName)*

-- Compound predicates
CompoundPred ← Pred WS '∧' WS Pred
             / Pred WS '∨' WS Pred
             / '¬' WS Pred
             / Pred WS '=>' WS Pred
             / '(' WS Pred WS ')'

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
10. Infix Operators
Operators in backtick form (infix application):
InfixOp     ← '`' SnakeName '`'     -- e.g., x `elem` xs
            / '(' WS UnicodeOp WS ')'

UnicodeOp   ← Arrow / Lambda / Forall / Exists / Bind / Compose
            / Sum / Product / Top / Bottom / Empty
            / NEq / LEq / GEq / In / NotIn / Subset
            / Plus / Minus / Times / TrueDiv / IntDiv / Pow
            / Pipe / EqEq / LT / GT
11. Attribute Syntax
Attributes annotate declarations with metadata. They appear immediately before the declaration they annotate.
Attribute   ← '#[' WS AttrContent WS ']'

AttrContent ← 'jet' WS ':' WS JetSpec         -- jet registration
            / 'pin'                             -- force pinning
            / 'deprecated' (WS ':' WS TextLit)?
            / SnakeName (WS ':' WS AttrVal)?   -- generic attribute

JetSpec     ← 'registry' WS '=' WS TextLit
              (',' WS 'version' WS '=' WS NatLit)?

AttrVal     ← TextLit / NatLit / SnakeName
12. Glass IR Extensions
The following constructs are valid in Glass IR but not in source programs. The parser must accept them when operating in Glass IR mode. In source mode, these are parse errors.
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
13. Well-Formedness Constraints
These constraints are checked during name resolution and type checking, not by the PEG grammar.
13.1 Naming
Every function/value name must match [a-z_][a-z0-9_]*
Every type/effect/constructor name must match [A-Z][A-Za-z0-9]*
TypeVar is exactly one character in [a-q]
RowVar is exactly one character in [r-z]
Violations produce a specific error naming the offending identifier and the expected form
13.2 Contracts
pre clauses must precede post clauses
inv expands to both a pre and post clause with the same predicate
Proven status is only valid if the predicate falls within LinArithPred or BoolPred and was discharged by Tier 0/1 procedures
Refuted status is a compile error — no executable code follows
A contract that is a syntactic recapitulation of the implementation body should trigger the tautology detector warning
13.3 Effects
Abort must never appear in an effect row
External must appear in the row of any function whose body calls an external mod operation
Row variables must be distinct from type variables in the same quantifier scope
Handler arms must cover the return case and at least one operation case
13.4 Exhaustiveness
Pattern matches must be exhaustive (see spec/03-exhaustiveness.md). When the checker cannot verify exhaustiveness, a catch-all | _ → arm is required. The compiler error names the specific DeferralReason.
13.5 Modules
Module dependency graph must be acyclic
All names in a module body must be in scope (defined locally, imported via use, or from external mod)
Mutual recursion is bounded by module — no cross-module mutual recursion
Export list must only name definitions that exist in the module
13.6 Macros
Macros must declare an effect signature if their expansion introduces effects
Macro expansion is always visible in Glass IR — unexpanded macros never appear in compiled output
Macro names follow snake_case convention
13.7 Glass IR Mode
In Glass IR mode (activated by the FragmentMeta header or explicit flag):
CompilerPin, PinRef, DictArg, GroupedPin, PendingDecl, TraceDecl are valid
All names must be fully qualified — no bare unqualified names from external modules
No use directives permitted
All typeclass constraints must appear as explicit DictType arguments
14. Operator Precedence Table
From lowest to highest:
Level
Operators
Associativity
1
|>
Left
2
·
Right
3
∨
Left
4
∧
Left
5
= ≠ < ≤ > ≥ ∈ ∉ ⊆
Non-associative
6
+ -
Left
7
* ÷ / mod ^
Left
8
Unary - ¬
—
9
Function application
Left
Parentheses override precedence at all levels. The = in expression position (comparison) and the = as the spec/implementation separator are disambiguated by syntactic position — the separator = never appears inside an expression context.
15. Complete Example
A well-formed Gallowglass module demonstrating the major syntactic forms:
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
        @raw   ← Core.IO.read_file path
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

  -- Macro

  macro expect_columns (table n) : {Exn CsvError | r}
    = `(match ,table {
          | []       → Exn.raise (SchemaError "empty table")
          | (r :: _) → if length r ≠ ,n
                        then Exn.raise (SchemaError "wrong column count")
                        else ,table
        })

}
This example is also a test case for the bootstrap compiler. It must parse without error, type-check without error (under the restricted dialect), and produce correct PLAN output.
