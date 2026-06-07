# Retrieval Brief — design + external review

> The retrieval/ranking design for the server, plus its revision after an external
> state-of-the-art review. This is the doc the others reference as "the retrieval
> brief" / "companion brief." Pairs with `semantic-search-current-state.md`
> (how it works today) and `roadmap.md` (sequencing). Citations `path:line`.

---

# Part A — Design

## The two systems are mirror images (memweave ↔ obsidian-mcp)

memweave (a sibling SQLite RAG library, studied as a design reference) and this
server have opposite strengths:

| | memweave | obsidian-mcp |
|---|---|---|
| Vector + keyword | **fused** (0.7/0.3 weighted) | **two separate tools**, never merged |
| Rerank pipeline | yes (threshold→decay→MMR→custom) | **none** |
| Recency/decay | optional (filename-derived age) | none in ranking (but `modified_at` exists) |
| Structured metadata | none (frontmatter = opaque text) | **frontmatter JSONB + tags + folder**, queryable |
| Link graph | none | **`note_links`**, resolved + indexed |
| Real timestamp | only mtime | `modified_at` column |
| Migrations | none | **alembic** |
| Embedding hygiene | embeds raw incl. frontmatter | strips fences + frontmatter (better) |

**Conclusion:** memweave has the *ranking ideas*; obsidian-mcp has the *data and
plumbing*. Bring memweave's ranking lessons here, where the structured data and
migrations already live — don't rebuild memory semantics in memweave.

## Proposed direction

1. **Hybrid fusion via RRF** — fuse the existing semantic + tsvector candidate
   pools into one ranked list (`score = Σ 1/(k+rank_i)`), scale-free, no training.
   Removes the agent's "which search?" choice and collapses two tools into one.
2. **A rerank layer using the vault's structure** (what memweave never had):
   recency (frontmatter date → `modified_at`), maturity (`state: canon` boost),
   `kind` prior, backlink authority. Cheap, metadata-driven.
3. **Index-time canonical fields** feed both filtering and the reranker.

## Frontmatter signal catalogue (sampled from the live vault)

Real frontmatter, with the **emergent patterns / drift** any ranking or schema
layer must survive:

- **Universal:** `kind, owner(user|agent|ingest|shared), state(draft|active|review|canon|stale|merge), date, title`.
- **Recency naming drift:** update-date appears as `oppdatert` (NO, efforts),
  `last_updated` (wiki), or absent. `date` is creation/import. → needs a
  precedence COALESCE, or index-time normalization.
- **Maturity drift:** schema says `state`; the datasenter effort uses `status`.
  Plus kind-specific `*_status` (sourcing/commercial/relationship) — a *different*
  axis (lifecycle, not document maturity).
- **Lingering `zone:`** in some notes despite "derive zone from path" decision.
- **Two memory conventions:** `memory/` notes use foreign `type:`/`permalink:`
  (basic-memory-style), not `kind/owner`.
- **`mythos/` subsystem** carries lineage edges in frontmatter
  (`builds_on/refines/opposes/compressed_by`) + `register_alignment` — a richer
  authority signal than backlinks for that subgraph (possible ranking input).
- **Kind/size skew:** hundreds of tiny `sporsmal/q-*.md` stubs (~700 B) and huge
  OCR'd `kilder/**` docs (up to ~1.6 MB) coexist — both distort ranking.
- **`tags` sparse/uneven**; **language mixing** (`lang: norsk`).

Candidate ranking signals: `state` (canon boost / stale demote), `kind` prior,
recency (normalized), weighted backlink count, folder/zone (curated vs raw dump),
file-size soft cap.

## Chunking — current, defects, upgrades

**Current** (`services/embeddings.py:38`, `config.py:15-18`): pure character-window
split, `char_size = chunk_size(512) × 4 = 2048` chars, **overlap 0** (deliberate,
citing 2025 benchmarks), structure-blind. **Hygiene is good** — code fences
stripped (`embeddings.py:26`) and frontmatter excluded (body-only embed).

**Weakness (documented elsewhere):** windows can split a fact / list / table; no
heading or section anchoring.

