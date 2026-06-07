# Obsidian MCP — Architecture & Ergonomics Analysis

> Scope: the MCP layer (`src/mcp_server/`, `src/services/`, `src/config.py`) of
> this server, analyzed for tool ergonomics, frontmatter awareness, parameters,
> and context-serving mechanisms (prompts / resources / sampling). Goal: chart
> the path from a working retrieval/CRUD MVP to a **structured ops + memory
> layer**. Findings are tagged **[severity · effort]**. Citations are
> `path:line`. Retrieval *ranking* design is covered in the companion retrieval
> brief and has been revised per external review — see "Search" note at the end.

## Verdict

A well-built **flat verb API**: clean safety primitives, genuinely good
LLM-facing tool docs, careful multi-user scoping. But architecturally it is a
*retrieval/CRUD* server, not yet an *ops/memory layer*. The largest gains are not
in the existing tools — they are in **three MCP mechanisms it does not use at all
(prompts, resources, structured outputs)** and in **moving the vault's schema and
guardrails from prose into executable server logic.**

## Good bones (keep, build on)

- **`apply_note_filters` as single filter source of truth** (`services/filters.py`) — extend, never fork.
- **`_tracked` + `correlation_id`** (`mcp_server/tools.py:55`) — a real telemetry spine; underexploited (see E6).
- **Safety layer:** `validate_path` traversal guard (`services/vault.py:94`), atomic tmp+rename writes, git snapshot before destructive ops, soft-delete to `.trash/`.
- **Tool descriptions** — the "use this vs that" guidance (`keyword_search` vs `semantic_search`; `get_neighborhood` vs `find_related`) is exactly right for LLM tool selection.
- **Per-user scoping** threaded carefully, with defense-in-depth in the BFS.

---

## A. Missing MCP mechanisms (the real opportunity)

- **A1. No MCP `prompts`. [high · med]** Zero `@mcp.prompt`. The `second-brain-dev`
  effort already specs a `vault-work-context` prompt (drafted, unbuilt). Prompts
  are the client-surfaced home for zone→write-permission rules, the `[!agent]`
  callout convention, client-data isolation, and "how to file work." Core
  workflows (ingest / briefing / query→file / vault-process) are prompt-shaped
  and today live only as client-side `.claude/skills/`, invisible to web/mobile.
- **A2. No MCP `resources`. [high · med]** `get_vault_guide` is a *tool*
  (pull-based; many clients never call it). `CLAUDE.md`, the property schema,
  `wiki/index.md`, `wiki/taxonomy.md`, `daily/log/` are textbook resources
  (addressable, cacheable, auto-attachable). A resource template `note://{path}`
  would let clients attach notes as first-class context instead of `read_note`
  round-trips.
- **A3. `FastMCP(...)` has no `instructions=`. [med · trivial]** `server.py:25`
  sets a name only. The server-level `instructions` string is the cheapest way to
  push non-obvious rules (zones, client isolation) without relying on the agent
  calling `get_vault_guide`. Lowest-hanging fruit in the repo.
- **A4. No `sampling`. [med · high]** The server can't ask the client LLM to work.
  For an ops layer this unlocks server-driven auto-classification (infer
  `kind`/`tags` on ingest), auto-linking, and dedup — without the server holding
  its own LLM key.

## B. Tool ergonomics

- **B1. Everything returns hand-formatted markdown strings. [high · high]** No
  structured content; tools don't compose (an agent must regex paths out of a
  bullet list to chain `semantic_search` → `read_note`). Return structured data
  (with a markdown rendering alongside). Foundation for everything else.
- **B2. Success and failure are indistinguishable. [high · low]** Errors are
  returned as ordinary success strings (`"Note not found: …"`); `_require_write()`
  returns a *string* (`tools.py:286`) rather than raising. No `isError`. An agent
  cannot reliably tell a permission denial from an answer. Correctness bug.
