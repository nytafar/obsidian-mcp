## Why

Frontmatter is parsed during indexing and stored in `notes_metadata.frontmatter` (JSONB). Tags are stored in `notes_metadata.tags` (ARRAY) with a GIN index. Neither is exposed as a filter on the search or listing tools — agents can only filter by folder prefix. Common structured queries an agent should be able to make in one call ("active project notes about X", "drafts in this folder", "recently modified meeting notes") are blocked because the relevant fields are invisible to the tools. The data is sitting there; we just need to expose it.

## What Changes

- Add a shared filter helper (`src/services/filters.py`) that applies optional `folder`, `tags`, and `frontmatter` filters to a SQLAlchemy `select` over `NoteMetadata`, using existing indexes (LIKE escape on folder, ARRAY containment on tags, JSONB containment on frontmatter).
- Add optional `tags: list[str]` and `frontmatter: dict[str, str | int | float | bool]` parameters to four MCP tools: `keyword_search`, `semantic_search`, `list_notes`, `get_recent`.
- Refactor `full_text_search` from raw `text()` SQL to a SQLAlchemy `select` so it can compose with the filter helper. `semantic_search` already uses an ORM `select`; route its filters through the helper too.
- Sharpen the docstrings on `keyword_search` and `semantic_search` so the difference is crisp (keyword for exact identifiers/proper nouns; semantic for conceptual queries) — neither tool is described as "primary".
- Log usage with the new params in `usage_logs.params` (existing `_tracked` decorator already truncates).

This is **non-breaking** — all new parameters are optional, defaults preserve current behavior.

## Capabilities

### New Capabilities
- `note-filters`: Optional tag and frontmatter filtering applied at the SQL layer to the search and listing MCP tools, using the existing GIN and JSONB indexes.

### Modified Capabilities
<!-- No existing specs in openspec/specs/. -->

## Impact

- **Code**:
  - `src/services/filters.py` (new) — shared filter helper.
  - `src/services/search.py` — refactor `full_text_search` to ORM `select`; route through filter helper.
  - `src/services/embeddings.py` — extend `semantic_search` signature; route through filter helper.
  - `src/mcp_server/tools.py` — extend `_impl` functions for the four affected tools; pass new params to underlying services.
  - `src/mcp_server/server.py` — add `tags` and `frontmatter` parameters to the four tool definitions; rewrite `keyword_search` and `semantic_search` docstrings to describe each tool's use case symmetrically.
- **APIs**: Four MCP tools gain optional parameters. No removals, no signature changes for required args.
- **DB**: No schema changes. Uses existing `notes_metadata.tags` (ARRAY+GIN), `notes_metadata.frontmatter` (JSONB), `notes_metadata.file_path`, and `notes_metadata.content_tsvector`.
- **Dependencies**: None new.
- **Performance**: Filters reduce candidate sets before ranking and limit, so ranked queries return faster on selective filters. JSONB containment with small filter dicts is fast even without a JSONB-specific index at this scale.
