# Vault Schema — single declarative contract

> One machine-readable file that encodes the vault's structure, consumed by
> **both** the server (validation, scaffolding, routing, facets, firewall,
> ranking hints) **and** agents (ground truth instead of prose). It turns the
> property schema from advisory `CLAUDE.md`/`properties.md` prose into a
> *checked* contract. Serves roadmap item 3.1 and the architecture north star
> ("frontmatter is a typed contract").

## Why one file, two consumers

- **Today** the schema lives as prose tables in `.claude/docs/properties.md` +
  conventions in `CLAUDE.md`. Agents *interpret* it; nothing *enforces* it. Result:
  observed drift (`state` vs `status`, `oppdatert` vs `last_updated`, foreign
  `memory/` schema).
- **The move:** make the schema a declarative file that is *the* source of truth.
  - **Server** loads it → rejects/normalizes invalid writes, scaffolds correct
    notes, routes by kind, powers facet queries, drives the client-data firewall,
    and supplies ranking hints (recency precedence, evergreen rules).
  - **Agents** read the same file (via an MCP resource + `get_vault_guide`) as
    ground truth for how to create/route/tag — no prose interpretation.
- **Enforcement = both directions:** the server *checks* (hard), the agent *reads*
  (guidance). Prose was advisory; this is verified.
- **Consistent with the north star:** the schema is itself a file (truth),
  human-authored, edited in Obsidian, git-tracked. Both planes respect it the
  instant it changes.

## Where it lives & format

- **Location:** `_system/vault-schema.yaml` (the vault's user-owned "conventions"
  zone). Server reads it from the vault root path like it already reads
  `CLAUDE.md`. Falls back to permissive mode if absent.
- **Format:** YAML (matches frontmatter, human-editable in Obsidian). A small,
  vault-shaped DSL — *not* raw JSON Schema, because we also model zones, routing,
  naming, the firewall, and ranking, which JSON Schema can't. Field-level rules
  *can* compile to per-kind JSON Schema internally if standard validation tooling
  is wanted.
- **Relationship to existing docs:** this file becomes canonical; render
  `.claude/docs/properties.md` *from* it (or have properties.md point to it).
  `CLAUDE.md` keeps the prose/behavioral primer and references the schema for
  field tables. No duplication.

## Shape (grounded in the real vault)

```yaml
version: 1

# Folder → ownership/purpose. Zone is derived from path (never frontmatter).
zones:
  daily:    { owner: mixed,  purpose: "captain's log, briefings, audit, tasks" }
  inputs:   { owner: ingest, purpose: "raw material" }
  registry: { owner: shared, purpose: "structured operational entities" }
  wiki:     { owner: agent,  purpose: "synthesized knowledge" }
  notes:    { owner: user,   purpose: "user thinking" }
  efforts:  { owner: user,   purpose: "time-bound projects" }
  _system:  { owner: user,   purpose: "templates, conventions, schema" }

# Universal frontmatter required on every note.
universal:
  required: [kind, owner, date]
  optional: [title, state, aliases, lang]
  fields:
    owner: { type: enum, values: [user, agent, ingest, shared] }
    state: { type: enum, values: [draft, active, review, canon, stale, merge] }
    date:  { type: date }
    lang:  { type: enum, values: [norsk], note: "omit = English default" }

# Reusable enums referenced by kinds.
enums:
  sourcing_status:    [research, approved, sourcing, sampling, active, sell, retail]
  commercial_status:  [sell, discontinued, development, seasonal]
  pipeline_status:    [pending, md, summarized, skip, skip_summary]

# Kinds: zone placement, owner, extra required/optional fields, naming.
kinds:
  effort:
    zone: efforts
    naming: kebab-case
    required: [state]
    optional: [started, deadline, deliverables, oppdatert]
  supplement:
    zone: registry
    naming: kebab-case
    required: [sourcing_status, supplement_form]
    fields:
      supplement_form: { type: enum, values: [capsule, softgel, tablet, powder, liquid] }
      sourcing_status: { type: enum, ref: sourcing_status }
      price: { type: number }
  wiki-topic:
    zone: wiki
    owner: agent
    optional: [sources, source_types, grounded, domain, last_updated]
  captain:
    zone: daily
    owner: user
    naming: "YYYY-MM-DD"
  study:
    zone: registry/studies
    naming: citekey            # {author}_{titleword}_{year}
    required: [pipeline_status]

# Drift healing — alias keys normalized on write/ingest (cures observed drift).
aliases:
  status: state
  oppdatert: last_updated
  type: kind                   # legacy memory/ foreign schema

# Scaffolding: folder a new kind lands in + auto-stamped defaults.
routing:
  defaults: { date: "@today", owner: "@from-zone" }

# Naming validators.
naming:
  kebab-case: '^[a-z0-9]+(-[a-z0-9]+)*$'
  YYYY-MM-DD: '^\d{4}-\d{2}-\d{2}$'

# Single source for the client-data firewall (roadmap 0.1).
sensitive_paths:
  - registry/clients/**
  - inputs/clients/**

# Ranking hints (feeds the metadata reranker; see retrieval brief).
ranking:
  recency_precedence: [last_updated, oppdatert, date, modified_at]
  evergreen_states:   [canon]
  decaying_kinds:     [captain, chat, clip]
```

## Enforcement model (graduated strictness)

Strict-but-survivable — the human edits in Obsidian and must not be blocked:

- **Hard (reject the write):** unknown `kind`, missing universal/kind-required
  field, enum violation, wrong zone for kind, naming violation. Applies to
  `create_note` / `set_frontmatter` / `scaffold_note`.
- **Auto-heal (normalize silently on write/ingest):** alias keys (`status→state`),
  date coercion, owner-from-zone defaulting.
- **Warn (lint, never block):** drift in *existing* notes, deprecated fields,
  missing-but-recommended optionals. Surfaced by `lint_vault`, not the write path
  (so human edits in Obsidian aren't rejected — they're reported and optionally
  healed).

## Server consumption

- `scaffold_note(kind, …)` — required fields, routing, `@today`/`@from-zone`
  defaults.
- write validation on `create_note`/`set_frontmatter` (hard + auto-heal).
- `lint_vault` — drift report + optional `--heal`.
- facet tools — enumerate kinds/states/enums from the file (no hardcoding).
- client-data firewall — reads `sensitive_paths`.
- ranking — `recency_precedence`, `evergreen_states`, `decaying_kinds`.
- exposed as an MCP **resource** so agents fetch the same contract.

## Versioning

`version:` pins the contract; server validates against it and can migrate. Git
history makes schema changes auditable alongside content.

## Open decisions

1. **Location:** `_system/vault-schema.yaml` vs vault root vs `.obsidian-mcp/`.
2. **Canonical vs rendered:** make this file canonical and *generate*
   `properties.md` from it, or keep properties.md and treat this as derived? (Single
   source argues for the former.)
3. **JSON Schema emit:** compile field rules to per-kind JSON Schema for standard
   tooling, or keep validation in-house?
