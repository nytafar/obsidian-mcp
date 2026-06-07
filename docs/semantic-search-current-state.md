# Semantic Search — Current State

> A factual snapshot of how semantic (vector) search works in this server today,
> end to end, with the mechanics, the good parts, and the honest constraints.
> Citations are `path:line`. Companion to the retrieval brief (forward-looking
> ranking design) and the MCP architecture analysis (tooling/ops).

## Pipeline, end to end

**Index time** (`src/services/indexer.py`, `src/services/embeddings.py`)
1. Indexer runs on startup, then every `index_interval_seconds` (default **300s**,
   `config.py:12`). Hash-based change detection (`content_hash`); only changed
   notes are re-embedded (`embedded_content_hash` gates it).
2. `parse_frontmatter(raw)` splits YAML from body; **body-only content is
   embedded** (`indexer.py:96`). Frontmatter is stored separately as JSONB.
3. `clean_for_embedding` strips fenced code blocks (``` and `~~~`) before
   embedding so data dumps don't pollute vector space (`embeddings.py:26`).
   Inline backticks and indented code are kept.
4. `chunk_text` — fixed **512-token** windows (≈ `512×4` chars), **overlap 0**
   (`config.py:15-18`, deliberate, citing 2025 chunking benchmarks). Pure
   character-window split; no heading/semantic awareness.
5. Embeddings via a pluggable provider (`get_provider()` singleton):
   - **Ollama** (default) — bge-m3, **1024-dim**, one HTTP POST per chunk to
     `/api/embed`, **dense vector only** (`embeddings.py:71-79`).
   - **OpenAI** — batched (96/req), retry/backoff (`embeddings.py:97-164`).
6. Chunks stored in `note_embeddings` (`note_id, chunk_index, chunk_text,
   embedding Vector(dim)`), HNSW index `m=16, ef_construction=64`,
   `vector_cosine_ops` (`models/db.py:148-157`).
7. Files matching `embedding_exclude_patterns` (default Excalidraw) are skipped —
   keyword-searchable but unvectorized (`config.py:19-23`).

**Query time** (`src/services/embeddings.py:213-278` → `tools.py:238` →
`server.py:131`)
1. Embed the query (single dense vector).
2. `SET LOCAL hnsw.ef_search = 80` and `random_page_cost = 1.1` (per-transaction;
   raises recall@10 to ~98% per the in-code note).
3. `ORDER BY embedding <=> query` (cosine distance), `LIMIT overfetch` where
   `overfetch = max(limit*5, 50)`.
4. Optional filters via `apply_note_filters`: `folder` (path prefix LIKE),
   `tags` (`@>` array containment), `frontmatter` (`@>` JSONB containment),
   `user_id`.
5. **Dedup per note in Python** — keep the single best-matching chunk per note,
   truncate to `limit` (`embeddings.py:256-264`).
6. Return `{path, title, tags, chunk[:500], chunk_index, similarity}`; the MCP
   tool layer re-truncates the preview to 200 chars (`tools.py:264`).

**Sibling vector tool:** `find_related(path)` averages a note's chunk embeddings
and runs the same cosine + dedup over the rest of the vault (`tools.py:514-595`).

## What's solid

- **Recall is good.** HNSW + `ef_search=80` + 5× overfetch + per-note dedup is a
  sound, production-shaped vector path. A single verbose note can't dominate.
- **Embedding hygiene is ahead of the curve** — body-only (no YAML noise) and
  code-fence stripping before embedding. (memweave, by contrast, embeds raw
  frontmatter as text.)
- **Multilingual model.** bge-m3 handles Norwegian/English content natively in the
  vector arm.
- **Pluggable provider + dimension guard** (startup `pg_attribute` check;
  `reset-embeddings` workflow). Clean operational story.
- **Filters compose with vectors** — folder/tags/frontmatter narrow the candidate
  set in SQL before ranking.

## Constraints / gaps (today)

- **Semantic and keyword are two separate tools; never fused.** No hybrid score.
  The agent must choose `semantic_search` vs `keyword_search`. (Biggest single
  ranking gap.)
- **No reranking of any kind** — no recency, no maturity/`state`, no `kind` prior,
  no cross-encoder. Pure cosine order.
- **Keyword arm is English-only.** `full_text_search` uses
  `websearch_to_tsquery('english', …)` (`search.py:18`) — stems English, serves
  Norwegian content poorly.
- **Ollama path is dense-only.** `/api/embed` returns one pooled dense vector
  (`embeddings.py:79`); it does **not** expose bge-m3's learned-sparse or ColBERT
  outputs. Any sparse/late-interaction/late-chunking upgrade requires a different
  serving path (FlagEmbedding / TEI / Infinity), not a config flip.
- **Index lag is invisible.** Search reads the index; a just-written note is
  unsearchable for ≤5 min, with no freshness signal to the caller.
- **Frontmatter filter is containment-only.** No range (`date > X`), no `IN`, no
  "key exists" — so "recent + active" style narrowing isn't expressible.
- **Fixed output shape, double-truncated preview.** Hand-formatted string; preview
  carried at 500 then cut to 200; no structured result for chaining.
- **Chunking is structure-blind.** 512-char windows, overlap 0 — can split a fact
  or a list across chunks; no heading/section anchoring.

## One-line status

Semantic search is the **most mature subsystem** in the server: recall is good,
hygiene is good, the model is right. The deficits are in **fusion, reranking, the
English keyword arm, and result structure** — not in the vector core itself.
