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

# ── Provenance, authority & temporal model ───────────────────────────────────
# Two facets: (1) node CLASS — authority/temporal/function per citizen;
# (2) derivation EDGES — typed relationships, surfaced to the agent (more
# valuable than ranking). Declarative so a changing corpus / different setup
# just redefines `assignment` and `derivation_edges`.

provenance_classes:
  # authority is illustrative + EVAL-TUNED + query-intent-dependent — a signal,
  # not a fixed global multiplier. temporal selects the clock/decay (below).
  canonical:      { authority: 1.00, temporal: living,   function: answer-from }
  primary:        { authority: 0.90, temporal: living,   function: ground-truth-of-intent }
  synthesized:    { authority: 0.70, temporal: living,   function: answer-with-citation }
  study:          { authority: 0.85, temporal: timeless, function: evidence-cite }       # peer/citekey'd
  corpus:         { authority: 0.60, temporal: timeless, function: attributed-external } # named author
  clipping:       { authority: 0.40, temporal: timeless, function: cite-only }
  conversational: { authority: 0.50, temporal: snapshot, function: mine-for-decisions }  # exported chats
  ephemeral:      { authority: 0.20, temporal: living,   function: ignore-unless-asked }

temporal_models:
  living:   { clock: [frontmatter.updated, git.section_mtime, frontmatter.date, file.mtime],
              decay: soft-recency, section_history: true }   # git-blame per heading region
  snapshot: { clock: [frontmatter.date, git.created], decay: none, section_history: false }
  timeless: { clock: [frontmatter.date], decay: none, section_history: false }

# Most-specific match wins (path > zone+state > zone).
assignment:
  - { match: { path: "inputs/chats/**" },          class: conversational }
  - { match: { path: "inputs/corpora/**" },         class: corpus }
  - { match: { path: "inputs/books/**" },           class: clipping }
  - { match: { path: "inputs/clippings/**" },       class: clipping }
  - { match: { zone: registry, kind: study },       class: study }
  - { match: { zone: registry, state: draft },      class: ephemeral }
  - { match: { zone: registry },                    class: canonical }
  - { match: { zone: wiki },                        class: synthesized }
  - { match: { zone: notes, state: canon },         class: canonical }
  - { match: { zone: notes },                       class: primary }
  - { match: { zone: efforts },                     class: primary }
  - { match: { zone: daily },                       class: primary }

# Typed DERIVATION edges — extracted into a provenance graph (distinct from the
# topical wikilink graph). Surfaced via get_provenance(); see temporal brief.
derivation_edges:
  grounded_in: { from_frontmatter: sources, strength_field: grounded, semantics: evidence }
  cites:       { from_body: "[@{citekey}]", target_class: study,      semantics: evidence }
  builds_on:   { from_frontmatter: builds_on, semantics: lineage }
  refines:     { from_frontmatter: refines,   semantics: lineage }
  opposes:     { from_frontmatter: opposes,   semantics: contrast }
  about:       { from_frontmatter: subject,   semantics: about }
```

### Notes on the provenance model
- **Class = node property; edges = relationships.** The edges are primarily for
  *surfacing how things relate to the agent* (grounding, attribution, impact),
  and only secondarily a ranking input. Studies/corpora are an authority gradient
  within "external," not one bucket.
- **`temporal` selects the clock.** `living` (canonical/primary/synthesized) gets
  git-section history + soft recency; `snapshot` (chats) and `timeless` (studies,
  external) do not evolve, so section-history correctly does not apply. This
  supersedes the earlier flat `recency_precedence` hint.
- **Edge vocabularies already exist in the vault** (`sources`+`grounded`,
  `[@citekey]`, mythos `builds_on/refines/opposes`, `subject`) — this formalizes
  them, it does not invent them.

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
- provenance — `provenance_classes`/`assignment` give each note an authority +
  temporal class; `derivation_edges` build the provenance graph that
  `get_provenance()` surfaces (basis ↑ / impact ↓). See the Temporal Awareness brief.
- temporal — `temporal_models` select the clock/decay per class; `living` enables
  git-section history (edit-recency) and walking `grounded_in` gives evidence-recency.
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
