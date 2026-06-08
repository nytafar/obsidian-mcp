# Obsidian MCP — Roadmap & Ranked Backlog

> One prioritized backlog across retrieval, ops/memory, and interface (CLI),
> merging the MCP architecture analysis, the retrieval brief, and the external
> state-of-the-art review. Ordered by **leverage = impact × strategic compounding
> ÷ effort**. The eval harness is the gate for all expensive ranking work.

## Guiding principles

1. **The consumer is an LLM agent.** It reranks by reading and re-querying.
   Invest in recall + clean, composable, well-described tools over single-shot
   ranking sophistication.
2. **Structure compounds; ranking polish doesn't.** Canonical frontmatter fields
   and a schema engine make filtering, ops, *and* future ranking cheap. Improving
   the already-good vector ranker is bounded by the agentic ceiling.
3. **Gate expensive search behind measurement.** No weighted fusion, cross-encoder,
   sparse arm, or graph signal ships without the eval harness showing headroom.
4. **One service layer, many adapters.** Keep tool logic in transport-agnostic
   `_impl` functions; MCP, CLI, and cron are thin wrappers.

## Tier 0 — Do now (cheap wins + safety)

| # | Item | Why | Effort |
|---|------|-----|--------|
| 0.1 | **Client-data firewall** (E8) — enforce `registry/clients/**`,`inputs/clients/**` exclusion in code, gated by explicit auth | Safety-critical; prose-only today | med |
| 0.2 | **Hybrid RRF `search` tool** — fuse existing semantic+keyword, vanilla RRF, k as config | Removes "which search?"; unifies tools; ranking foundation | low |
| 0.3 | **Norwegian keyword fix** — configurable FTS configs (`['english']` default; `simple`/multi-language); see `specs/configurable-fts-language.md` | Recovers NO keyword recall | trivial |
| 0.4 | `FastMCP(instructions=…)` (A3); fix `search_notes`/`keyword_search` telemetry name (B3); clamp `limit`s (B5); kill double-truncation (B4) | Pure hygiene, hours | trivial |

## Tier 1 — Eval gate (build before any expensive ranking)

| # | Item | Why | Effort |
|---|------|-----|--------|
| 1.1 | **Eval harness** — golden set seeded from `usage_logs` (E6) real queries + synthetic; nDCG@10/MRR/Recall@k; frozen ablation rig; optional eRAG scoring | Gate for all of Tier 4 | med |

*Gate: <30 trustworthy judged queries → ship vanilla RRF, stop tuning, skip Tier 4.*

## Tier 2 — Foundations (composability + data plane)

| # | Item | Why | Effort |
|---|------|-----|--------|
| 2.1 | **Structured tool outputs** (B1) + **real error signaling** (B2) | Composability + correctness; everything rides on it | high |
| 2.2 | **Richer frontmatter filters** (C1) — ranges, `IN`, exists, negation, `since`/`until` | The Bases-equivalent query layer | med |
| 2.3 | **Facet tools** `get_kinds`/`get_states` (C2) | Schema becomes discoverable | low |
| 2.4 | **Index freshness / write-through** (E7) | Fixes write→query loop | med |
| 2.5 | Expose `usage_logs` as a tool/resource (E6) | Memory primitive + eval source | low |
| 2.6 | **`.base`-filter → SQL translator** — reuse existing `.base` defs as server-side saved queries (see `bases-and-query-layer.md`) | Bases-grade queries, headless, no Electron | low |

## Tier 3 — The pivot (ops / memory layer + interface)

| # | Item | Why | Effort |
|---|------|-----|--------|
| 3.1 | **Server-side schema engine** (E1, C3; see `vault-schema-spec.md`) — kind registry, `scaffold_note`, write validation, `lint_vault` | Turns MVP into a structured ops layer; fixes drift | med-high |
| 3.2 | **MCP prompts** (A1) — ingest / briefing / query→file / vault-process | Client-surfaced workflows + guardrails | med |
| 3.3 | **MCP resources** (A2) — schema, taxonomy, indexes, `note://{path}` template | Cacheable, auto-attachable context | med |
| 3.4 | **Composable agent toolset** — `read_section`/`get_chunk` (granularity; see `section-as-atomic-memory.md`), `follow-links`, frontmatter-filtered list, optional server-side `grep`, `suggest_links` (E5), daily-append (E2), task primitives (E3), `get_provenance` (provenance graph) | Where agentic retrieval lives | med |
| 3.5 | **Headless CLI** — thin Click adapter over `_impl`, scoped to ops primitives (`search`, `scaffold`, `lint`, `reindex`, `append-daily`, `eval`) | Cron/git-hooks/eval/dev; the headless interface to the index | low-med |

## Tier 4 — Deferred behind the eval gate (expensive ranking)

| # | Item | Condition to fund |
|---|------|-------------------|
| 4.1 | Metadata-feature reranker (maturity/recency/kind/provenance; heuristic→LambdaMART) | eval shows per-signal nDCG lift |
| 4.2 | Weighted/convex fusion (replace vanilla RRF) | ≥40 labeled queries + ≥2–3 nDCG headroom |
| 4.3 | Cross-encoder rerank (`bge-reranker-v2-m3`, top-20–50, flag) | beats metadata reranker on golden set |
| 4.4 | bge-m3 learned-sparse arm / late chunking | needs new serving path (not Ollama dense); eval-justified |
| 4.5 | Contextual retrieval (per-chunk LLM context) | boundary-context loss measured as bottleneck |
| 4.6 | Section-aware chunking + heading-path context-prefix (see `section-as-atomic-memory.md`) | re-embed; eval A/B |

## Tier 5 — Experiments (research bets, measured)

| # | Item | Note |
|---|------|------|
| 5.1 | Weighted **backlink count** baseline | replaces PageRank-as-authority |
| 5.2 | Query-seeded **personalized PageRank** as diffusion signal | must beat 5.1 on nDCG to keep |
| 5.3 | Typed-lineage edge weights (`builds_on`/`refines`/`opposes`) in PPR | unproven; label as research |

## Cross-cutting tracks (span tiers; see dedicated briefs)

- **Section as atomic memory unit** — Seams 1–4 + `read_section`/`get_chunk` +
  write symmetry. Foundation for granular read/write. (`section-as-atomic-memory.md` — vault effort)
- **Provenance & temporal awareness** — node classes + derivation edges +
  two-dimensional staleness (git-section × evidence-recency).
  (`vault-schema-spec.md` provenance section + `temporal-awareness.md` — vault effort)

## Critical path

`0.2/0.3 (cheap search) → 1.1 (eval) → 2.1/2.2 (structured + filters) → 3.1
(schema engine) → 3.4/3.5 (toolset + CLI)`. Tier 4 hangs off 1.1. Tier 0.1
(safety) runs independently and first.
