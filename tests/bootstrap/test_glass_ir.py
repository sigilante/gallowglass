#!/usr/bin/env python3
"""
M17.1 tests — Glass IR AST-based renderer.

Run: python3 -m pytest tests/bootstrap/test_glass_ir.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program
from bootstrap.pin import compute_pin_id
from bootstrap.glass_ir import (
    render_fragment, render_expr, render_pattern, render_decl, render_module,
    collect_decl_deps, collect_pin_deps, render_scc_group,
    verify_roundtrip,
)


def _compile(src, module='Test'):
    """Compile source, return (resolved, compiled, pin_ids)."""
    prog = parse(lex(src, '<test>'), '<test>')
    resolved, _ = resolve(prog, module, {}, '<test>')
    compiled = compile_program(resolved, module)
    pin_ids = {fq: compute_pin_id(v) for fq, v in compiled.items()}
    return resolved, compiled, pin_ids


class TestRenderExpr(unittest.TestCase):
    """Expression rendering."""

    def test_nat(self):
        resolved, _, _ = _compile('let main = 42')
        body = resolved.decls[0].body
        self.assertEqual(render_expr(body), '42')

    def test_text(self):
        resolved, _, _ = _compile('let main = "hello"')
        body = resolved.decls[0].body
        self.assertEqual(render_expr(body), '"hello"')

    def test_var_fq_name(self):
        resolved, _, _ = _compile('let foo = 1\nlet bar = foo')
        body = resolved.decls[1].body
        rendered = render_expr(body)
        # After scope resolution, foo should be FQ
        self.assertIn('Test.foo', rendered)

    def test_lambda(self):
        resolved, _, _ = _compile('let inc = λ n → n')
        body = resolved.decls[0].body
        rendered = render_expr(body)
        self.assertIn('λ', rendered)
        self.assertIn('→', rendered)

    def test_app(self):
        resolved, _, _ = _compile('let foo = 1\nlet bar = λ n → n\nlet main = bar foo')
        body = resolved.decls[2].body
        rendered = render_expr(body)
        self.assertIn('Test.bar', rendered)
        self.assertIn('Test.foo', rendered)

    def test_match(self):
        resolved, _, _ = _compile(
            'type Color = | Red | Blue\n'
            'let swap = λ cc → match cc {\n'
            '  | Red → Blue\n'
            '  | Blue → Red\n'
            '}'
        )
        body = resolved.decls[1].body
        rendered = render_expr(body)
        self.assertIn('match', rendered)
        self.assertIn('Test.Red', rendered)
        self.assertIn('Test.Blue', rendered)

    def test_if(self):
        resolved, _, _ = _compile(
            'let check = λ bb → if bb then 1 else 0'
        )
        body = resolved.decls[0].body
        rendered = render_expr(body)
        self.assertIn('if', rendered)
        self.assertIn('then', rendered)
        self.assertIn('else', rendered)


class TestRenderPattern(unittest.TestCase):
    """Pattern rendering."""

    def test_wild(self):
        from bootstrap.ast import PatWild, Loc
        self.assertEqual(render_pattern(PatWild(Loc('<t>', 1, 1))), '_')

    def test_var(self):
        from bootstrap.ast import PatVar, Loc
        self.assertEqual(render_pattern(PatVar('foo', Loc('<t>', 1, 1))), 'foo')

    def test_nat(self):
        from bootstrap.ast import PatNat, Loc
        self.assertEqual(render_pattern(PatNat(42, Loc('<t>', 1, 1))), '42')

    def test_con(self):
        from bootstrap.ast import PatCon, PatVar, QualName, Loc
        loc = Loc('<t>', 1, 1)
        pat = PatCon(QualName(['Test', 'Some'], loc), [PatVar('val', loc)], loc)
        rendered = render_pattern(pat)
        self.assertEqual(rendered, 'Test.Some val')


class TestRenderDecl(unittest.TestCase):
    """Declaration rendering with FQ names and pin annotations."""

    def test_let_decl_fq_name(self):
        resolved, _, _ = _compile('let main = 42')
        rendered = render_decl(resolved.decls[0], 'Test')
        self.assertIn('Test.main', rendered)
        self.assertIn('42', rendered)

    def test_let_decl_with_pin(self):
        resolved, compiled, pin_ids = _compile('let main = 42')
        rendered = render_decl(resolved.decls[0], 'Test', pin_ids)
        self.assertIn('[pin#', rendered)
        self.assertIn('Test.main', rendered)

    def test_type_decl(self):
        resolved, _, _ = _compile('type Color = | Red | Green | Blue')
        rendered = render_decl(resolved.decls[0], 'Test')
        self.assertIn('type Test.Color', rendered)
        self.assertIn('Test.Red', rendered)
        self.assertIn('Test.Green', rendered)
        self.assertIn('Test.Blue', rendered)


class TestRenderFragment(unittest.TestCase):
    """Full Glass IR fragment rendering."""

    def test_fragment_header(self):
        resolved, compiled, pin_ids = _compile('let main = 42')
        fq = 'Test.main'
        frag = render_fragment(fq, resolved.decls[0], pin_id=pin_ids[fq], module='Test')
        self.assertIn('-- Snapshot: pin#', frag)
        self.assertIn('-- Source: Test.main', frag)
        self.assertIn('-- Budget: 4096 tokens', frag)

    def test_fragment_has_fq_names(self):
        resolved, compiled, pin_ids = _compile('let foo = 1\nlet bar = foo')
        fq = 'Test.bar'
        frag = render_fragment(fq, resolved.decls[1], pin_id=pin_ids[fq], module='Test')
        self.assertIn('Test.bar', frag)
        self.assertIn('Test.foo', frag)

    def test_fragment_has_pin_hash(self):
        resolved, compiled, pin_ids = _compile('let main = 42')
        fq = 'Test.main'
        frag = render_fragment(fq, resolved.decls[0], pin_id=pin_ids[fq], module='Test')
        self.assertIn('[pin#', frag)

    def test_fragment_with_deps(self):
        resolved, compiled, pin_ids = _compile('let foo = 1\nlet bar = foo')
        fq = 'Test.bar'
        deps = {'Test.foo': pin_ids['Test.foo']}
        frag = render_fragment(fq, resolved.decls[1], pin_id=pin_ids[fq],
                               module='Test', deps=deps)
        self.assertIn('@![pin#', frag)
        self.assertIn('Test.foo', frag)

    def test_fragment_no_use_directives(self):
        """Glass IR fragments should not contain use directives."""
        resolved, compiled, pin_ids = _compile('let main = 42')
        fq = 'Test.main'
        frag = render_fragment(fq, resolved.decls[0], pin_id=pin_ids[fq], module='Test')
        self.assertNotIn('use ', frag)


class TestRenderModule(unittest.TestCase):
    """Module-level rendering."""

    def test_render_module_all_decls(self):
        resolved, compiled, pin_ids = _compile(
            'type Color = | Red | Blue\n'
            'let main = Red'
        )
        manifest = {'pins': pin_ids}
        rendered = render_module(resolved, 'Test', manifest)
        self.assertIn('type Test.Color', rendered)
        self.assertIn('let Test.main', rendered)


class TestSCCGroup(unittest.TestCase):
    """M17.3: SCC group rendering."""

    def test_scc_group_format(self):
        """SCC group renders as @![pin#...] { ... } block."""
        src = (
            'let is_even = λ nn → match nn {\n'
            '  | 0 → 1\n'
            '  | _ → is_odd nn\n'
            '}\n'
            'let is_odd = λ nn → match nn {\n'
            '  | 0 → 0\n'
            '  | _ → is_even nn\n'
            '}\n'
        )
        resolved, compiled, pin_ids = _compile(src)
        rendered = render_scc_group(
            ['Test.is_even', 'Test.is_odd'],
            [resolved.decls[0], resolved.decls[1]],
            group_pin_id='ab' * 32,
            module='Test',
            pin_ids=pin_ids,
        )
        self.assertIn('@![pin#abababab]', rendered)
        self.assertIn('Test.is_even', rendered)
        self.assertIn('Test.is_odd', rendered)
        self.assertTrue(rendered.strip().endswith('}'))

    def test_codegen_records_scc_groups(self):
        """Compiler.scc_groups records multi-member SCCs."""
        src = (
            'let is_even = λ nn → match nn {\n'
            '  | 0 → 1\n'
            '  | _ → is_odd nn\n'
            '}\n'
            'let is_odd = λ nn → match nn {\n'
            '  | 0 → 0\n'
            '  | _ → is_even nn\n'
            '}\n'
        )
        from bootstrap.codegen import Compiler
        prog = parse(lex(src, '<test>'), '<test>')
        from bootstrap.scope import resolve as scope_resolve
        resolved_prog, _ = scope_resolve(prog, 'Test', {}, '<test>')
        compiler = Compiler(module='Test')
        compiler.compile(resolved_prog)
        self.assertGreater(len(compiler.scc_groups), 0)
        group = compiler.scc_groups[0]
        self.assertIn('Test.is_even', group)
        self.assertIn('Test.is_odd', group)


class TestCollectDeps(unittest.TestCase):
    """M17.2: Dependency collection for pin declarations."""

    def test_no_cross_module_deps(self):
        """Definition using only local names has no external deps."""
        resolved, _, _ = _compile('let foo = 42\nlet bar = foo')
        deps = collect_decl_deps(resolved.decls[1], 'Test')
        self.assertEqual(deps, set())

    def test_cross_module_deps(self):
        """Definition using imported names collects them as deps."""
        from bootstrap.build import build_modules
        sources = [
            ('Dep', 'let val = 42'),
            ('Main', 'use Dep\nlet main = Dep.val'),
        ]
        compiled = build_modules(sources)
        # Re-resolve Main to get its AST
        from bootstrap.scope import resolve as scope_resolve
        prog = parse(lex('use Dep\nlet main = Dep.val', '<test>'), '<test>')
        # Build module_envs from Dep
        from bootstrap.scope import Env
        dep_prog = parse(lex('let val = 42', '<dep>'), '<dep>')
        _, dep_env = scope_resolve(dep_prog, 'Dep', {}, '<dep>')
        resolved, _ = scope_resolve(prog, 'Main', {'Dep': dep_env}, '<test>')
        # Main's let main should reference Dep.val
        deps = collect_decl_deps(resolved.decls[1], 'Main')  # decls[0] is use
        self.assertIn('Dep.val', deps)

    def test_collect_pin_deps_with_manifest(self):
        """collect_pin_deps returns PinIds for cross-module refs."""
        from bootstrap.build import build_modules
        sources = [
            ('Dep', 'let val = 42'),
            ('Main', 'use Dep\nlet main = Dep.val'),
        ]
        compiled = build_modules(sources)
        pin_ids = {fq: compute_pin_id(v) for fq, v in compiled.items()}
        manifest = {'pins': pin_ids}
        # Get resolved AST for Main
        from bootstrap.scope import resolve as scope_resolve
        dep_prog = parse(lex('let val = 42', '<dep>'), '<dep>')
        _, dep_env = scope_resolve(dep_prog, 'Dep', {}, '<dep>')
        prog = parse(lex('use Dep\nlet main = Dep.val', '<test>'), '<test>')
        resolved, _ = scope_resolve(prog, 'Main', {'Dep': dep_env}, '<test>')
        deps = collect_pin_deps('Main.main', resolved.decls[1], 'Main', manifest)
        self.assertIn('Dep.val', deps)
        self.assertEqual(deps['Dep.val'], pin_ids['Dep.val'])

    def test_fragment_includes_pin_decls(self):
        """Fragment with deps includes @![pin#...] declarations."""
        resolved, compiled, pin_ids = _compile('let foo = 1\nlet bar = foo')
        deps = {'External.dep': 'a' * 64}
        frag = render_fragment('Test.bar', resolved.decls[1],
                               pin_id=pin_ids['Test.bar'],
                               module='Test', deps=deps)
        self.assertIn('@![pin#aaaaaaaa] External.dep', frag)

    def test_no_deps_no_pin_section(self):
        """Fragment without deps has no pin declarations."""
        resolved, compiled, pin_ids = _compile('let main = 42')
        frag = render_fragment('Test.main', resolved.decls[0],
                               pin_id=pin_ids['Test.main'], module='Test')
        self.assertNotIn('@!', frag)


class TestRoundtrip(unittest.TestCase):
    """M17.4: Round-trip verification."""

    def test_simple_roundtrip(self):
        """Simple nat literal round-trips."""
        resolved, compiled, _ = _compile('let main = 42')
        results = verify_roundtrip(resolved, compiled, 'Test')
        for ok, msg in results:
            self.assertTrue(ok, msg)

    def test_lambda_roundtrip(self):
        """Lambda expression round-trips."""
        resolved, compiled, _ = _compile('let inc = λ nn → nn')
        results = verify_roundtrip(resolved, compiled, 'Test')
        for ok, msg in results:
            self.assertTrue(ok, msg)

    def test_match_roundtrip(self):
        """Match expression round-trips."""
        src = (
            'type Color = | Red | Blue\n'
            'let swap = λ cc → match cc {\n'
            '  | Red → Blue\n'
            '  | Blue → Red\n'
            '}'
        )
        resolved, compiled, _ = _compile(src)
        results = verify_roundtrip(resolved, compiled, 'Test')
        for ok, msg in results:
            self.assertTrue(ok, msg)

    def test_module_roundtrip(self):
        """All declarations in a module round-trip."""
        src = 'let foo = 1\nlet bar = 2\nlet baz = 3'
        resolved, compiled, _ = _compile(src)
        results = verify_roundtrip(resolved, compiled, 'Test')
        for ok, msg in results:
            self.assertTrue(ok, msg)
        self.assertEqual(len(results), 3)


class TestTypeAnnotatedRendering(unittest.TestCase):
    """M18.2: Type annotations in Glass IR."""

    def _compile_typed(self, src, module='Test'):
        """Compile and typecheck source."""
        from bootstrap.typecheck import typecheck
        prog = parse(lex(src, '<test>'), '<test>')
        resolved, env = resolve(prog, module, {}, '<test>')
        compiled = compile_program(resolved, module)
        pin_ids = {fq: compute_pin_id(v) for fq, v in compiled.items()}
        type_env = typecheck(resolved, env, module, '<test>')
        return resolved, compiled, pin_ids, type_env

    def test_fragment_with_type_env(self):
        """Fragment with type_env renders : Type annotation."""
        resolved, _, pin_ids, type_env = self._compile_typed('let foo = 42')
        decl = resolved.decls[0]
        frag = render_fragment('Test.foo', decl, pin_id=pin_ids.get('Test.foo'),
                               module='Test', type_env=type_env)
        self.assertIn(': Nat', frag)

    def test_fragment_without_type_env(self):
        """Fragment without type_env renders no annotation (backward compatible)."""
        resolved, _, pin_ids, _ = self._compile_typed('let foo = 42')
        decl = resolved.decls[0]
        frag = render_fragment('Test.foo', decl, pin_id=pin_ids.get('Test.foo'),
                               module='Test')
        self.assertNotIn(':', frag.split('\n')[4])  # body line, no colon

    def test_nat_arrow_type(self):
        """Function type renders correctly."""
        resolved, _, pin_ids, type_env = self._compile_typed(
            'let inc = λ nn → nn')
        decl = resolved.decls[0]
        frag = render_fragment('Test.inc', decl, pin_id=pin_ids.get('Test.inc'),
                               module='Test', type_env=type_env)
        # Should have an arrow type annotation
        self.assertIn('→', frag.split('let Test.inc')[1].split('\n')[0])

    def test_polymorphic_renders_forall(self):
        """Polymorphic definition renders ∀."""
        resolved, _, pin_ids, type_env = self._compile_typed(
            'let ident = λ xx → xx')
        decl = resolved.decls[0]
        frag = render_fragment('Test.ident', decl, pin_id=pin_ids.get('Test.ident'),
                               module='Test', type_env=type_env)
        self.assertIn('∀', frag)

    def test_render_decl_with_type(self):
        """render_decl with type_env adds annotation."""
        resolved, _, pin_ids, type_env = self._compile_typed('let foo = 42')
        decl = resolved.decls[0]
        text = render_decl(decl, 'Test', pin_ids, type_env=type_env)
        self.assertIn(': Nat', text)

    def test_render_module_with_types(self):
        """render_module threads type_env correctly."""
        resolved, _, pin_ids, type_env = self._compile_typed(
            'let foo = 42\nlet bar = λ xx → xx')
        manifest = {'pins': pin_ids}
        text = render_module(resolved, 'Test', manifest, type_env=type_env)
        self.assertIn(': Nat', text)
        self.assertIn('∀', text)

    def test_module_name_mismatch_raises(self):
        """Renderer raises if type_env was built for a different module.

        Regression for the silent-footgun where typecheck() and render_*() were
        given different module names — annotations would silently disappear.
        """
        resolved, _, pin_ids, type_env = self._compile_typed(
            'let foo = 42', module='Real')
        # Build resolved separately for the wrong module name to keep the
        # render call internally consistent except for the type_env mismatch.
        prog = parse(lex('let foo = 42', '<test>'), '<test>')
        wrong_resolved, _ = resolve(prog, 'Wrong', {}, '<test>')
        decl = wrong_resolved.decls[0]
        manifest = {'pins': {}}
        with self.assertRaises(ValueError) as cm:
            render_module(wrong_resolved, 'Wrong', manifest, type_env=type_env)
        self.assertIn("'Wrong'", str(cm.exception))
        self.assertIn("'Real'", str(cm.exception))
        with self.assertRaises(ValueError):
            render_fragment('Wrong.foo', decl, module='Wrong',
                            type_env=type_env)

    def test_module_name_match_passes(self):
        """Renderer accepts type_env when the module names line up."""
        resolved, _, pin_ids, type_env = self._compile_typed(
            'let foo = 42', module='Real')
        manifest = {'pins': pin_ids}
        text = render_module(resolved, 'Real', manifest, type_env=type_env)
        self.assertIn(': Nat', text)

    def test_constrained_type_renders(self):
        """Constrained type with ⇒ renders correctly."""
        src = '''class Eq a {
  eq : a -> a -> Bool
}

let neq : Eq a => a -> a -> Bool = λ xx yy ->
  match eq xx yy {
    | True -> False
    | False -> True
  }'''
        resolved, _, pin_ids, type_env = self._compile_typed(src)
        # Find the neq decl
        from bootstrap.ast import DeclLet
        for decl in resolved.decls:
            if isinstance(decl, DeclLet) and decl.name == 'neq':
                frag = render_fragment('Test.neq', decl,
                                       pin_id=pin_ids.get('Test.neq'),
                                       module='Test', type_env=type_env)
                self.assertIn('⇒', frag)
                self.assertIn('Eq', frag)
                break
        else:
            self.fail("neq not found")


if __name__ == '__main__':
    unittest.main()
