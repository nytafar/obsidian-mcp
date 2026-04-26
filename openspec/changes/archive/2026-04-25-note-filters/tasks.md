## 1. Filter helper module

- [x] 1.1 Create `src/services/filters.py` with `apply_note_filters(stmt: Select, *, folder: str | None = None, tags: list[str] | None = None, frontmatter: dict | None = None) -> Select`
- [x] 1.2 Implement folder predicate: escape `\`, `%`, `_` in the folder string and apply `NoteMetadata.file_path.like(f"{escaped}%", escape="\\")`
- [x] 1.3 Implement tags predicate using `NoteMetadata.tags.contains(tags)` (ARRAY containment, hits the GIN index)
- [x] 1.4 Implement frontmatter predicate using `NoteMetadata.frontmatter.contains(frontmatter)` (JSONB `@>`)
- [x] 1.5 Short-circuit each predicate when its argument is None or empty (no-op, not a falsy WHERE)
- [x] 1.6 Remove the duplicated `_escape_like` from `src/mcp_server/tools.py`; have callers route through the helper

## 2. Refactor `full_text_search` to ORM `select`

- [x] 2.1 In `src/services/search.py`, replace the raw `text()` SQL with a SQLAlchemy `select(NoteMetadata, func.ts_rank_cd(NoteMetadata.content_tsvector, func.websearch_to_tsquery('english', query)).label("rank"))`
- [x] 2.2 Add the tsvector match predicate via `NoteMetadata.content_tsvector.op("@@")(func.websearch_to_tsquery('english', query))`
- [x] 2.3 Update the function signature to accept `tags` and `frontmatter` and route through `apply_note_filters`
- [x] 2.4 Order by `rank desc`, apply `limit`, return rows in the existing dict shape (`path`, `title`, `tags`, `rank`)

## 3. Update `semantic_search`

- [x] 3.1 Extend `src/services/embeddings.py::semantic_search` signature to accept `tags` and `frontmatter`
- [x] 3.2 Route the existing folder filter and the new filters through `apply_note_filters` after the join to `NoteMetadata`
- [x] 3.3 Preserve existing return shape (`path`, `title`, `tags`, `chunk`, `chunk_index`, `similarity`)

## 4. Switch `list_notes_impl` to a DB read

- [x] 4.1 Replace the filesystem walk in `list_notes_impl` (`src/mcp_server/tools.py`) with a `select(NoteMetadata)` ordered by `modified_at desc`
- [x] 4.2 Route filters through `apply_note_filters`
- [x] 4.3 Map result rows to the existing response shape (`path` ← `file_path`, `size` ← `file_size`, `modified` ← unix timestamp from `modified_at`)
- [x] 4.4 Update the docstring (in `server.py`) to note that results come from the index and may lag on-disk changes by up to one index interval

## 5. Update `get_recent_impl`

- [x] 5.1 Route the existing folder filter and the new `tags` / `frontmatter` filters through `apply_note_filters`
- [x] 5.2 Drop the inline LIKE-escape now that the helper handles it

## 6. MCP tool registrations

- [x] 6.1 Add `tags: list[str] | None = None` and `frontmatter: dict | None = None` parameters to `keyword_search`, `semantic_search`, `list_notes`, and `get_recent` in `src/mcp_server/server.py`
- [x] 6.2 Forward both into the corresponding `_impl` functions
- [x] 6.3 Update the `_tracked` decorators on the four `_impl` functions in `tools.py` to include `"tags"` and `"frontmatter"` in their `param_keys` so the new params land in `usage_logs.params`
- [x] 6.4 Rewrite the `keyword_search` docstring: describes use case (exact identifiers, code symbols, proper nouns, known phrases); mentions `semantic_search` as the alternative; does not call itself "primary"
- [x] 6.5 Rewrite the `semantic_search` docstring: describes use case (conceptual or paraphrased queries); mentions `keyword_search` as the alternative; does not call itself "primary"
- [x] 6.6 Update `list_notes` and `get_recent` docstrings to document the new filter params with one-line examples for each

## 7. Verification

- [x] 7.1 Manual: deploy via `make deploy`; pick a tag known to be on multiple notes, call `keyword_search(query="...", tags=[that_tag])`; confirm only matching notes return
- [x] 7.2 Manual: pick a frontmatter key/value present in some notes, call `semantic_search(query="...", frontmatter={key: value})`; confirm filtering
- [x] 7.3 Manual: call `list_notes(folder="Cards/", tags=["idea"])`; confirm folder + tag intersection
- [x] 7.4 Manual: call `get_recent(limit=10, tags=["meeting"])`; confirm tag filter
- [x] 7.5 Manual: confirm existing calls (without new params) return the same results as before via spot-checking against pre-deploy responses on the same query
- [x] 7.6 Manual: query `usage_logs` and confirm rows include `tags` and `frontmatter` in `params` when supplied
- [x] 7.7 Sanity: existing tools (`read_note`, `get_tags`, `create_note`, `edit_note`, `get_vault_context`) continue to work unchanged
