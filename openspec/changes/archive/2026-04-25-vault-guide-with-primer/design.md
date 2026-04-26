## Context

`get_vault_context` exists at `src/mcp_server/tools.py:228-237`. Today it's a 7-line wrapper around `read_file("CLAUDE.md")` that returns the raw note content. Two other tools' docstrings (`src/mcp_server/server.py:153, :169`) instruct the agent to "Call get_vault_context first…", which is the only thing tying this tool into the rest of the surface.

The server is moving from a single-user deployment (Max's vault) toward something other users can self-host. The current design forces every user to (a) know to write a `CLAUDE.md`, (b) know what to put in it, and (c) include Obsidian-syntax knowledge that is identical across every install. Each install duplicating the same primer text by hand is wasted effort and error-prone.

Recent precedent for an MCP-tool rename: commits `bbf8dc1` (`search_notes` → `keyword_search`) and `1fc2633` (added `keyword_search` recognition in usage logs). Those landed without a DB migration; the `usage_logs.tool` column is plain text and accepts new values transparently.

## Goals / Non-Goals

**Goals:**
- Ship a server that is useful to a brand-new user with an empty vault, no `CLAUDE.md`, and no prior context about Obsidian-MCP integration.
- Keep the per-vault customization story exactly as it is today (edit `CLAUDE.md` in the vault root, version-controlled with notes, editable from inside Obsidian).
- Rename the tool to a name that better describes what it returns.
- Keep peer-tool docstrings symmetric — no tool ranked above another (per existing project preference).

**Non-Goals:**
- A database-backed settings page for the user guide. Considered and rejected: would lose the "edit alongside your notes in Obsidian" ergonomic, and adds Postgres state that isn't needed for a doc that's already version-controlled in the vault.
- Per-API-key guides. The guide is per-install, not per-key.
- A migration shim that keeps `get_vault_context` working as an alias. The server is single-user today; agents can adapt on the next turn.
- Auto-generating vault conventions (folder structure inference, tag taxonomy detection). Out of scope; users write `CLAUDE.md` manually.

## Decisions

### 1. Primer is a Markdown file shipped with the source, loaded at import time

Store the Obsidian primer as `src/mcp_server/vault_guide_primer.md` and read it once into a module-level constant at import. The tool then concatenates `PRIMER + "\n\n" + claude_md_section`.

**Why a file over an inline Python string:** the primer is ~100 lines of Markdown with code fences, headings, and examples. Inline triple-quoted strings make code review hard, break syntax highlighting, and tempt people to mix Python escaping into Markdown.

**Why module-level constant over per-call read:** the primer doesn't change at runtime; reading it from disk on every tool call is wasted I/O. If a developer edits the primer they'll restart the server anyway (current dev loop already requires this for code changes). The container image bakes the primer in, so there's no "user edits primer at runtime" use case.

**Alternative considered:** Jinja template. Rejected — there's nothing dynamic about the primer text. A static file is simpler.

### 2. Missing `CLAUDE.md` returns helpful onboarding text, not an error

Replace today's `FileNotFoundError` branch (which returns `"Vault context not available (CLAUDE.md not found)"`) with a positive message that tells the agent (and, transitively, the user) how to create one. Example:

> ## Vault-Specific Conventions
>
> No `CLAUDE.md` found in the vault root. To teach the agent about your folder
> structure, naming conventions, tag taxonomy, or task workflow, create a
> `CLAUDE.md` file at the vault root. The agent will pick it up automatically.

**Why:** the most common path for a brand-new user is "no CLAUDE.md yet." Returning an error-flavored response makes the tool feel broken; returning instructions makes it self-documenting.

### 3. Other tools' docstrings reference the new name with neutral framing

Today (`server.py:153, :169`):

> IMPORTANT: Call get_vault_context first to learn naming conventions, folder placement rules, and tag taxonomy before creating notes.

After:

> See `get_vault_guide` for vault conventions (Obsidian syntax, folder placement, tag taxonomy).

**Why:** project preference (recorded in feedback memory) is for symmetric peer tools, not a "primary" tool with others ranked beneath. The new docstring still surfaces `get_vault_guide` as relevant context but doesn't compel a call ordering. Agents that need the guide will fetch it; agents that don't, won't.

### 4. No DB migration; usage logs absorb the rename naturally

The `usage_logs.tool` column is freeform text. Existing rows with `tool='get_vault_context'` stay as-is for historical accuracy; new rows record `tool='get_vault_guide'`. The control panel's tool-list rendering already handles arbitrary tool names (it just groups by string).

**Alternative considered:** rewrite old rows to the new name. Rejected — it falsifies history. The two names refer to slightly different behaviors (raw `CLAUDE.md` vs. primer + `CLAUDE.md`), so keeping them distinct is more honest.

## Risks / Trade-offs

- **Risk**: agents in flight call `get_vault_context` and get a "tool not found" error.
  **Mitigation**: single-user deployment today; user can re-trigger the agent. If the rename causes friction in practice, an alias function delegating to `get_vault_guide_impl` is a 3-line add.

- **Risk**: the primer drifts out of date as Obsidian evolves (e.g., new syntax features).
  **Mitigation**: it's a single Markdown file in the repo; updating it is a one-line PR. Acceptable maintenance cost.

- **Risk**: returning a long primer on every call inflates context for agents that already know Obsidian syntax.
  **Mitigation**: the primer should stay tight (~100-150 lines / ~2KB). Agents that already know the syntax can skip the tool entirely; nothing forces a call. If the size becomes a real problem we can split into `get_obsidian_primer` / `get_vault_conventions`, but YAGNI for now.

- **Trade-off**: the rename breaks any external script or skill that hard-codes `get_vault_context`. The Claude Code Obsidian skill mentioned in conversation will need its docstring updated.

## Migration Plan

1. Land the rename + primer in a single PR (no feature flag — this is a small, atomic change).
2. Deploy via `make deploy`.
3. Update the Claude Code Obsidian skill (out of repo) to reference `get_vault_guide`.
4. Update README to mention `CLAUDE.md` as the per-vault customization point.

**Rollback**: revert the commit; the old tool name returns. No data migration required.

## Open Questions

- Should the primer mention `make deploy` / control panel URLs, or stay pure-Obsidian? **Tentative answer:** stay pure-Obsidian. Server-operator docs belong in README, not in the agent-facing guide.
- Should `get_vault_guide` accept an argument like `section="primer"|"vault"|"both"` (default both)? **Tentative answer:** no — adds surface area for negligible benefit. Revisit only if primer size becomes a real problem.
