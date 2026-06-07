# Architecture — North Star

> The governing model the other docs (retrieval brief, MCP analysis, roadmap,
> semantic-search state) all serve. One sentence:
>
> **The canonical shape is folders of markdown with frontmatter. Obsidian is the
> human viewer/editor. The MCP/CLI is the query + ops plane. They meet only at
> the files.**

## The three planes

| Plane | Role | Is the system of record? |
|---|---|---|
| **Files** — MD + frontmatter on disk (git-backed) | Canonical truth | **Yes** |
| **Obsidian** — desktop app, Bases, Dataview, graph, canvas, LiveSync | Human view/edit UI | No — a renderer |
| **MCP / CLI** — Postgres index, embeddings, link graph, schema engine | Query + ops API (agents, cron, CI) | No — a derived index |

The files are the contract. Both the human plane and the query plane read and
write the same files; neither owns state the other can't see.

## Constitution (load-bearing rules)

1. **Files are truth; the index is derived and disposable.** Postgres must be
   rebuildable from the vault at any time. No canonical state lives only in the
   DB. Every agent mutation round-trips to disk.
2. **Frontmatter is a typed contract, not decoration.** Because MCP/CLI query and
   route by `kind`/`state`/`owner`/dates, frontmatter consistency is load-bearing.
   Drift (`state` vs `status`, `oppdatert` vs `last_updated`, foreign `memory/`
   `type:`/`permalink:`) is a **bug class**, not a style nit.
3. **Obsidian features are views, never the record.** Bases/Dataview/graph are
   renderings of the same properties the index already holds. The server never
   depends on them and never treats plugin artifacts as semantic truth (normalize
   on ingest).
4. **Every destructive write is git-snapshotted and atomic.** The reconciliation
   story (LiveSync ↔ agent writes ↔ git) is first-class, not incidental.
5. **The query plane is the durable interface; the viewer is swappable.** Because
   the shape is just MD+frontmatter folders, the system is portable — VS Code,
   Logseq, a web UI, plain git could replace Obsidian. The MCP/CLI contract
   outlives any one editor.

## What this buys

- **Portability / no lock-in** — Obsidian is a convenience, not a dependency.
- **The frontmatter schema doubles as the agent's affordance map** — reliable
  `kind`/`state`/`owner` let the agent navigate work ("active efforts," "canon
  wiki," "drafts in review"). The schema is both query contract *and* workflow
  state machine.
- **Human and agent share one substrate** — what you edit in Obsidian, the agent
  sees; what the agent writes, you read in Obsidian. No separate agent memory.

## What it costs (the honest tension)

- **Two writers, one truth.** Human (via Obsidian + LiveSync across devices) and
  agent (via MCP/CLI) both mutate files. Requires: prompt index freshness /
  write-through (so the agent sees human edits), and atomic+snapshotted writes (so
  neither clobbers the other).
- **The human can break the contract.** Editing in Obsidian, a person can type the
  wrong frontmatter key and silently break a query. So the **schema engine needs
  two directions**: *validate* agent writes, and *lint/heal* drift introduced by
  the human side. `lint_vault` is the immune system, not a nice-to-have.

## How it sharpens priorities (see roadmap)

- **Schema engine + write validation (3.1) is THE keystone** — it guards the
  contract that makes the whole query plane trustworthy. Promotes from "pivot" to
  "the point."
- **Index freshness / write-through (2.4)** rises — two writers make staleness a
  correctness issue, not a convenience.
- **Structured outputs (2.1) + richer frontmatter filters (2.2)** are the *API
  surface* of the contract; the `.base`→SQL translator (2.6) reuses the human's
  own query intent against the index.
- **Ranking sophistication stays secondary** — the agent reranks by reading; the
  contract and the toolset are the leverage.
- **Client-data firewall (0.1)** is enforced at the file/query boundary,
  consistent with "files are truth."