- **B3. Telemetry name mismatch. [med · trivial]** Tool `keyword_search`
  (`server.py:36`) is `@_tracked("search_notes")` (`tools.py:90`). `usage_logs`
  records the wrong tool name — future analytics silently wrong.
- **B4. Double, inconsistent truncation. [low · trivial]** `semantic_search`
  carries `chunk[:500]` (`embeddings.py:271`) then re-truncates to `[:200]`
  (`tools.py:264`). Pick one; make it a param.
- **B5. Unbounded `limit` on hot tools. [med · trivial]** `keyword_search` /
  `semantic_search` / `list_notes` / `get_recent` do not clamp (others do). Clamp
  uniformly.
- **B6. `read_note` has no size cap or pagination. [med · med]** `create_note`
  enforces `MAX_NOTE_BYTES`; `read_note` will return a 1.6 MB OCR doc inline. Add
  offset/section/byte window.
- **B7. `read_note` stringifies frontmatter lossily. [med · low]** `tools.py:134`
  renders `f"  {k}: {v}"`, flattening lists/nested (e.g. lineage arrays,
  `sources: [...]`). Agent can't reliably read structured metadata it may rewrite.
- **B8. Tool-count / cognitive load. [design axis]** 17 flat tools with
  overlapping intent. Candidates to unify: `keyword_search` + `semantic_search` →
  one `search`/`query`; `get_links`/`get_backlinks`/`get_neighborhood` → one
  `graph` with direction+depth. Fewer richer tools vs more params — decide
  deliberately.

## C. Frontmatter awareness (load-bearing for "structured ops")

- **C1. Filtering is containment-only. [high · med]** `frontmatter @> :json`
  (`filters.py:43`) supports exact key=value only — no ranges (`date > X`), no
  `IN`, no "key exists", no negation. Cannot express "active efforts modified in
  the last 30 days." The most limiting data-plane gap.
- **C2. The schema is invisible to discovery. [high · med]** `get_tags` exists but
  there is no `get_kinds` / `get_states` / facet tool. The whole `kind/owner/state`
  spine is unqueryable through MCP.
- **C3. No schema enforcement on write. [high · high — the pivot]**
  `create_note`/`set_frontmatter` accept anything. The property schema lives only
  in human docs + client-side Templater; the MCP bypasses it. The observed drift
  (`state` vs `status`, `oppdatert` vs `last_updated`, lingering `zone:`, the
  foreign `memory/` `type:`/`permalink:` schema) is exactly what an unenforced
  write path produces.
- **C4. No frontmatter-aware ranking.** Same root cause as C1 — metadata captured,
  never used beyond exact-match filtering. (Ranking detail in the retrieval brief.)

## D. Tool variables / parameters

- **No projection/`fields` param [med · low]** — every read pulls everything.
- **No date-range params anywhere [high · med]** — `modified_at` is core yet
  nothing accepts `since`/`until`. Time is the most natural ops filter.
- **No `sort` param [low · low]** — `list_notes`/`get_recent` hardcode
  `modified_at DESC`.
- **No pagination/cursor [med · med]** — `limit` only, no `offset`.
- **`folder` default inconsistency [trivial]** — `list_notes` uses `""`, others `None`.

## E. Ops / memory-layer opportunities (the restructuring)

- **E1. Server-side schema engine. [keystone]** Make the property schema
  executable (kind registry: required fields, allowed `state` values, folder→kind
  mapping, status enums). Then `scaffold_note(kind=…)` (stamps correct
  frontmatter, routes to the right folder), write-time validation, and a
  `lint_vault` tool that surfaces drift. Fixes C3 and anchors every other ops
  feature.
- **E2. First-class daily-log / audit append. [high · low]** Convention is "append
  audit to `daily/log/`" but there is no `log_activity`/`append_daily` primitive —
  the memory write-path for "what happened" is missing.
- **E3. Task primitives. [high · med]** Vault runs on `- [ ]` tasks + a TickTick
  mirror in `daily/tasks/`. No `list_tasks`/`add_task`/`complete_task`.