**Latent code defects (NOT previously documented):**
1. **No `overlap < chunk_size` guard.** `start = end - char_overlap`
   (`embeddings.py:57`); if `chunk_overlap ≥ chunk_size`, `start` never advances →
   **infinite loop**. `config.py` has no validator (memweave's `ChunkingConfig`
   raises on this; this server doesn't). Footgun the moment overlap is enabled.
2. **"512-token" is really 2048 chars** via the 4-chars≈1-token approximation.
   For token-dense content (CJK, code, some Norwegian compounds) a window can
   exceed bge-m3's real token limit, and there is **no recursive-halving fallback**
   (memweave has one) — the over-long chunk simply fails to embed.

**Upgrade path:** small overlap A/B (10–20%); **late chunking** (natural fit for
cross-referential notes; needs a token-level serving path — Ollama `/api/embed`
is pooled/dense-only); contextual retrieval (highest ceiling, per-chunk LLM call,
defer until measured need).

## Norwegian unlock

- **Cheap fix (hours):** the keyword arm uses `websearch_to_tsquery('english', …)`
  (`search.py:18`) — English stemming on Norwegian content. Switch to the
  `'simple'` (unstemmed, language-agnostic) config, or store per-note language and
  pick the config. Recovers most NO keyword recall with zero new infra.
- **Stronger (infra bet):** a **bge-m3 learned-sparse** lexical arm sidesteps the
  language-config mismatch entirely — but Ollama's `/api/embed` is dense-only;
  sparse needs a different serving path (FlagEmbedding / TEI / Infinity).
- **Fusion safety:** the "weakest-link" effect (Part B) means a weak English arm
  can *drag the hybrid below* the vector arm — so gate/down-weight the keyword arm
  on detected-Norwegian queries.

---

# Part B — Revised per external state-of-the-art review (2024–2026)

## What changed

- **Build the eval harness FIRST.** Everything (weighting, cross-encoder, graph
  signals) is unanswerable without it. Seed the golden set from `usage_logs` real
  queries (the telemetry spine); add synthetic (RAGAS/ARES) for coverage; metrics
  nDCG@10 / MRR / Recall@k; consider eRAG (score by downstream LLM answer) since
  the consumer is an agent. *Gate: <30 trustworthy judged queries → ship vanilla
  RRF and stop tuning.*
- **Ship vanilla RRF first** (k≈30–60, config). Calibrated convex/weighted fusion
  (Bruch et al., TOIS 2023) can beat RRF but needs ~40 labeled queries (Elastic).
- **Demote graph PageRank.** On a ~2,577-node single-author wikilink graph,
  PageRank collapses toward in-degree with an old-node bias (Mariani/Medo/Zhang,
  *Sci. Rep.* 2015) and the "you wrote all the links" caveat kills the endorsement
  semantics. Use **weighted backlink count** as baseline; treat query-seeded
  Personalized PageRank as a *measured diffusion* experiment; typed-lineage PPR =
  research bet, not deliverable. (Note: HippoRAG's PPR gains run over LLM-extracted
  *entity* graphs, not document link graphs — they don't transfer.)
- **Keep the metadata-feature reranker; add an optional cross-encoder on top.**
  Neural rerankers score *semantic* relevance; they do **not** subsume recency /
  maturity / kind / authority (Vespa multi-phase ranking, LambdaMART results).
  Optional `bge-reranker-v2-m3` (multilingual, ~0.14s) on top-20–50, eval-gated.
  **Skip listwise LLM rerankers** (RankZephyr/RankLLM) server-side — redundant when
  the consumer is itself an LLM, plus OOD variance.
- **Chunking:** 512/overlap-0 is competitive; small overlap and **late chunking**
  are the cheap upgrades; contextual retrieval (Anthropic: −35% to −67% failure
  rate) is the higher-ceiling, higher-cost option — defer.
- **bge-m3 learned-sparse for Norwegian** — strongest new idea; infra-gated
  (serving path), not "free."

## The strategic reframe (most important)

The consumer is an **LLM agent** that reranks by reading and re-querying (the
field is shifting to agentic retrieval — Claude Code dropped RAG for agentic
search; Karpathy's LLM-wiki pattern is what an agent-served vault *is*). So:

- Make `hybrid_search` excellent at **recall + clean, well-described, composable
  results** (the agent reranks by reading).
- Expose **cheap composable tools** (get-note, follow-links/backlinks,
  frontmatter-filtered list, optional grep) so the agent navigates like Claude
  Code navigates a codebase.
- **Don't over-invest in single-shot ranking** whose output the agent re-evaluates
  anyway. Index-time canonical fields get *more* valuable (cheap filtering); heavy
  score-fusion/graph reranking gets *less*.

This converges with the MCP architecture analysis: the **schema engine + composable
ops toolset** is the higher-leverage bet; ranking sophistication is secondary.

## Cost / leverage summary

| Item | Verdict |
|---|---|
| RRF fusion | ~free, worth it |
| Index-time canonical fields | cheap, worth it (more so in agentic setting) |
| Metadata reranker | negligible latency, worth it |
| Cross-encoder (top-20) | +0.1–0.5s, worth it behind a flag |
| Norwegian tsvector `simple` fix | trivial, do now |
| bge-m3 sparse / late chunking | needs new serving path, eval-justified |
| Contextual retrieval | high ceiling, defer until measured |
| Graph PageRank (authority) | not worth it; diffusion experiment only |
| Listwise LLM rerank | skip (redundant with the consuming agent) |

## Caveats

- Vendor benchmarks are directional; trust the arXiv anchors + your own golden set.
- Agentic-search-beats-RAG evidence is partly anecdotal and strongest for *code* +
  *frontier models*; for a multilingual note vault, good hybrid retrieval still
  matters — don't over-rotate to grep-only.
- Typed-lineage PPR is genuinely unproven — pursue with measurement, not as a
  committed feature.
