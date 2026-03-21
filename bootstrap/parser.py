"""
Gallowglass bootstrap parser.

Recursive descent, 1-token lookahead.
Input:  list[Token] from bootstrap.lexer.lex()
Output: bootstrap.ast.Program

Implements the restricted dialect (BOOTSTRAP.md §2).
Excluded features are parsed and stored in the AST but flagged:
  - handle expressions: parsed, stored as ExprHandle
  - effect rows: parsed, stored in TyEffect (not checked)
  - contracts: parsed, stored in ContractClause (ignored by typecheck)
  - fix / do / with: parsed, stored in ExprFix / ExprDo / ExprWith

Reference: bootstrap/src/parser.sire (design stub)
"""

from __future__ import annotations
from bootstrap.lexer import (
    Token, Loc, LexError,
    KIND_SNAKE, KIND_PASCAL, KIND_TYPEVAR, KIND_ROWVAR, KIND_KEYWORD,
    KIND_NAT, KIND_TEXT, KIND_RAWTEXT, KIND_BYTES, KIND_HEXBYTES,
    KIND_OP, KIND_PUNCT, KIND_EOF,
)
from bootstrap.ast import *


class ParseError(Exception):
    def __init__(self, msg: str, loc: Loc):
        super().__init__(f"{loc}: error: {msg}")
        self.loc = loc


# ---------------------------------------------------------------------------
# Parser state
# ---------------------------------------------------------------------------

