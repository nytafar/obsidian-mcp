# Spec — Configurable Full-Text Search Language(s)

**Status:** ready to implement · **Type:** upstream-quality feature PR · **Owner:** (incoming CC agent)

> This spec is self-contained. The implementing agent does **not** need any prior
> conversation. Read it top to bottom, then implement on a clean feature branch.

---

## 0. Operating instructions for the implementing agent

**PR target is UPSTREAM (Max's repo), not the fork.**
- `origin` = the fork: `https://github.com/nytafar/obsidian-mcp`
- `upstream` = the target: `https://github.com/maxkuminov/obsidian-mcp` (per `README.md`)

Flow:
1. Add the upstream remote and fetch it:
   `git remote add upstream https://github.com/maxkuminov/obsidian-mcp.git && git fetch upstream`
2. **Branch off `upstream/main`** — NOT `origin/main` and NOT `experiments`.
   (`origin/main` may carry the fork's own unsynced commit; `experiments` carries
   unrelated planning docs. Only `upstream/main` is a clean base for an upstream
   PR.) Name it `feat/configurable-fts-language`.
3. Implement, commit (conventional commits — `feat(...)`/`fix(...)`/`docs(...)`).
4. Push the feature branch to **`origin`** (the fork): `git push -u origin feat/configurable-fts-language`.
5. Open a **cross-fork PR**: base = `maxkuminov/obsidian-mcp` `main`, head =
   `nytafar:feat/configurable-fts-language`. Suggested title:
   `feat(search): configurable full-text search language(s)`.
- **Do not push to or rewrite any `main`** (fork or upstream).
- **Tooling caveat:** if GitHub tooling is repo-scoped to `nytafar/*`, opening a PR
  whose *base repo* is `maxkuminov/obsidian-mcp` may be denied — in that case push
  the branch to the fork and open the cross-fork PR manually on GitHub (or have
  the maintainer-scope added first). Confirm the PR target with the user before
  opening.
- Keep the diff tight: this PR is **only** the FTS-config feature. Do **not**
  bundle the chunking fixes, hybrid fusion, or reranking (separate PRs).

---

## 1. Problem

Full-text (keyword) search hardcodes the PostgreSQL text-search configuration to
`'english'` in three aligned places:

- `src/services/indexer.py:227` — `SET content_tsvector = to_tsvector('english', :content)`
- `src/services/indexer.py:234` — same (second code path)
- `src/services/search.py:18` — `func.websearch_to_tsquery("english", query)`

The English Snowball stemmer mis-stems non-English text (e.g. Norwegian), and a
single configuration cannot correctly serve a **mixed-language vault**. Index-time
and query-time configs must agree, so all three sites must change together.

## 2. Goal & non-goals

**Goal:** make the FTS configuration a **list** of PostgreSQL text-search configs,
settable via the existing env/pydantic-settings mechanism, defaulting to
`["english"]` (exact current behavior). The list supports:
- `["simple"]` — language-agnostic, no stemming/stopwords (exact word forms).
- `["english"]` — current behavior.
- `["english","norwegian"]` — multi-language: both stemmers applied.
- `["simple","norwegian"]` — verbatim lexemes **plus** Norwegian stems.

**Non-goals (out of scope — do not implement here):**
- No web-UI editing and **no runtime settings store** — config file/env only.
- No hybrid fusion, reranking, chunking changes, or schema migration.
- **No per-user FTS config** — this setting is **global** (see §4.1).

## 3. How PostgreSQL FTS works (background)

`to_tsvector(config, text)` parses text into lexemes using a *text-search
configuration* (stemmer + stop-word dictionary). `'simple'` does neither (just
lowercases + tokenizes). The config used at index time and query time must match
or stems won't align. Crucially, **tsvectors and tsqueries are composable**:
- `tsvector || tsvector` → concatenation (merge lexeme sets; duplicates merge).
- `tsquery || tsquery` → logical **OR** (`&&` is AND).

So multi-language support = index each note under every configured config and
concatenate; query under every configured config and OR. A note matches if *any*
configured config's parse of the query hits.

## 4. Design

**New setting** (in `src/config.py`, `Settings`):
```python
fts_configs: list[str] = ["english"]
```
- Env var: `FTS_CONFIGS`. Accept JSON (`["simple","norwegian"]`) **and**
  comma-separated (`simple,norwegian`) for ergonomics — add a `field_validator`
  (mode="before") that splits a plain string on commas.
- Normalize: strip, lowercase, dedupe (preserve order), drop empties. Reject
  empty list (raise `ValueError` in a validator).
- Name it `fts_configs` (not `fts_languages`) because `simple` is a config, not a
  language.

**Index time** — `content_tsvector` becomes the concatenation over the list:
```sql
to_tsvector(CAST(:fts_cfg_0 AS regconfig), :content)
  || to_tsvector(CAST(:fts_cfg_1 AS regconfig), :content)
  || ...
```

**Query time** — OR the per-config tsqueries, rank against the same combined query:
```python
combined = reduce(lambda a, b: a.op("||")(b),
                  [func.websearch_to_tsquery(cfg, query) for cfg in settings.fts_configs])
rank = func.ts_rank_cd(NoteMetadata.content_tsvector, combined)
where(NoteMetadata.content_tsvector.op("@@")(combined))
```

**Backward compatibility:** default `["english"]` reproduces today's SQL exactly
(a single-element concat/OR is identical to the current single call). Existing
deployments are unaffected until they change the setting.

**No schema migration:** `content_tsvector` stays `TSVECTOR` regardless of config.
The only consequence of a config change is that stored tsvectors must be
**rebuilt** (data operation, see §7) — no Alembic migration needed.

### 4.1 Multi-user scope — GLOBAL

`FTS_CONFIGS` is a **single global setting**, applied to every user's vault. This
is consistent with the existing architecture: `embedding_model`,
`embedding_dimensions`, and `chunk_size` are all global env settings too — the
server runs one embedding/indexing configuration for all users. FTS config
follows the same model.

For a mixed-language multi-user instance, the **multi-config list is exactly the
accommodation**: set a superset (e.g. `["english","norwegian"]`, or `["simple"]`)
and every user's vault is served reasonably without per-user configuration.

**Why not per-user (and why it's deferred, not impossible):** per-user FTS *would*
be coherent — each `notes_metadata` row carries `user_id`/`owner`, so you could
index each note under its owner's config and query under the searcher's config,
and the existing per-user result scoping guarantees index/query configs align.
But it requires a **per-user settings store**, which has been explicitly deferred
(this project is config/env only for now). So: global for this PR; per-user is a
clean future extension once a settings store exists. Do not build it here.

## 5. Implementation by file

### 5.1 New module `src/services/fts.py` (single source of truth)
Centralize so indexer and search never drift:
```python
from functools import reduce
from sqlalchemy import func
from src.config import settings

def index_tsvector_sql(content_bind: str = "content") -> tuple[str, dict]:
    """SQL fragment + bind params to build content_tsvector from the configured
    FTS configs. Config names are passed as bound params + ::regconfig cast —
    never string-interpolated (injection-safe)."""
    cfgs = settings.fts_configs
    frag = " || ".join(
        f"to_tsvector(CAST(:fts_cfg_{i} AS regconfig), :{content_bind})"
        for i in range(len(cfgs))
    )
    params = {f"fts_cfg_{i}": cfg for i, cfg in enumerate(cfgs)}
    return frag, params

def combined_tsquery(query: str):
    """SQLAlchemy expression OR-ing websearch_to_tsquery over configs."""
    parts = [func.websearch_to_tsquery(cfg, query) for cfg in settings.fts_configs]
    return reduce(lambda a, b: a.op("||")(b), parts)

async def validate_fts_configs(session) -> None:
    """Fail fast if any configured config is not installed in this PG instance."""
    rows = await session.execute(text("SELECT cfgname FROM pg_ts_config"))
    available = {r[0] for r in rows}
    missing = [c for c in settings.fts_configs if c not in available]
    if missing:
        raise SystemExit(
            f"FTS_CONFIGS contains unknown text-search config(s): {missing}. "
            f"Available: {sorted(available)}"
        )
```

### 5.2 `src/services/indexer.py:227,234`
Replace both `to_tsvector('english', :content)` statements with the fragment from
`index_tsvector_sql()`, merging its params into the existing bind dict. Do not
change *what content* is passed — only the config(s).

### 5.3 `src/services/search.py:18-19`
Replace the single `websearch_to_tsquery("english", query)` with
`combined_tsquery(query)`; use it for both the `ts_rank_cd` rank and the `@@`
match. (Apply the same change to any other tsquery site if present — grep
`websearch_to_tsquery`.)

### 5.4 Startup validation `src/main.py` (lifespan)
Call `validate_fts_configs(session)` during startup, alongside the existing
embedding-dimension guard, so a typo'd config name fails fast with a clear
message rather than producing silent zero-result searches.

### 5.5 Rebuild operation (required after a config change)
Because `notes_metadata` stores no raw body column, `content_tsvector` must be
recomputed by re-reading each note's file. Add:
- `rebuild_tsvectors(session)` in `indexer.py` — iterate all indexed notes, read
  each file via the existing read path, `UPDATE ... SET content_tsvector =
  <index_tsvector_sql fragment>` with `:content`. Reuse the indexer's
  file-read + the §5.1 helper. Light operation (local IO + SQL; **no embeddings,
  no API calls**).
- Expose it as a command, mirroring the existing `reset-embeddings` style: a
  `make rebuild-tsvectors` target (and/or a small module entrypoint
  `python -m src.scripts.rebuild_tsvectors`). Config-only workflow: edit
  `FTS_CONFIGS` in `.env` → redeploy → run `make rebuild-tsvectors`.
- *(Optional, only if cheap)* store an `fts_configs` fingerprint and auto-trigger
  the rebuild on startup when it changes. Nice-to-have; keep out if it expands
  the diff much.

### 5.6 (Optional) read-only display
The admin settings page (`settings_page`, `routes.py:804`) renders env config
read-only. Optionally add a "Search language(s)" line showing
`settings.fts_configs`. Display only — no editing. Skip if it adds noise.

## 6. SQL / injection safety
Config names originate from env, not end users, but still must never be
string-interpolated. Index side uses **bound params + `::regconfig` cast**; query
side passes the config as a bound argument to `func.websearch_to_tsquery`. The
startup allowlist check (§5.1) is for clear errors, not the primary safety
mechanism.

## 7. Backward compatibility & the rebuild requirement
- Default `["english"]` → byte-identical behavior; no action for existing users.
- Changing `FTS_CONFIGS` makes stored tsvectors stale → run `make
  rebuild-tsvectors`. **This rebuilds the keyword index only — it does NOT touch
  embeddings/vectors and makes no API calls.** At a few thousand notes it
  completes in seconds. Document this prominently so users don't confuse it with
  the expensive `reset-embeddings` flow.

## 8. Edge cases
- Empty list → validation error (reject at config load).
- Duplicate configs → deduped by the validator.
- `simple` + a language → valid (verbatim lexemes + stems both stored).
- Stop-word asymmetry across configs in the OR'd query → harmless.
- Same lexeme produced by multiple configs → merges in the concat/OR (fine).
- **Parser tokenization caveat (document, don't fix):** the tsvector *parser*
  still splits on punctuation/hyphens regardless of config, so `bge-m3` →
  `bge` + `m3`. `'simple'` preserves word *forms*, not punctuation-bearing
  strings. Exact-string-with-punctuation matching is out of scope (would need a
  trigram index).

## 9. Tests
**Unit (`src/services/fts.py`):**
- `index_tsvector_sql` produces correct fragment + params for 1, 2, and 3 configs.
- `combined_tsquery` ORs N configs.
- config validator: parses JSON and CSV env forms; lowercases/dedupes; rejects empty.

**Integration (needs a PG with `norwegian` + `simple` installed — standard):**
- Index a Norwegian note containing `datasenteret`.
  - `FTS_CONFIGS=["norwegian"]` → query `datasenter` **matches** (stemmed).
  - `FTS_CONFIGS=["english"]` → query `datasenter` **misses** `datasenteret`.
  - `FTS_CONFIGS=["simple"]` → query `datasenteret` matches, `datasenter` misses (exact).
  - `FTS_CONFIGS=["english","norwegian"]` → matches both an English and a Norwegian term.
- `rebuild_tsvectors` updates all rows; a query that missed under the old config
  hits under the new one after rebuild.
- Default (unset) behaves exactly as `["english"]`.

## 10. Docs to update (in the PR)
- `README.md` — full-text search section: document `FTS_CONFIGS`, the `simple`
  vs language trade-off, multi-language, and the `make rebuild-tsvectors` step.
- `CLAUDE.md` — "Full-text search via PostgreSQL tsvector" line → note it's now
  configurable.
- `.env.example` — add `FTS_CONFIGS` with a comment and the default.
- `config.py` — docstring on the new field.

## 11. Acceptance criteria
- [ ] `FTS_CONFIGS` setting exists, defaults to `["english"]`, parses JSON + CSV, validated non-empty.
- [ ] All three hardcoded `'english'` sites route through `src/services/fts.py`.
- [ ] Startup fails fast on an unknown config name with a helpful message.
- [ ] `make rebuild-tsvectors` recomputes all tsvectors with no API calls.
- [ ] Default config reproduces current behavior (a regression test asserts this).
- [ ] Unit + integration tests pass; docs updated.
- [ ] Diff scoped to FTS only; branched off **`upstream/main`**; cross-fork PR opened with base `maxkuminov/obsidian-mcp:main`, head `nytafar:feat/configurable-fts-language`.
- [ ] Setting is global (no per-user config introduced).

## 12. Design rationale (for the PR description)
Keyword search is the *exact-match* arm (identifiers, proper nouns, phrases);
morphological/conceptual recall is the vector arm's job (bge-m3 is multilingual).
So `simple` is a principled default for mixed-language vaults, and stemmed
multi-language is available for users who want keyword-side morphology. A single
configurable list spans all cases with no schema change and full backward
compatibility.
