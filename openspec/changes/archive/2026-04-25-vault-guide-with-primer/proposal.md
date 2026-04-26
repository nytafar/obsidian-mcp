## Why

The current `get_vault_context` tool reads `CLAUDE.md` from the vault root and returns its raw content. This works for the original single-user deployment but assumes every install has a hand-written `CLAUDE.md`. New users — including anyone we share this server with — get no useful response, and even if they do create a `CLAUDE.md`, the tool teaches the agent nothing about Obsidian-specific syntax (wikilinks, embeds, block refs, frontmatter) that is the same across every vault. The goal is to make this server generic enough to hand to other users without losing the per-vault customization that makes it valuable today.

## What Changes

- **BREAKING**: Rename MCP tool `get_vault_context` to `get_vault_guide` (registered name, impl name, and all docstring references).
- Add a hardcoded **Obsidian primer** that ships with the server: a static document covering wikilinks `[[Note]]` / `[[Note|alias]]`, embeds `![[Note]]`, block refs `[[Note#^id]]`, heading refs `[[Note#Heading]]`, backlinks, tags `#tag/nested`, frontmatter YAML conventions, and a note that Dataview/Templater syntax in notes should be treated as literal text.
- `get_vault_guide` returns the primer **plus** the contents of `CLAUDE.md` from the vault root (when present), concatenated with clear section headers.
- When `CLAUDE.md` is absent, the response replaces today's "not available" error with a friendly note explaining how to create one (folder structure, naming conventions, tag taxonomy) so users can opt in.
- Update docstrings on other MCP tools (`keyword_search`, `semantic_search`, etc.) to reference `get_vault_guide` neutrally instead of "MUST call this first" — keeping with the project's preference for peer tools without enforced ranking.

## Capabilities

### New Capabilities
- `vault-guide`: The MCP tool that returns onboarding instructions for an agent — both generic Obsidian syntax (hardcoded primer) and per-vault conventions (sourced from `CLAUDE.md` in the vault root). Replaces the previous undocumented `get_vault_context` behavior.

### Modified Capabilities
<!-- None — no existing spec covers this tool. -->

## Impact

- **Code**:
  - `src/mcp_server/tools.py:228-237` — rename `get_vault_context_impl` → `get_vault_guide_impl`; add primer concatenation; replace `FileNotFoundError` branch with friendly "no CLAUDE.md yet" message.
  - `src/mcp_server/server.py:10` — update import.
  - `src/mcp_server/server.py:188-194` — rename registered tool function and its docstring.
  - `src/mcp_server/server.py:153, :169` — update other tool docstrings to use new name and neutral framing.
  - New file: `src/mcp_server/vault_guide_primer.md` (or equivalent Python constant) holding the static Obsidian primer text.
- **API**:
  - MCP tool surface: `get_vault_context` removed, `get_vault_guide` added. This is a breaking change for any agent that calls the old name; agents will see a "tool not found" error and adapt on their next turn.
  - Usage logs (`usage_logs.tool` column) will start recording `get_vault_guide`. The recently-renamed `keyword_search`/`semantic_search` precedent (commits `bbf8dc1`, `1fc2633`) shows the tracker already handles such renames without DB migration.
- **Docs**:
  - `README.md` and any control-panel copy referencing `get_vault_context` need updating.
  - Add a short note in README explaining that users can create `CLAUDE.md` in their vault root to teach the agent their conventions.
- **Tests**: Any test that calls `get_vault_context_impl` by name needs renaming (search shows none today, but verify during implementation).
- **Dependencies**: None.