class Parser:
    def __init__(self, tokens: list[Token], filename: str = '<stdin>'):
        self.tokens = tokens
        self.pos = 0
        self.filename = filename

    # -- primitives --

    def _loc(self) -> Loc:
        return self.tokens[self.pos].loc

    def _peek(self) -> Token:
        return self.tokens[self.pos]

    def _peek2(self) -> Token:
        idx = min(self.pos + 1, len(self.tokens) - 1)
        return self.tokens[idx]

    def _advance(self) -> Token:
        tok = self.tokens[self.pos]
        if tok.kind != KIND_EOF:
            self.pos += 1
        return tok

    def _at_end(self) -> bool:
        return self.tokens[self.pos].kind == KIND_EOF

    def _check(self, kind: str, value=None) -> bool:
        t = self._peek()
        if t.kind != kind:
            return False
        if value is not None and t.value != value:
            return False
        return True

    def _check2(self, kind: str, value=None) -> bool:
        """Check the token *after* the current one."""
        t = self._peek2()
        if t.kind != kind:
            return False
        if value is not None and t.value != value:
            return False
        return True

    def _eat(self, kind: str, value=None) -> Token:
        """Consume and return token, raising ParseError on mismatch."""
        t = self._peek()
        if t.kind != kind or (value is not None and t.value != value):
            exp = f"{kind!r}" + (f" {value!r}" if value is not None else "")
            got = f"{t.kind!r} {t.value!r}"
            raise ParseError(f"expected {exp}, got {got}", t.loc)
        return self._advance()

    def _try_eat(self, kind: str, value=None) -> Token | None:
        """Consume and return token if it matches, else return None."""
        if self._check(kind, value):
            return self._advance()
        return None

    def _error(self, msg: str) -> ParseError:
        return ParseError(msg, self._loc())

    # -- helpers for qualified names --

    def _parse_qual_name(self) -> QualName:
        """
        Parse a qualified name: Pascal.Parts.snake_name or Pascal.Parts.PascalName
        or a bare SnakeName / PascalName.

        Strategy: consume consecutive Pascal.Pascal.Pascal sequences, then
        check if a dot follows with a snake_name.
        """
        loc = self._loc()
        parts = []

        # Must start with Pascal or Snake
        t = self._peek()
        if t.kind == KIND_PASCAL:
            parts.append(self._advance().value)
            # Consume .Pascal or .snake chains
            while self._check(KIND_PUNCT, '.'):
                # peek after the dot
                nxt = self._peek2()
                if nxt.kind in (KIND_PASCAL, KIND_SNAKE, KIND_TYPEVAR, KIND_ROWVAR):
                    self._advance()  # consume dot
                    parts.append(self._advance().value)
                else:
                    break
        elif t.kind in (KIND_SNAKE, KIND_TYPEVAR, KIND_ROWVAR):
            parts.append(self._advance().value)
        else:
            raise self._error(f"expected name, got {t.kind!r} {t.value!r}")

        return QualName(parts, loc)

    def _parse_pascal_qual(self) -> QualName:
        """Parse a qualified Pascal name (module path): Foo.Bar.Baz"""
        loc = self._loc()
        parts = [self._eat(KIND_PASCAL).value]
        while self._check(KIND_PUNCT, '.') and self._check2(KIND_PASCAL):
            self._advance()  # dot
            parts.append(self._eat(KIND_PASCAL).value)
        return QualName(parts, loc)

    # =========================================================================
    # Top-level
    # =========================================================================

    def parse_program(self) -> Program:
        loc = self._loc()
        decls = []
        while not self._at_end():
            decls.append(self._parse_top_decl())
        return Program(decls, loc)

    def _parse_top_decl(self) -> Any:
        t = self._peek()
        if t.kind == KIND_KEYWORD:
            kw = t.value
            if kw == 'mod':
                return self._parse_mod_decl()
            if kw == 'use':
                return self._parse_use_decl()
            if kw == 'let':
                return self._parse_let_decl()
            if kw == 'type':
                return self._parse_type_decl()
            if kw == 'eff':
                return self._parse_eff_decl()
            if kw == 'class':
                return self._parse_class_decl()
            if kw == 'instance':
                return self._parse_instance_decl()
            if kw == 'external':
                return self._parse_external_mod()
            if kw == 'export':
                return self._parse_export_decl()
        raise self._error(f"unexpected token {t.kind!r} {t.value!r} at top level")

    # =========================================================================
    # Module / use / export
    # =========================================================================

    def _parse_mod_decl(self) -> DeclMod:
        loc = self._loc()
        self._eat(KIND_KEYWORD, 'mod')
        name = self._parse_pascal_qual()
        self._eat(KIND_PUNCT, '{')
        body = []
        while not self._check(KIND_PUNCT, '}'):
            body.append(self._parse_top_decl())
        self._eat(KIND_PUNCT, '}')
        return DeclMod(name.parts, body, loc)

    def _parse_use_decl(self) -> DeclUse:
        loc = self._loc()
        self._eat(KIND_KEYWORD, 'use')
        module_path = self._parse_pascal_qual()
        spec = None
        if self._check(KIND_KEYWORD, 'unqualified'):
            self._advance()
            self._eat(KIND_PUNCT, '{')
            names = self._parse_use_list()
            self._eat(KIND_PUNCT, '}')
            spec = UseSpec(unqualified=True, names=names, loc=loc)
        elif self._check(KIND_PUNCT, '{'):
            self._advance()
            names = self._parse_use_list()
            self._eat(KIND_PUNCT, '}')
            spec = UseSpec(unqualified=False, names=names, loc=loc)
        return DeclUse(module_path.parts, spec, loc)

    def _parse_use_list(self) -> list:
        items = [self._parse_use_item()]
        while self._try_eat(KIND_PUNCT, ','):
            items.append(self._parse_use_item())
        return items

    def _parse_use_item(self) -> Any:
        if self._check(KIND_KEYWORD, 'instance'):
            self._advance()
            name = self._eat(KIND_PASCAL).value
            types = []
            while self._is_atom_type_start():
                types.append(self._parse_atom_type())
            return ('instance', name, types)
        if self._check(KIND_KEYWORD, 'instances'):
            self._advance()
            return ('instances',)
        if self._check(KIND_PUNCT, '('):
            self._advance()
            op = self._advance().value
            self._eat(KIND_PUNCT, ')')
            return ('op', op)
        if self._check(KIND_PASCAL):
            return ('type', self._advance().value)
        return ('name', self._eat(KIND_SNAKE).value)

    def _parse_export_decl(self) -> Any:
        """Parse export { ... } — store as a raw list, not used by bootstrap."""
        loc = self._loc()
        self._eat(KIND_KEYWORD, 'export')
        self._eat(KIND_PUNCT, '{')
        items = []
        while not self._check(KIND_PUNCT, '}'):
            items.append(self._advance().value)
            self._try_eat(KIND_PUNCT, ',')
        self._eat(KIND_PUNCT, '}')
        return ('export', items, loc)

    # =========================================================================
    # Let declaration
    # =========================================================================

    def _parse_let_decl(self) -> DeclLet:
        loc = self._loc()
        self._eat(KIND_KEYWORD, 'let')
        name = self._eat(KIND_SNAKE).value
        type_ann = None
        if self._try_eat(KIND_PUNCT, ':'):
            type_ann = self._parse_type()
        contracts = []
        while self._check(KIND_PUNCT, '|') and self._check2(KIND_KEYWORD):
            kw = self._peek2().value
            if kw in ('pre', 'post', 'inv', 'law'):
                contracts.append(self._parse_contract_clause())
            else:
                break
        self._eat(KIND_PUNCT, '=')
        body = self._parse_expr()
        return DeclLet(name, type_ann, contracts, body, loc)

    def _parse_contract_clause(self) -> ContractClause:
        loc = self._loc()
        self._eat(KIND_PUNCT, '|')
        kind = self._eat(KIND_KEYWORD).value   # pre/post/inv/law
        # status: Proven | Deferred(...) | Refuted | Checked | Violated
        status_tok = self._eat(KIND_PASCAL)
        status = status_tok.value
        if status == 'Deferred' and self._check(KIND_PUNCT, '('):
            self._advance()
            reason = self._eat(KIND_PASCAL).value
            self._eat(KIND_PUNCT, ')')
            status = f'Deferred({reason})'
        self._eat(KIND_PUNCT, '(')
        # Skip predicate tokens until matching )
        pred = self._parse_pred()
        self._eat(KIND_PUNCT, ')')
        return ContractClause(kind, status, pred, loc)

    # =========================================================================
    # Type declaration
    # =========================================================================

    def _parse_type_decl(self) -> Any:
        loc = self._loc()
        self._eat(KIND_KEYWORD, 'type')
        name = self._eat(KIND_PASCAL).value
        params = []
        while self._check(KIND_TYPEVAR):
            params.append(self._advance().value)

        # builtin: type Foo : builtin  ('builtin' is KIND_SNAKE, not a keyword)
        if self._check(KIND_PUNCT, ':'):
            self._advance()
            self._eat(KIND_SNAKE, 'builtin')
            return DeclTypeBuiltin(name, params, loc)

        self._eat(KIND_PUNCT, '=')

        # Record type: type Foo = { field : Type, ... }
        if self._check(KIND_PUNCT, '{'):
            self._advance()
            fields = self._parse_record_fields()
            self._eat(KIND_PUNCT, '}')
            return DeclRecord(name, params, fields, loc)

        # Sum type: type Foo = | Bar | Baz a
        if self._check(KIND_PUNCT, '|'):
            constructors = []
            while self._check(KIND_PUNCT, '|'):
                self._advance()
                c_loc = self._loc()
                t = self._peek()
                if t.kind == KIND_KEYWORD and t.value in ('True', 'False', 'Unit', 'Never'):
                    c_name = self._advance().value
                else:
                    c_name = self._eat(KIND_PASCAL).value
                arg_types = []
                while self._is_atom_type_start():
                    arg_types.append(self._parse_atom_type())
                constructors.append(Constructor(c_name, arg_types, c_loc))
            return DeclType(name, params, constructors, loc)

        # Type alias: type Foo = Bar a
        ty = self._parse_type()
        return DeclTypeAlias(name, params, ty, loc)

    def _parse_record_fields(self) -> list[tuple[str, Any]]:
        fields = [self._parse_record_field()]
        while self._try_eat(KIND_PUNCT, ','):
            if self._check(KIND_PUNCT, '}'):
                break   # trailing comma
            fields.append(self._parse_record_field())
        return fields

    def _parse_record_field(self) -> tuple[str, Any]:
        # Field names may be single chars (KIND_TYPEVAR a-q, KIND_ROWVAR r-z)
        t = self._peek()
        if t.kind in (KIND_SNAKE, KIND_TYPEVAR, KIND_ROWVAR):
            name = self._advance().value
        else:
            name = self._eat(KIND_SNAKE).value  # produces a nice error
        self._eat(KIND_PUNCT, ':')
        ty = self._parse_type()
        return (name, ty)

    # =========================================================================
    # Effect declaration
    # =========================================================================

    def _parse_eff_decl(self) -> DeclEff:
        loc = self._loc()
        self._eat(KIND_KEYWORD, 'eff')
        name = self._eat(KIND_PASCAL).value
        params = []
        while self._check(KIND_TYPEVAR) or self._check(KIND_ROWVAR):
            params.append(self._advance().value)
        self._eat(KIND_PUNCT, '{')
        ops = []
        while not self._check(KIND_PUNCT, '}'):
            op_loc = self._loc()
            op_name = self._eat(KIND_SNAKE).value
            self._eat(KIND_PUNCT, ':')
            op_ty = self._parse_type()
            ops.append(EffOp(op_name, op_ty, op_loc))
        self._eat(KIND_PUNCT, '}')
        return DeclEff(name, params, ops, loc)

    # =========================================================================
    # Class / instance declarations
    # =========================================================================

    def _parse_class_constraints(self) -> list[tuple[str, list[Any]]]:
        """Parse optional superclass constraints before class/instance name."""
        # Lookahead: if we see PascalName AtomType+ => we have constraints
        saved = self.pos
        constraints = []
        try:
            c = self._parse_constraint()
            constraints.append(c)
            while self._try_eat(KIND_PUNCT, ','):
                constraints.append(self._parse_constraint())
            self._eat(KIND_PUNCT, '=>')
            return constraints
        except ParseError:
            self.pos = saved
            return []

    def _parse_constraint(self) -> tuple[str, list[Any]]:
        name = self._eat(KIND_PASCAL).value
        types = []
        while self._is_atom_type_start():
            types.append(self._parse_atom_type())
        return (name, types)

    def _parse_class_decl(self) -> DeclClass:
        loc = self._loc()
        self._eat(KIND_KEYWORD, 'class')
        constraints = self._parse_class_constraints()
        name = self._eat(KIND_PASCAL).value
        params = []
        while self._check(KIND_TYPEVAR) or self._check(KIND_ROWVAR):
            params.append(self._advance().value)
        self._eat(KIND_PUNCT, '{')
        members = []
        while not self._check(KIND_PUNCT, '}'):
            members.append(self._parse_class_member())
        self._eat(KIND_PUNCT, '}')
        return DeclClass(constraints, name, params, members, loc)

    def _parse_class_member(self) -> Any:
        loc = self._loc()
        if self._check(KIND_KEYWORD, 'law'):
            self._advance()
            name = self._eat(KIND_SNAKE).value
            self._eat(KIND_PUNCT, ':')
            pred = self._parse_pred()
            return ClassLaw(name, pred, loc)
        name = self._eat(KIND_SNAKE).value
        self._eat(KIND_PUNCT, ':')
        ty = self._parse_type()
        contracts = []
        while self._check(KIND_PUNCT, '|') and self._check2(KIND_KEYWORD):
            kw = self._peek2().value
            if kw in ('pre', 'post', 'inv', 'law'):
                contracts.append(self._parse_contract_clause())
            else:
                break
        default = None
        if self._try_eat(KIND_PUNCT, '='):
            default = self._parse_expr()
        return ClassMember(name, ty, contracts, default, loc)

    def _parse_instance_decl(self) -> DeclInst:
        loc = self._loc()
        self._eat(KIND_KEYWORD, 'instance')
        constraints = self._parse_class_constraints()
        name = self._eat(KIND_PASCAL).value
        type_args = []
        while self._is_atom_type_start():
            type_args.append(self._parse_atom_type())
        self._eat(KIND_PUNCT, '{')
        members = []
        while not self._check(KIND_PUNCT, '}'):
            m_loc = self._loc()
            m_name = self._eat(KIND_SNAKE).value
            self._eat(KIND_PUNCT, '=')
            m_body = self._parse_expr()
            members.append(InstanceMember(m_name, m_body, m_loc))
        self._eat(KIND_PUNCT, '}')
        return DeclInst(constraints, name, type_args, members, loc)

    # =========================================================================
    # External mod
    # =========================================================================

    def _parse_external_mod(self) -> DeclExt:
        loc = self._loc()
        self._eat(KIND_KEYWORD, 'external')
        self._eat(KIND_KEYWORD, 'mod')
        # module path: Pascal.Pascal...
        parts = [self._eat(KIND_PASCAL).value]
        while self._check(KIND_PUNCT, '.') and self._check2(KIND_PASCAL):
            self._advance()
            parts.append(self._eat(KIND_PASCAL).value)
        self._eat(KIND_PUNCT, '{')
        items = []
        while not self._check(KIND_PUNCT, '}'):
            item_loc = self._loc()
            if self._check(KIND_KEYWORD, 'type'):
                self._advance()
                type_name = self._eat(KIND_PASCAL).value
                self._eat(KIND_PUNCT, ':')
                spec = self._parse_ext_type_spec()
                items.append(ExtItem(type_name, spec, is_type=True, loc=item_loc))
            else:
                op_name = self._eat(KIND_SNAKE).value
                self._eat(KIND_PUNCT, ':')
                ty = self._parse_type()
                items.append(ExtItem(op_name, ty, is_type=False, loc=item_loc))
        self._eat(KIND_PUNCT, '}')
        return DeclExt(parts, items, loc)

    def _parse_ext_type_spec(self) -> Any:
        # 'builtin' is lexed as KIND_SNAKE (not a keyword)
        if self._check(KIND_SNAKE, 'builtin') or self._check(KIND_KEYWORD, 'builtin'):
            self._advance()
            return 'builtin'
        # Opaque[variance]
        self._eat(KIND_PASCAL)   # 'Opaque'
        variance = None
        if self._try_eat(KIND_PUNCT, '['):
            variance = self._advance().value
            self._eat(KIND_PUNCT, ']')
        return ('Opaque', variance)

    # =========================================================================
    # Types
    # =========================================================================

    def _parse_type(self) -> Any:
        """Parse a full type expression."""
        loc = self._loc()
        # ∀ a b. Type
        if self._check(KIND_KEYWORD, '∀') or self._check(KIND_KEYWORD, 'forall'):
            return self._parse_forall_type()
        # Try constrained type: Constraint => Type
        # (backtrack if => not found)
        saved = self.pos
        try:
            return self._parse_constrained_type()
        except ParseError:
            self.pos = saved
        return self._parse_fun_type()

    def _parse_forall_type(self) -> TyForall:
        loc = self._loc()
        self._advance()  # ∀ or forall
        vars_ = []
        while self._check(KIND_TYPEVAR):
            vars_.append(self._advance().value)
        if not vars_:
            raise self._error("expected type variable after ∀")
        self._eat(KIND_PUNCT, '.')
        body = self._parse_type()
        return TyForall(vars_, body, loc)

    def _parse_constrained_type(self) -> TyConstrained:
        loc = self._loc()
        constraints = [self._parse_constraint()]
        while self._try_eat(KIND_PUNCT, ','):
            constraints.append(self._parse_constraint())
        self._eat(KIND_PUNCT, '=>')
        ty = self._parse_type()
        return TyConstrained(constraints, ty, loc)

    def _parse_fun_type(self) -> Any:
        loc = self._loc()
        lhs = self._parse_eff_type()
        if self._check(KIND_OP, '→'):
            self._advance()
            rhs = self._parse_fun_type()   # right-associative
            return TyArr(lhs, rhs, loc)
        return lhs

    def _parse_eff_type(self) -> Any:
        loc = self._loc()
        if self._check(KIND_PUNCT, '{'):
            self._advance()
            row = self._parse_eff_row()
            self._eat(KIND_PUNCT, '}')
            ty = self._parse_sum_prod_type()
            return TyEffect(row, ty, loc)
        return self._parse_sum_prod_type()

    def _parse_eff_row(self) -> EffRow:
        loc = self._loc()
        # Empty row
        if self._check(KIND_PUNCT, '}'):
            return EffRow([], None, loc)
        # Row variable only
        if self._check(KIND_ROWVAR) and (
                self._check2(KIND_PUNCT) and self._peek2().value == '}'):
            rv = self._advance().value
            return EffRow([], rv, loc)
        entries = []
        row_var = None
        while True:
            if self._check(KIND_ROWVAR) and (
                    self._check2(KIND_PUNCT) and self._peek2().value in ('}', '|')):
                # Bare row variable
                break
            entry_name = self._eat(KIND_PASCAL).value
            type_args = []
            while self._is_atom_type_start() and not self._check(KIND_PUNCT, '}'):
                type_args.append(self._parse_atom_type())
            entries.append((entry_name, type_args))
            if not self._try_eat(KIND_PUNCT, ','):
                break
        if self._try_eat(KIND_PUNCT, '|'):
            row_var = self._eat(KIND_ROWVAR).value
        return EffRow(entries, row_var, loc)

    def _parse_sum_prod_type(self) -> Any:
        loc = self._loc()
        lhs = self._parse_app_type()
        while self._check(KIND_OP, '⊕') or self._check(KIND_OP, '⊗'):
            op = self._advance().value
            rhs = self._parse_app_type()
            lhs = TyApp(TyApp(TyCon(QualName([op], loc), loc), lhs, loc), rhs, loc)
        return lhs

    def _parse_app_type(self) -> Any:
        loc = self._loc()
        head = self._parse_atom_type()
        while self._is_atom_type_start() and not self._check(KIND_OP, '→') \
                and not self._check(KIND_PUNCT, '=>'):
            arg = self._parse_atom_type()
            head = TyApp(head, arg, head.loc)
        return head

    def _is_atom_type_start(self) -> bool:
        t = self._peek()
        if t.kind == KIND_PASCAL:
            return True
        if t.kind in (KIND_TYPEVAR, KIND_ROWVAR):
            return True
        if t.kind == KIND_OP and t.value in ('⊤', '⊥', '∅'):
            return True
        if t.kind == KIND_PUNCT and t.value == '(':
            return True
        # Note: '{' not included — record types as atom type args must be
        # parenthesized. This prevents instance/class braces from being
        # consumed as record type arguments.
        return False

    def _parse_atom_type(self) -> Any:
        loc = self._loc()
        t = self._peek()

        if t.kind == KIND_OP and t.value == '⊤':
            self._advance()
            return TyUnit(loc)
        if t.kind == KIND_OP and t.value == '⊥':
            self._advance()
            return TyBottom(loc)
        if t.kind == KIND_OP and t.value == '∅':
            self._advance()
            return TyEmpty(loc)

        if t.kind in (KIND_TYPEVAR, KIND_ROWVAR):
            self._advance()
            return TyVar(t.value, loc)

        if t.kind == KIND_PASCAL:
            name = self._parse_pascal_qual()
            return TyCon(name, loc)

        if t.kind == KIND_PUNCT and t.value == '(':
            self._advance()
            # Check for unit: ()
            if self._check(KIND_PUNCT, ')'):
                self._advance()
                return TyUnit(loc)
            first = self._parse_type()
            if self._try_eat(KIND_PUNCT, ','):
                # Tuple type
                elems = [first]
                elems.append(self._parse_type())
                while self._try_eat(KIND_PUNCT, ','):
                    elems.append(self._parse_type())
                self._eat(KIND_PUNCT, ')')
                return TyTuple(elems, loc)
            self._eat(KIND_PUNCT, ')')
            return first

        if t.kind == KIND_PUNCT and t.value == '{':
            self._advance()
            fields = self._parse_record_fields()
            self._eat(KIND_PUNCT, '}')
            return TyRecord(fields, loc)

        raise self._error(f"expected type, got {t.kind!r} {t.value!r}")

    # =========================================================================
    # Expressions
    # =========================================================================

    def _parse_expr(self) -> Any:
        loc = self._loc()
        t = self._peek()

        # let x [: T] = rhs  body
        if t.kind == KIND_KEYWORD and t.value == 'let':
            return self._parse_let_expr()

        # @name [: T] = rhs  body  or  @name ← rhs  body
        if t.kind == KIND_PUNCT and t.value == '@':
            return self._parse_pin_expr()

        # λ args → body
        if t.kind == KIND_KEYWORD and t.value == 'λ':
            return self._parse_lambda_expr()

        # fix λ ...
        if t.kind == KIND_KEYWORD and t.value == 'fix':
            return self._parse_fix_expr()

        # if cond then t else e
        if t.kind == KIND_KEYWORD and t.value == 'if':
            return self._parse_if_expr()

        # name ← rhs  body  (do-style effectful bind)
        # Lookahead: snake_name ←
        if t.kind == KIND_SNAKE and self._check2(KIND_OP, '←'):
            return self._parse_do_expr()

        return self._parse_ann_expr()

    def _parse_let_expr(self) -> ExprLet:
        # Bootstrap restriction: requires 'in' to separate rhs from body.
        # This avoids the ambiguity in 'let x = f y body' (is y part of
        # the rhs application or is it the body?).
        # Full spec: let x = rhs body (two Exprs, layout-disambiguated).
        loc = self._loc()
        self._eat(KIND_KEYWORD, 'let')
        pat = self._parse_pattern()
        type_ann = None
        if self._try_eat(KIND_PUNCT, ':'):
            type_ann = self._parse_type()
        self._eat(KIND_PUNCT, '=')
        rhs = self._parse_expr()
        self._eat(KIND_KEYWORD, 'in')
        body = self._parse_expr()
        return ExprLet(pat, type_ann, rhs, body, loc)

    def _parse_pin_expr(self) -> ExprPin:
        # Bootstrap restriction: requires 'in' to separate rhs from body.
        loc = self._loc()
        self._eat(KIND_PUNCT, '@')
        name = self._eat(KIND_SNAKE).value
        type_ann = None
        if self._check(KIND_OP, '←'):
            self._advance()
            rhs = self._parse_expr()
            self._eat(KIND_KEYWORD, 'in')
            body = self._parse_expr()
            return ExprPin(name, None, rhs, body, loc)
        if self._try_eat(KIND_PUNCT, ':'):
            type_ann = self._parse_type()
        self._eat(KIND_PUNCT, '=')
        rhs = self._parse_expr()
        self._eat(KIND_KEYWORD, 'in')
        body = self._parse_expr()
        return ExprPin(name, type_ann, rhs, body, loc)

    def _parse_lambda_expr(self) -> ExprLam:
        loc = self._loc()
        self._eat(KIND_KEYWORD, 'λ')
        params = []
        while not self._check(KIND_OP, '→'):
            params.append(self._parse_atom_pat())
        self._eat(KIND_OP, '→')
        body = self._parse_expr()
        return ExprLam(params, body, loc)

    def _parse_fix_expr(self) -> ExprFix:
        loc = self._loc()
        self._eat(KIND_KEYWORD, 'fix')
        lam = self._parse_lambda_expr()
        return ExprFix(lam, loc)

    def _parse_if_expr(self) -> ExprIf:
        loc = self._loc()
        self._eat(KIND_KEYWORD, 'if')
        cond = self._parse_expr()
        self._eat(KIND_KEYWORD, 'then')
        then_ = self._parse_expr()
        self._eat(KIND_KEYWORD, 'else')
        else_ = self._parse_expr()
        return ExprIf(cond, then_, else_, loc)

    def _parse_do_expr(self) -> ExprDo:
        # Bootstrap restriction: requires 'in' to separate rhs from body.
        loc = self._loc()
        name = self._eat(KIND_SNAKE).value
        self._eat(KIND_OP, '←')
        rhs = self._parse_expr()
        self._eat(KIND_KEYWORD, 'in')
        body = self._parse_expr()
        return ExprDo(name, rhs, body, loc)

    def _parse_ann_expr(self) -> Any:
        loc = self._loc()
        expr = self._parse_op_expr()
        if self._try_eat(KIND_PUNCT, ':'):
            ty = self._parse_type()
            return ExprAnn(expr, ty, loc)
        return expr

    # -- operator precedence climbing --

    def _parse_op_expr(self) -> Any:
        return self._parse_compose_expr()

    def _parse_compose_expr(self) -> Any:
        loc = self._loc()
        lhs = self._parse_or_expr()
        while self._check(KIND_OP, '|>'):
            op = self._advance().value
            rhs = self._parse_or_expr()
            lhs = ExprOp(op, lhs, rhs, loc)
        return lhs

    def _parse_or_expr(self) -> Any:
        loc = self._loc()
        lhs = self._parse_and_expr()
        while self._check(KIND_OP, '·') or self._check(KIND_OP, '∨'):
            op = self._advance().value
            rhs = self._parse_and_expr()
            lhs = ExprOp(op, lhs, rhs, loc)
        return lhs

    def _parse_and_expr(self) -> Any:
        loc = self._loc()
        lhs = self._parse_cmp_expr()
        while self._check(KIND_OP, '∧'):
            op = self._advance().value
            rhs = self._parse_cmp_expr()
            lhs = ExprOp(op, lhs, rhs, loc)
        return lhs

    CMP_OPS = frozenset(['=', '≠', '≤', '≥', '<', '>', '∈', '∉', '⊆'])

    def _parse_cmp_expr(self) -> Any:
        loc = self._loc()
        lhs = self._parse_concat_expr()
        while self._check(KIND_OP) and self._peek().value in self.CMP_OPS:
            op = self._advance().value
            rhs = self._parse_concat_expr()
            lhs = ExprOp(op, lhs, rhs, loc)
        return lhs

    def _parse_concat_expr(self) -> Any:
        loc = self._loc()
        lhs = self._parse_add_expr()
        while self._check(KIND_OP, '++'):
            op = self._advance().value
            rhs = self._parse_add_expr()
            lhs = ExprOp(op, lhs, rhs, loc)
        return lhs

    def _parse_add_expr(self) -> Any:
        loc = self._loc()
        lhs = self._parse_mul_expr()
        while self._check(KIND_OP, '+') or self._check(KIND_OP, '-'):
            op = self._advance().value
            rhs = self._parse_mul_expr()
            lhs = ExprOp(op, lhs, rhs, loc)
        return lhs

    def _parse_mul_expr(self) -> Any:
        loc = self._loc()
        lhs = self._parse_cons_expr()
        while self._check(KIND_OP) and self._peek().value in ('*', '÷', '/', '^') \
                or self._check(KIND_KEYWORD, 'mod'):
            op = self._advance().value
            rhs = self._parse_cons_expr()
            lhs = ExprOp(op, lhs, rhs, loc)
        return lhs

    def _parse_cons_expr(self) -> Any:
        loc = self._loc()
        lhs = self._parse_unary_expr()
        if self._check(KIND_PUNCT, '::'):
            self._advance()
            rhs = self._parse_cons_expr()   # right-associative
            return ExprOp('::', lhs, rhs, loc)
        return lhs

    def _parse_unary_expr(self) -> Any:
        loc = self._loc()
        if self._check(KIND_OP, '-'):
            self._advance()
            operand = self._parse_unary_expr()
            return ExprUnary('-', operand, loc)
        if self._check(KIND_OP, '¬'):
            self._advance()
            operand = self._parse_unary_expr()
            return ExprUnary('¬', operand, loc)
        return self._parse_with_expr()

    def _parse_with_expr(self) -> Any:
        loc = self._loc()
        expr = self._parse_app_expr()
        if self._check(KIND_KEYWORD, 'with'):
            self._advance()
            self._eat(KIND_PUNCT, '(')
            dict_ = self._parse_expr()
            self._eat(KIND_PUNCT, ')')
            extra_args = []
            while self._is_app_arg_start():
                extra_args.append(self._parse_app_arg())
            return ExprWith(expr, dict_, extra_args, loc)
        return expr

    def _parse_app_expr(self) -> Any:
        loc = self._loc()
        head = self._parse_app_head()
        while self._is_app_arg_start():
            arg = self._parse_app_arg()
            head = ExprApp(head, arg, loc)
        return head

    def _parse_app_head(self) -> Any:
        if self._check(KIND_KEYWORD, 'handle'):
            return self._parse_handle_expr()
        if self._check(KIND_KEYWORD, 'match'):
            return self._parse_match_expr()
        return self._parse_atom_expr()

    def _is_app_arg_start(self) -> bool:
        t = self._peek()
        if t.kind == KIND_NAT:
            return True
        if t.kind in (KIND_TEXT, KIND_RAWTEXT, KIND_BYTES, KIND_HEXBYTES):
            return True
        if t.kind == KIND_SNAKE:
            return True
        if t.kind == KIND_PASCAL:
            return True
        if t.kind == KIND_TYPEVAR:
            return True
        if t.kind == KIND_ROWVAR:
            return True
        if t.kind == KIND_PUNCT and t.value == '(':
            return True
        if t.kind == KIND_PUNCT and t.value == '[':
            return True
        # Note: '{' is NOT an app arg start. Record literals as args must be
        # parenthesized: f ({x = 1, y = 2}). This prevents match/handle braces
        # from being consumed as record-literal arguments to the scrutinee.
        if t.kind == KIND_OP and t.value == '⊤':
            return True
        if t.kind == KIND_KEYWORD and t.value in ('True', 'False', 'Unit', 'Never'):
            return True
        if t.kind == KIND_KEYWORD and t.value in ('handle', 'match'):
            return True
        return False

    def _parse_app_arg(self) -> Any:
        if self._check(KIND_KEYWORD, 'handle'):
            return self._parse_handle_expr()
        if self._check(KIND_KEYWORD, 'match'):
            return self._parse_match_expr()
        return self._parse_atom_expr()

    def _parse_handle_expr(self) -> ExprHandle:
        loc = self._loc()
        self._eat(KIND_KEYWORD, 'handle')
        comp = self._parse_expr()
        self._eat(KIND_PUNCT, '{')
        arms = []
        while self._check(KIND_PUNCT, '|'):
            arms.append(self._parse_handler_arm())
        self._eat(KIND_PUNCT, '}')
        return ExprHandle(comp, arms, loc)

    def _parse_handler_arm(self) -> Any:
        loc = self._loc()
        self._eat(KIND_PUNCT, '|')
        if self._check(KIND_KEYWORD, 'return'):
            self._advance()
            pat = self._parse_pattern()
            self._eat(KIND_OP, '→')
            body = self._parse_expr()
            return HandlerReturn(pat, body, loc)
        once = bool(self._try_eat(KIND_KEYWORD, 'once'))
        op_name = self._eat(KIND_SNAKE).value
        arg_pats = []
        # Consume atom patterns until the last snake before →
        # The last snake before → is the continuation variable (resume).
        # Strategy: peek ahead to find the last snake name before the →.
        while not self._at_end():
            # If next is snake and the one after is →, this is the resume var
            if self._check(KIND_SNAKE) and self._check2(KIND_OP, '→'):
                break
            if not self._is_atom_pat_start():
                break
            arg_pats.append(self._parse_atom_pat())
        resume = self._eat(KIND_SNAKE).value
        self._eat(KIND_OP, '→')
        body = self._parse_expr()
        return HandlerOp(once, op_name, arg_pats, resume, body, loc)

    def _parse_match_expr(self) -> ExprMatch:
        loc = self._loc()
        self._eat(KIND_KEYWORD, 'match')
        scrutinee = self._parse_expr()
        self._eat(KIND_PUNCT, '{')
        arms = []
        while self._check(KIND_PUNCT, '|'):
            arms.append(self._parse_match_arm())
        self._eat(KIND_PUNCT, '}')
        return ExprMatch(scrutinee, arms, loc)

    def _parse_match_arm(self) -> tuple[Any, Any, Any]:
        self._eat(KIND_PUNCT, '|')
        pat = self._parse_pattern()
        guard = None
        if self._check(KIND_KEYWORD, 'if'):
            self._advance()
            guard = self._parse_app_expr()
        self._eat(KIND_OP, '→')
        body = self._parse_expr()
        return (pat, guard, body)

    def _parse_atom_expr(self) -> Any:
        loc = self._loc()
        t = self._peek()

        # Boolean / unit / never keyword literals
        if t.kind == KIND_KEYWORD and t.value in ('True', 'False', 'Unit', 'Never'):
            self._advance()
            return ExprVar(QualName([t.value], loc), loc)

        # Unit: ()
        if t.kind == KIND_PUNCT and t.value == '(':
            self._advance()
            if self._check(KIND_PUNCT, ')'):
                self._advance()
                return ExprUnit(loc)
            first = self._parse_expr()
            if self._try_eat(KIND_PUNCT, ','):
                elems = [first, self._parse_expr()]
                while self._try_eat(KIND_PUNCT, ','):
                    elems.append(self._parse_expr())
                self._eat(KIND_PUNCT, ')')
                return ExprTuple(elems, loc)
            self._eat(KIND_PUNCT, ')')
            return first

        # List literal: [a, b, c] or []
        if t.kind == KIND_PUNCT and t.value == '[':
            return self._parse_list_expr()

        # Record literal: { field = expr, ... }
        if t.kind == KIND_PUNCT and t.value == '{':
            return self._parse_record_expr()

        # Nat literal
        if t.kind == KIND_NAT:
            self._advance()
            return ExprNat(t.value, loc)

        # Text literals
        if t.kind == KIND_TEXT:
            self._advance()
            return ExprText(t.value, loc)
        if t.kind == KIND_RAWTEXT:
            self._advance()
            return ExprRawText(t.value, loc)
        if t.kind == KIND_BYTES:
            self._advance()
            return ExprBytes(t.value, loc)
        if t.kind == KIND_HEXBYTES:
            self._advance()
            return ExprHexBytes(t.value, loc)

        # ⊤ unit value
        if t.kind == KIND_OP and t.value == '⊤':
            self._advance()
            return ExprUnit(loc)

        # Variable or constructor (possibly qualified)
        if t.kind in (KIND_SNAKE, KIND_TYPEVAR, KIND_ROWVAR, KIND_PASCAL):
            name = self._parse_qual_name()
            return ExprVar(name, loc)

        raise self._error(f"unexpected token {t.kind!r} {t.value!r} in expression")

    def _parse_list_expr(self) -> ExprList:
        loc = self._loc()
        self._eat(KIND_PUNCT, '[')
        if self._check(KIND_PUNCT, ']'):
            self._advance()
            return ExprList([], loc)
        elems = [self._parse_expr()]
        while self._try_eat(KIND_PUNCT, ','):
            if self._check(KIND_PUNCT, ']'):
                break
            elems.append(self._parse_expr())
        self._eat(KIND_PUNCT, ']')
        return ExprList(elems, loc)

    def _parse_record_expr(self) -> ExprRecord:
        loc = self._loc()
        self._eat(KIND_PUNCT, '{')
        fields = []
        while not self._check(KIND_PUNCT, '}'):
            f_name = self._eat(KIND_SNAKE).value
            self._eat(KIND_PUNCT, '=')
            f_val = self._parse_expr()
            fields.append((f_name, f_val))
            if not self._try_eat(KIND_PUNCT, ','):
                break
        self._eat(KIND_PUNCT, '}')
        return ExprRecord(fields, loc)

    # =========================================================================
    # Patterns
    # =========================================================================

    def _parse_pattern(self) -> Any:
        return self._parse_or_pat()

    def _parse_or_pat(self) -> Any:
        loc = self._loc()
        first = self._parse_as_pat()
        if not self._check(KIND_PUNCT, '|'):
            return first
        pats = [first]
        while self._check(KIND_PUNCT, '|'):
            self._advance()
            pats.append(self._parse_as_pat())
        return PatOr(pats, loc)

    def _parse_as_pat(self) -> Any:
        loc = self._loc()
        pat = self._parse_cons_pat()
        if self._check(KIND_KEYWORD, 'as'):
            self._advance()
            name = self._eat(KIND_SNAKE).value
            return PatAs(pat, name, loc)
        return pat

    def _parse_cons_pat(self) -> Any:
        loc = self._loc()
        # Constructor pattern: PascalName AtomPat* or keyword constructor (True/False/Unit/Never)
        t = self._peek()
        if t.kind == KIND_PASCAL or (t.kind == KIND_KEYWORD and t.value in ('True', 'False', 'Unit', 'Never')):
            if t.kind == KIND_PASCAL:
                name = self._parse_pascal_qual()
            else:
                self._advance()
                name = QualName([t.value], loc)
            args = []
            while self._is_atom_pat_start():
                args.append(self._parse_atom_pat())
            return PatCon(name, args, loc)
        pat = self._parse_atom_pat()
        # Infix cons: pat :: pat
        if self._check(KIND_PUNCT, '::'):
            self._advance()
            tail = self._parse_cons_pat()
            return PatCons(pat, tail, loc)
        return pat

    def _is_atom_pat_start(self) -> bool:
        t = self._peek()
        if t.kind in (KIND_SNAKE, KIND_TYPEVAR, KIND_ROWVAR):
            return t.value != '_' or True
        if t.kind == KIND_PASCAL:
            return True
        if t.kind == KIND_KEYWORD and t.value in ('True', 'False', 'Unit', 'Never'):
            return True
        if t.kind == KIND_NAT or t.kind == KIND_TEXT or t.kind == KIND_BYTES:
            return True
        if t.kind == KIND_PUNCT and t.value in ('(', '[', '{'):
            return True
        if t.kind == KIND_OP and t.value == '_':
            return True
        return False

    def _parse_atom_pat(self) -> Any:
        loc = self._loc()
        t = self._peek()

        # Wildcard
        if t.kind == KIND_SNAKE and t.value == '_':
            self._advance()
            return PatWild(loc)
        if t.kind == KIND_PUNCT and t.value == '_':
            self._advance()
            return PatWild(loc)

        # Nat literal
        if t.kind == KIND_NAT:
            self._advance()
            return PatNat(t.value, loc)

        # Text literal
        if t.kind == KIND_TEXT:
            self._advance()
            return PatText(t.value, loc)

        # Bytes literal
        if t.kind == KIND_BYTES or t.kind == KIND_HEXBYTES:
            self._advance()
            return PatNat(0, loc)   # placeholder; bytes patterns rare in bootstrap

        # Variable pattern (snake_case only, not keyword)
        if t.kind in (KIND_SNAKE, KIND_TYPEVAR, KIND_ROWVAR):
            if t.kind == KIND_SNAKE and t.value == '_':
                self._advance()
                return PatWild(loc)
            self._advance()
            return PatVar(t.value, loc)

        # Parenthesized or tuple pattern
        if t.kind == KIND_PUNCT and t.value == '(':
            self._advance()
            first = self._parse_pattern()
            if self._try_eat(KIND_PUNCT, ','):
                pats = [first, self._parse_pattern()]
                while self._try_eat(KIND_PUNCT, ','):
                    pats.append(self._parse_pattern())
                self._eat(KIND_PUNCT, ')')
                return PatTuple(pats, loc)
            self._eat(KIND_PUNCT, ')')
            return first

        # List pattern
        if t.kind == KIND_PUNCT and t.value == '[':
            return self._parse_list_pat()

        # Constructor (bare PascalName with no args — args consumed by caller)
        if t.kind == KIND_PASCAL:
            name = self._parse_pascal_qual()
            return PatCon(name, [], loc)

        # Keyword constructors: True, False, Unit, Never
        if t.kind == KIND_KEYWORD and t.value in ('True', 'False', 'Unit', 'Never'):
            self._advance()
            return PatCon(QualName([t.value], loc), [], loc)

        raise self._error(f"expected pattern, got {t.kind!r} {t.value!r}")

    def _parse_list_pat(self) -> PatList:
        loc = self._loc()
        self._eat(KIND_PUNCT, '[')
        if self._check(KIND_PUNCT, ']'):
            self._advance()
            return PatList([], loc)
        pats = [self._parse_pattern()]
        while self._try_eat(KIND_PUNCT, ','):
            if self._check(KIND_PUNCT, ']'):
                break
            pats.append(self._parse_pattern())
        self._eat(KIND_PUNCT, ']')
        return PatList(pats, loc)

    # =========================================================================
    # Predicates (parsed but ignored in bootstrap)
    # =========================================================================

    def _parse_pred(self) -> Any:
        """
        Parse a predicate expression. In the bootstrap we store the raw token
        sequence and don't attempt to evaluate or check it.
        """
        tokens = []
        depth = 0
        while not self._at_end():
            t = self._peek()
            if t.kind == KIND_PUNCT and t.value == '(':
                depth += 1
            if t.kind == KIND_PUNCT and t.value == ')':
                if depth == 0:
                    break
                depth -= 1
            tokens.append(self._advance())
        return ('pred_tokens', tokens)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(tokens: list[Token], filename: str = '<stdin>') -> Program:
    """
    Parse a Gallowglass restricted-dialect token stream into an AST.

    Args:
        tokens:   Output of bootstrap.lexer.lex().
        filename: Used in error messages.

    Returns:
        Program AST node.

    Raises:
        ParseError on any syntax error.
    """
    return Parser(tokens, filename).parse_program()