- **E4. Workflow/state-machine surface. [high · med]** `state` transitions and
  `[!agent]` callouts are how work flows, but nothing exposes "open `[!agent]`
  callouts" or "notes in `review` awaiting me." Promote the `vault-process` grep
  logic to a tool.
- **E5. Link-suggestion on write. [high · med]** `create_note` doesn't help connect
  the note. `suggest_links(path|content)` (reuse `find_related` + graph) fights
  orphan-creation at the source — highest-value feature for graph health.
- **E6. Surface the telemetry already collected. [med · low]** `usage_logs` has
  correlation-ids, durations, params, but no MCP tool reads it back. "What did
  I/the agent work on today" is a free memory primitive. (Also: source of real
  queries for the eval golden set — see Search note.)
- **E7. Index freshness / write-through. [high · med]** Tools read from the index,
  so a just-written note is invisible for ≤5 min (indexer interval). The "write →
  immediately query" loop silently breaks. Add `reindex(path)` / write-through /
  freshness indicator.
- **E8. Client-data firewall in code, not prose. [highest severity · med]**
  `registry/clients/**` and `inputs/clients/**` are sensitive health data,
  isolated only by `CLAUDE.md` prose. `validate_path` blocks traversal but not
  client-path access (`vault.py:94`); `semantic_search` will return client chunks.
  Enforce path/embedding exclusion server-side, gated by explicit authorization.
  Fix first.
- **E9. Multi-note / transactional ops. [med · high]** Every tool is single-note;
  compound workflows (move + rewrite + reindex + log) aren't atomic. `move_note`
  already exposes the seams (FS move succeeds, DB update can fail with a warning,
  `tools.py:929`).

## Prioritized roadmap

**Quick wins (hours):** `FastMCP(instructions=…)` (A3) · fix
`search_notes`/`keyword_search` telemetry name (B3) · clamp all `limit`s (B5) ·
kill double-truncation (B4) · structured frontmatter in `read_note` (B7).

**Foundations (days):** structured tool outputs (B1) + real error signaling (B2),
together · frontmatter range/exists/IN filters + `since`/`until` (C1, D) · facet
tool `get_kinds`/`get_states` (C2) · expose `usage_logs` (E6) · index freshness
signal (E7).

**The pivot (weeks):** MCP **prompts** for core workflows + **resources** for
schema/taxonomy/indexes (A1, A2) · **server-side schema engine** with
`scaffold_note` + `lint_vault` (E1, C3) · unified **`query`** tool (hybrid +
filters + rerank + facets, structured results) that subsumes keyword/semantic
(folds in the retrieval upgrades; cuts tool count).

**Safety (early despite effort):** the **client-data firewall** (E8).

---

## Search (retrieval ranking) — see companion brief, revised per external review

The ranking design lives in the retrieval brief. Net of an external
state-of-the-art review, the headline shifts are:

- **Build the eval harness first** (40–50 golden queries; `usage_logs` (E6) can
  seed real ones); gate everything on it.
- **Ship vanilla RRF first** (k≈30–60 as config); only move to weighted/convex
  fusion with ≥40 labeled queries.
- **Norwegian "weakest-link":** down-weight/gate the English-config tsvector arm;
  evaluate a **bge-m3 learned-sparse** lexical arm (infra caveat: Ollama's
  `/api/embed` is dense-only — sparse needs a different serving path).
- **Demote graph PageRank** to a measured diffusion experiment; use weighted
  **backlink count** as the baseline. Typed-lineage PPR = research bet.
- **Keep the metadata-feature reranker** (maturity/recency/kind) — not subsumed by
  neural rerankers — with an **optional cross-encoder** (`bge-reranker-v2-m3`,
  multilingual) behind an eval-gated flag.
- **Strategic reframe:** the consumer is an LLM agent → invest in **recall + clean
  composable tools** (E-series here) over single-shot ranking sophistication. The
  ops toolset is the higher-leverage bet; this analysis and the retrieval review
  converge on the same conclusion.
