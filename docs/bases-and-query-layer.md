# Bases, Obsidian-CLI, and the query layer

> Why the query plane is Postgres-over-JSONB, not a headless Obsidian, and how to
> reuse the user's existing `.base` definitions. Resolves the `.base`→SQL
> translator reference (roadmap 2.6) and the architecture doc's mention of it.

## The question

"Can we run Obsidian CLI / Bases headless and have the MCP query it for robust
frontmatter/DB-like queries, almost for free?"

## What Bases / "Obsidian CLI" actually are

- **Bases** is a **core GUI plugin** (Obsidian 1.9, 2025): `.base` YAML files
  defining filters + formulas + views over note properties + file metadata. It
  evaluates **inside the running Electron app** against the in-memory metadata
  cache and renders views in the UI. There is **no headless "run this `.base`,
  return rows as JSON" interface.** Bases is a view layer, not a query engine.
- **"Obsidian CLI"** — no official one. Community tools drive the *running app*
  via the `obsidian://` URI scheme or poke files on disk; none expose Bases/
  Dataview query *results* headlessly. Truly headless = Electron under `xvfb` —
  fragile, GUI-bound, no clean query stdout.

## Verdict: no — and you don't need to

Routing a clean FastAPI+Postgres server through a headless Electron app is the
**wrong dependency direction**, for capabilities you already have in a more
robust form.

**Your Postgres index is already a superset of Bases.** Bases operates on
(a) frontmatter, (b) tags, (c) the link graph, (d) file metadata — your indexer
already extracts all four into `frontmatter JSONB`, `tags ARRAY`, `note_links`,
`modified_at`. SQL over JSONB is *strictly more powerful* than Bases filters:
ranges, `IN`/`OR`/`NOT`, joins, aggregates, full-text, sorting. So the
"Bases engine" equivalent is just **richer SQL operators on data you already
store** (roadmap 2.2) — no Electron.

## The almost-free move: `.base` filter → SQL (roadmap 2.6)

Parse a `.base` file's **filter spec** and translate it to SQL against the JSONB
column. The user's existing 8 `.base` definitions become **server-side saved
queries**, executable by MCP and CLI, with no Obsidian runtime.

**Scope split (the honest caveat):** Bases **formulas** are a small
Obsidian-specific expression language (`file.links.filter(value.asFile().inFolder(...))`,
`note.title`). Filters translate to SQL cleanly; arbitrary formulas don't.
- **Filters** (property comparisons, folder, tags, dates) → translate to SQL. Easy, high value.
- **Common formulas** (backlink count, "links into folder X") → reimplement in SQL via `note_links`.
- **Arbitrary formulas** → out of scope; the GUI keeps those.

## How this lands

- Confirms **roadmap 2.2** (richer frontmatter filters) is the real "Bases-like
  queries" deliverable — and raises its value (subsumes existing `.base` files).
- Adds **roadmap 2.6**: a `.base`-filter→SQL translator (low effort, high reuse).
- Confirms the **CLI direction** (roadmap 3.5): the CLI is the *headless interface
  to your index*, e.g. `obsidian query studies.base --json` runs the translator
  against Postgres — the robust, headless, almost-free DB-like querying you want,
  with zero Electron.

**Principle:** don't make the MCP a client of Obsidian; make it the query engine
Obsidian's data deserves. Obsidian stays the human viewer/editor (see
`architecture.md`).
