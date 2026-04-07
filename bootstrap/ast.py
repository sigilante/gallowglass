"""
Gallowglass bootstrap AST.

Dataclass representations of every AST node for the restricted dialect
(see bootstrap/BOOTSTRAP.md §2). Source locations are stored on every node.

Reference: bootstrap/src/ast.sire (design stub)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from bootstrap.lexer import Loc


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

@dataclass
class QualName:
    """A possibly-qualified name: [Module.Path.]name or [Module.Path.]Name"""
    parts: list[str]   # all Pascal parts + final name
    loc: Loc

    @property
    def is_qualified(self) -> bool:
        return len(self.parts) > 1

    def __str__(self) -> str:
        return '.'.join(self.parts)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class TyVar:
    name: str
    loc: Loc

@dataclass
class TyCon:
    name: QualName
    loc: Loc

@dataclass
class TyApp:
    fun: Any   # Type
    arg: Any   # Type
    loc: Loc

@dataclass
class TyArr:
    from_: Any  # Type
    to_: Any    # Type
    loc: Loc

@dataclass
class TyForall:
    vars: list[str]
    body: Any   # Type
    loc: Loc

@dataclass
class TyEffect:
    """Effect-annotated type: {IO, Exn E | r} T. Parsed but not checked."""
    row: Any    # EffRow (list of EffEntry, optional row var)
    ty: Any     # Type
    loc: Loc

@dataclass
class TyTuple:
    elems: list[Any]   # Type list, len >= 2
    loc: Loc

@dataclass
class TyRecord:
    fields: list[tuple[str, Any]]  # (name, Type) pairs
    loc: Loc

@dataclass
class TyUnit:
    loc: Loc

@dataclass
class TyBottom:
    loc: Loc

@dataclass
class TyEmpty:
    loc: Loc

@dataclass
class TyConstrained:
    constraints: list[tuple[str, list[Any]]]  # [(ClassName, [AtomType])]
    ty: Any   # Type
    loc: Loc

@dataclass
class TyRefined:
    name: str
    ty: Any    # Type
    pred: Any  # Pred (ignored in bootstrap)
    loc: Loc

@dataclass
class EffRow:
    entries: list[tuple[str, list[Any]]]  # [(EffectName, [AtomType])]
    row_var: str | None                   # open row variable, or None
    loc: Loc


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

@dataclass
class PatWild:
    loc: Loc

@dataclass
class PatVar:
    name: str
    loc: Loc

@dataclass
class PatCon:
    name: QualName
    args: list[Any]   # Pattern list
    loc: Loc

@dataclass
class PatNat:
    value: int
    loc: Loc

@dataclass
class PatText:
    value: str
    loc: Loc

@dataclass
class PatTuple:
    pats: list[Any]   # Pattern list, len >= 2
    loc: Loc

@dataclass
class PatList:
    pats: list[Any]   # Pattern list (may be empty = Nil)
    loc: Loc

@dataclass
class PatCons:
    head: Any  # Pattern
    tail: Any  # Pattern
    loc: Loc

@dataclass
class PatRecord:
    fields: list[tuple[str, Any]]  # (field_name, Pattern) pairs
    loc: Loc

@dataclass
class PatAs:
    pat: Any   # Pattern
    name: str
    loc: Loc

@dataclass
class PatOr:
    pats: list[Any]  # Pattern list, len >= 2
    loc: Loc


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------

@dataclass
class ExprVar:
    name: QualName
    loc: Loc

@dataclass
class ExprApp:
    fun: Any   # Expr
    arg: Any   # Expr
    loc: Loc

@dataclass
class ExprLam:
    params: list[Any]   # Pattern list
    body: Any           # Expr
    loc: Loc

@dataclass
class ExprLet:
    pattern: Any        # Pattern
    type_ann: Any | None  # Type or None
    rhs: Any            # Expr
    body: Any           # Expr
    loc: Loc

@dataclass
class ExprMatch:
    scrutinee: Any              # Expr
    arms: list[tuple[Any, Any | None, Any]]  # (Pattern, guard_expr | None, Expr)
    loc: Loc

@dataclass
class ExprHandle:
    """handle expr { arms } — parsed but not elaborated in bootstrap."""
    comp: Any
    arms: list[Any]   # HandlerArm
    loc: Loc

@dataclass
class HandlerReturn:
    pattern: Any   # Pattern
    body: Any      # Expr
    loc: Loc

@dataclass
class HandlerOp:
    once: bool
    op_name: str
    arg_pats: list[Any]   # Pattern list
    resume: str           # continuation variable name
    body: Any             # Expr
    loc: Loc

@dataclass
class ExprIf:
    cond: Any    # Expr
    then_: Any   # Expr
    else_: Any   # Expr
    loc: Loc

@dataclass
class ExprTuple:
    elems: list[Any]  # Expr list, len >= 2
    loc: Loc

@dataclass
class ExprList:
    elems: list[Any]  # Expr list (may be empty)
    loc: Loc

@dataclass
class ExprNat:
    value: int
    loc: Loc

@dataclass
class ExprText:
    value: Any   # str or list of (str | ('interp', str)) fragments
    loc: Loc

@dataclass
class ExprRawText:
    value: str
    loc: Loc

@dataclass
class ExprBytes:
    value: bytes
    loc: Loc

@dataclass
class ExprHexBytes:
    value: bytes
    loc: Loc

@dataclass
class ExprUnit:
    loc: Loc

@dataclass
class ExprPin:
    """@name [: Type] = rhs  body   — programmer pin binding."""
    name: str
    type_ann: Any | None   # Type or None
    rhs: Any               # Expr
    body: Any              # Expr
    loc: Loc

@dataclass
class ExprDo:
    """name ← rhs  body   — effectful bind."""
    name: str
    rhs: Any    # Expr
    body: Any   # Expr
    loc: Loc

@dataclass
class ExprFix:
    """fix λ self args → body   — anonymous recursion."""
    lam: ExprLam
    loc: Loc

@dataclass
class ExprOp:
    op: str    # canonical Unicode operator string
    lhs: Any   # Expr
    rhs: Any   # Expr
    loc: Loc

@dataclass
class ExprUnary:
    op: str   # '-' or '¬'
    operand: Any   # Expr
    loc: Loc

@dataclass
class ExprAnn:
    expr: Any   # Expr
    type_: Any  # Type
    loc: Loc

@dataclass
class ExprWith:
    """expr with (dict) args — explicit dictionary override."""
    expr: Any
    dict_: Any       # Expr (the dictionary)
    extra_args: list[Any]  # Expr list
    loc: Loc

@dataclass
class ExprRecord:
    fields: list[tuple[str, Any]]   # (name, Expr) pairs
    loc: Loc

@dataclass
class ExprRecordUpdate:
    base: Any
    fields: list[tuple[str, Any]]
    loc: Loc


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------

@dataclass
class ContractClause:
    kind: str          # 'pre' | 'post' | 'inv' | 'law'
    status: str        # 'Proven' | 'Deferred(...)' | etc.
    pred: Any          # Pred (ignored in bootstrap)
    loc: Loc

@dataclass
class DeclLet:
    name: str
    type_ann: Any | None      # Type or None
    contracts: list[ContractClause]
    body: Any                 # Expr
    loc: Loc

@dataclass
class Constructor:
    name: str
    arg_types: list[Any]   # AtomType list
    loc: Loc

@dataclass
class DeclType:
    name: str
    params: list[str]           # TypeVar names
    constructors: list[Constructor]
    loc: Loc

@dataclass
class DeclTypeAlias:
    name: str
    params: list[str]
    ty: Any   # Type
    loc: Loc

@dataclass
class DeclTypeBuiltin:
    name: str
    params: list[str]
    loc: Loc

@dataclass
class DeclRecord:
    name: str
    params: list[str]
    fields: list[tuple[str, Any]]  # (name, Type)
    loc: Loc

@dataclass
class EffOp:
    name: str
    ty: Any   # Type
    loc: Loc

@dataclass
class DeclEff:
    name: str
    params: list[str]
    ops: list[EffOp]
    loc: Loc

@dataclass
class ClassMember:
    name: str
    ty: Any              # Type
    contracts: list[ContractClause]
    default: Any | None  # Expr or None
    loc: Loc

@dataclass
class ClassLaw:
    name: str
    pred: Any   # Pred
    loc: Loc

@dataclass
class DeclClass:
    constraints: list[tuple[str, list[Any]]]  # superclass constraints
    name: str
    params: list[str]
    members: list[Any]  # ClassMember | ClassLaw
    loc: Loc

@dataclass
class InstanceMember:
    name: str
    body: Any   # Expr
    loc: Loc

@dataclass
class DeclInst:
    constraints: list[tuple[str, list[Any]]]
    class_name: str
    type_args: list[Any]   # AtomType list
    members: list[InstanceMember]
    loc: Loc

@dataclass
class ExtItem:
    """A single item in an external mod block."""
    name: str           # snake_case op name, or None for type
    ty: Any             # Type, or ExtTypeSpec for type items
    is_type: bool
    loc: Loc

@dataclass
class DeclExt:
    module_path: list[str]   # ['Core', 'Nat']
    items: list[ExtItem]
    loc: Loc

@dataclass
class UseSpec:
    unqualified: bool
    names: list[Any]   # UseItem (str or ('instance', name, types))
    loc: Loc

@dataclass
class DeclUse:
    module_path: list[str]
    spec: UseSpec | None
    loc: Loc

@dataclass
class ModItem:
    """A declaration inside a mod { } block."""
    decl: Any   # any Decl
    loc: Loc

@dataclass
class DeclMod:
    name: list[str]    # ['Foo', 'Bar'] for Foo.Bar
    body: list[Any]    # ModItem list
    loc: Loc


# ---------------------------------------------------------------------------
# Program
# ---------------------------------------------------------------------------

@dataclass
class Program:
    decls: list[Any]   # TopDecl list
    loc: Loc
