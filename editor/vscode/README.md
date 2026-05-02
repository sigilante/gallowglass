# Gallowglass — VSCode language support

Syntax highlighting and basic language configuration for [Gallowglass](https://github.com/sigilante/gallowglass) `.gls` files.

## What this provides

- Token highlighting for keywords, types, contracts, effect rows, pin
  literals, lambdas, qualifiers, and Unicode operators (`→`, `λ`, `∀`,
  `≤`, `∈`, etc. plus their ASCII equivalents `->`, `<=`, ...).
- Line (`-- ...`) and block (`{- ... -}`) comments.
- Auto-closing of brackets and quotes.
- Bracket matching for `{}` `()` `[]`.
- Indentation hints around `match`, `handle`, `let`, `class`, `instance`.

## What this does *not* provide

- Hover types, goto-definition, completions — those would require a real
  language server. The Gallowglass repo ships an MCP server in
  `bootstrap/mcp_server.py` that exposes `infer_type` /
  `compile_snippet` / `explain_effect_row` / `render_fragment`. If you
  want LSP-style hover, the Pre-2 infrastructure (`bootstrap/ide.py`) is
  the place to plug it in — but no LSP shim ships today.
- Semantic highlighting. The grammar is purely lexical; it cannot tell
  a constructor reference from a type reference, so both PascalCase
  forms get the same scope (`entity.name.type.gallowglass`). Themes
  that distinguish them via semantic tokens won't help here.

## Installation

### Local development

Symlink (or copy) this directory into your VSCode extensions folder:

```sh
# macOS / Linux
ln -s "$(pwd)/editor/vscode" ~/.vscode/extensions/gallowglass-0.0.1

# Windows (PowerShell)
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.vscode\extensions\gallowglass-0.0.1" -Target "$(pwd)/editor/vscode"
```

Reload VSCode. Open any `.gls` file; the language should be detected
automatically by the file extension.

### Packaging as a `.vsix`

If you want a portable install:

```sh
cd editor/vscode
npm install -g @vscode/vsce
vsce package
# → gallowglass-0.0.1.vsix
code --install-extension gallowglass-0.0.1.vsix
```

## Other editors

The TextMate grammar in `syntaxes/gallowglass.tmLanguage.json` is the
portable artifact. Several other editors consume it directly:

- **Sublime Text** — drop the `.tmLanguage.json` (or convert with
  `tmlanguage` tools to `.sublime-syntax`) into your User packages.
- **Atom / Pulsar** — wrap in a small Atom package; the grammar entry
  point is the same.
- **GitHub web rendering** — github/linguist accepts TextMate grammars
  via Pull Request to the linguist repo if you want `.gls` files to
  render with highlighting on github.com.
- **JetBrains IDEs** — supports TextMate bundles via the TextMate plugin.

The language configuration (`language-configuration.json`) is
VSCode-specific and won't transfer; each editor has its own format for
brackets / comments / auto-close.

## Verifying the grammar

`samples/syntax.gls` is a contrived file that exercises every form the
grammar recognises — keywords, contracts, effects, pins, interpolation,
qualifiers, type variables, the lot. Open it in VSCode after installing
the extension; visually check that everything highlights as expected.
The Developer: Inspect Editor Tokens and Scopes command (Ctrl-Shift-P
in VSCode) shows the scope under the cursor — useful when a token is
miscoloured and you want to know which pattern claimed it.

## Keeping the grammar in sync with the language

The grammar is hand-maintained. When the lexer in
`bootstrap/lexer.py` gains a new keyword, operator, or literal form, the
grammar should be updated to match. Test by opening
`samples/syntax.gls` and adding an example of the new form; if it
doesn't highlight, add a pattern. There is no automated drift check;
the syntax sample plus the lexer's `KEYWORDS` / `UNICODE_OPS` /
`PUNCT_*` constants are the canonical source of truth.
