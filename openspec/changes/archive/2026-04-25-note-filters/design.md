## Context

The MCP server today exposes four tools that touch `notes_metadata`:

- `keyword_search` — `src/services/search.py::full_text_search` with raw `text()` SQL, accepts optional `folder` prefix.
- `semantic_search` — `src/services/embeddings.py::semantic_search` with SQLAlchemy ORM `select`, accepts optional `folder` prefix.
- `list_notes` — `src/services/vault.py::list_files`, filesystem walk, no DB filter.
- `get_recent` — direct ORM `select(NoteMetadata)` in `src/mcp_server/tools.py::get_recent_impl`, accepts optional `folder` prefix with manual LIKE escape.

`notes_metadata` already has:
- `tags` (`ARRAY[String]`) with GIN index `ix_notes_metadata_tags`.
- `frontmatter` (`JSONB`) — no dedicated index, but JSONB containment on small filter dicts at 2,577 rows is fast (~ms).
- `file_path` — used today for folder prefix LIKE.

The folder LIKE-escape logic exists in two places (`tools._escape_like` and an inline LIKE in `search.py`). This is the natural moment to consolidate.

`list_notes` is a special case: it currently walks the filesystem, not the DB. To support `tags` and `frontmatter` filters, it needs to read from `notes_metadata` instead. That's a behavior shift worth being explicit about.

## Goals / Non-Goals

**Goals:**
- One shared filter helper used by all four tools.
- AND semantics across `tags` (multi-tag means all must match).
- JSONB containment semantics for `frontmatter` (strict type match).
- Filters pushed down to SQL before ranking and limit.
- Existing calls without the new parameters keep their current behavior.

**Non-Goals:**
- OR semantics for tags (`tags_any`) — can be added later non-breakingly if asked for.
- Wildcard or regex matching on frontmatter values.
- Hybrid (RRF) search — explicitly dropped from scope after design review; keyword and semantic remain distinct peers.
- Schema changes or new indexes.
- Removing or renaming any tool.

## Decisions

### Decision: Shared `filters.py` module

**What:** New `src/services/filters.py` exporting `apply_note_filters(stmt, *, folder, tags, frontmatter) -> Select`. Takes a SQLAlchemy `select` over `NoteMetadata` and returns it with `WHERE` clauses appended.

**Why:** Three call sites today (after refactor: four). Single source of truth means consistent escaping, consistent containment semantics, and one place to add new filters later.

**Alternatives considered:**
- Inline filter logic per call site. Risks divergence (already happening with the duplicated LIKE escape).
- A query-builder class. Overkill for three filter types.

### Decision: Refactor `full_text_search` to ORM `select`

**What:** Replace the raw `text()` SQL with `select(NoteMetadata, func.ts_rank_cd(...).label("rank")).where(NoteMetadata.content_tsvector.op("@@")(func.websearch_to_tsquery(...)))`.

**Why:** Without this, the filter helper can't compose. `tsvector` operators have first-class SQLAlchemy support.

**Alternatives considered:**
- Keep raw `text()` and concatenate WHERE clauses as strings. Painful, fragile, defeats the purpose of a helper.

### Decision: Tag filter uses ARRAY containment with AND semantics

**What:** SQL: `notes_metadata.tags @> ARRAY[:t1, :t2]::text[]`. Hits the GIN index `ix_notes_metadata_tags`.

**Why:**
- ARRAY containment on a GIN-indexed column is the textbook fast path.
- AND semantics covers the typical agent use ("active projects" = `["status:active", "type:project"]`).
- OR can be added as a separate `tags_any` parameter later if the need is real.

### Decision: Frontmatter filter uses JSONB containment with strict typing

**What:** SQL: `notes_metadata.frontmatter @> :json::jsonb`, where `:json` is the filter dict serialized to JSON.

**Why:**
- JSONB containment is symmetric and indexable; with no JSONB GIN index today it's still fast at this scale.
- Strict type match (`{"status": "draft"}` matches the string `"draft"` but not the integer `0`) prevents surprising behavior. Document this in the tool docstrings.

**Alternatives considered:**
- Equality-by-key with type coercion. Adds complexity for marginal value; the agent can read frontmatter values via `read_note` and pass back the right type.

### Decision: `list_notes` switches from filesystem walk to DB read

**What:** `list_notes_impl` reads from `notes_metadata` instead of walking the filesystem. Returns the same shape (`path`, `size`, `modified`).

**Why:**
- Required to support `tags` and `frontmatter` filters (they only exist in the DB).
- Aligns with the rest of the tools — single source of truth is the indexer.

**Behavior change to be explicit about:** A note that exists on disk but hasn't been indexed yet will not appear in `list_notes`. In practice the indexer runs on startup and every 5 minutes, so the lag is bounded. After the upcoming `watchfiles` change, lag will drop to seconds.

**Alternatives considered:**
- Keep the filesystem walk for the no-filter case, switch to DB only when filters are present. More code paths, more edge cases. Reject.

### Decision: Sharpen docstrings without ranking either search tool as "primary"

**What:**
- `keyword_search`: "Full-text keyword search via PostgreSQL tsvector. Use this for exact identifiers, code symbols, proper nouns, or known phrases — anywhere semantic noise hurts."
- `semantic_search`: "Vector similarity search using bge-m3 embeddings. Use this for conceptual or paraphrased queries — anywhere exact word matching would miss the point."

**Why:** A previous version of `semantic_search` documented itself as "the primary search tool". Architecture review concluded both have distinct, well-understood use cases and the agent should pick. Symmetric phrasing makes that explicit.

## Risks / Trade-offs

- **[`list_notes` no longer reflects on-disk-but-not-yet-indexed notes]** → A note created externally won't appear until the next index pass. **Mitigation**: indexer runs every 5 min today, drops to seconds with watchfiles. Document in the `list_notes` docstring.

- **[Frontmatter strict typing surprises agents]** → `frontmatter={"due": "2026-01-01"}` won't match a note where `due` is parsed as a YAML date. **Mitigation**: indexer's `_sanitize_frontmatter` coerces non-primitive YAML values to strings, so most fields end up as strings in the JSONB. Document the strict-match rule in the tool docstring.

- **[GIN index doesn't help with empty `tags` arrays]** → A filter like `tags=[]` would be a no-op. **Mitigation**: helper short-circuits — empty list / dict means "no filter applied".

- **[Folder LIKE pattern still fragile if used outside the helper]** → After this change all tools route through the helper, but a future tool author might inline a LIKE again. **Mitigation**: docstring on the helper marks it as the only supported way to apply these filters.

## Migration Plan

1. Land the helper module (`src/services/filters.py`).
2. Refactor `full_text_search` to ORM `select` and route through the helper.
3. Update `semantic_search` to route through the helper.
4. Switch `list_notes_impl` from filesystem walk to DB read.
5. Route `get_recent_impl` through the helper.
6. Add the new parameters to MCP tool definitions in `server.py`.
7. Update docstrings.
8. Deploy via `make deploy`.

**Rollback:** Revert the commits. No schema or data changes to undo.
