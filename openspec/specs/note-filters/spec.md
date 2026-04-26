# note-filters Specification

## Purpose
TBD - created by archiving change note-filters. Update Purpose after archive.
## Requirements
### Requirement: Shared filter helper

The system SHALL provide a single helper function that applies optional `folder`, `tags`, and `frontmatter` filters to a SQLAlchemy `select` over `NoteMetadata`.

#### Scenario: Folder prefix filter with LIKE escape

- **WHEN** the helper is invoked with `folder="Projects/100%/"`
- **THEN** the helper SHALL escape LIKE wildcards (`%`, `_`, `\`) in the folder string and apply a `file_path LIKE 'Projects/100\%/%' ESCAPE '\'` predicate
- **AND** the helper SHALL not apply a folder predicate when `folder` is None or empty string

#### Scenario: Tag filter uses ARRAY containment

- **WHEN** the helper is invoked with `tags=["project", "active"]`
- **THEN** the helper SHALL apply a predicate equivalent to `notes_metadata.tags @> ARRAY['project','active']::text[]`
- **AND** the helper SHALL not apply a tag predicate when `tags` is None or an empty list

#### Scenario: Frontmatter filter uses JSONB containment

- **WHEN** the helper is invoked with `frontmatter={"status": "draft", "type": "card"}`
- **THEN** the helper SHALL apply a predicate equivalent to `notes_metadata.frontmatter @> '{"status":"draft","type":"card"}'::jsonb`
- **AND** the helper SHALL not apply a frontmatter predicate when `frontmatter` is None or an empty dict

#### Scenario: Multiple filters AND-combined

- **WHEN** the helper is invoked with non-empty `folder`, `tags`, and `frontmatter`
- **THEN** all three predicates SHALL be applied with AND semantics

### Requirement: `keyword_search` accepts tag and frontmatter filters

The `keyword_search` MCP tool SHALL accept optional `tags: list[str]` and `frontmatter: dict[str, str | int | float | bool]` parameters in addition to the existing `query`, `folder`, and `limit`.

#### Scenario: Existing call without new parameters

- **WHEN** an existing client calls `keyword_search(query="docker", folder="Projects/", limit=10)` without supplying `tags` or `frontmatter`
- **THEN** behavior SHALL be identical to the pre-change implementation, modulo the underlying query refactor

#### Scenario: Tag filter applied

- **WHEN** an agent calls `keyword_search(query="status update", tags=["active"])`
- **THEN** the system SHALL only return notes whose `tags` array contains `active` AND match the tsvector query
- **AND** results SHALL still be ordered by `ts_rank_cd` descending

#### Scenario: Frontmatter filter applied

- **WHEN** an agent calls `keyword_search(query="quarterly", frontmatter={"status": "draft"})`
- **THEN** the system SHALL only return notes whose JSONB frontmatter contains `status: "draft"` AND match the tsvector query

### Requirement: `semantic_search` accepts tag and frontmatter filters

The `semantic_search` MCP tool SHALL accept optional `tags: list[str]` and `frontmatter: dict[str, str | int | float | bool]` parameters in addition to the existing `query`, `folder`, and `limit`.

#### Scenario: Existing call without new parameters

- **WHEN** an existing client calls `semantic_search(query="x", folder="Projects/")` without supplying `tags` or `frontmatter`
- **THEN** behavior SHALL be identical to the pre-change implementation

#### Scenario: Combined filters

- **WHEN** an agent calls `semantic_search(query="ideas about onboarding", tags=["product"], frontmatter={"status": "active"})`
- **THEN** the system SHALL apply all filters at the SQL level before ordering by cosine distance and applying the `limit`

### Requirement: `list_notes` accepts tag and frontmatter filters and reads from the index

The `list_notes` MCP tool SHALL read note metadata from `notes_metadata` (rather than walking the filesystem) and SHALL accept optional `tags` and `frontmatter` parameters in addition to the existing `folder` and `limit`.

#### Scenario: Listing without filters

- **WHEN** an agent calls `list_notes()` with no arguments
- **THEN** the system SHALL return up to 50 notes ordered by `modified_at` descending, each with `path`, `size`, and `modified` fields preserving the existing response shape

#### Scenario: Listing with folder filter

- **WHEN** an agent calls `list_notes(folder="Cards/")`
- **THEN** the system SHALL return only notes whose `file_path` starts with `Cards/`

#### Scenario: Listing with tag and frontmatter filters

- **WHEN** an agent calls `list_notes(folder="Cards/", tags=["idea"], frontmatter={"status": "active"})`
- **THEN** the system SHALL apply all three filters and return matching notes ordered by `modified_at` descending

#### Scenario: Newly created on-disk note not yet indexed

- **WHEN** a note exists on disk but has not yet been picked up by the indexer
- **THEN** `list_notes` SHALL not return that note until the next index pass completes

### Requirement: `get_recent` accepts tag and frontmatter filters

The `get_recent` MCP tool SHALL accept optional `tags` and `frontmatter` parameters in addition to the existing `limit` and `folder`, and route folder/tag/frontmatter filtering through the shared filter helper.

#### Scenario: Existing call without new parameters

- **WHEN** an existing client calls `get_recent(limit=20, folder="Projects/")`
- **THEN** behavior SHALL be identical to the pre-change implementation

#### Scenario: Recent notes filtered by tag

- **WHEN** an agent calls `get_recent(limit=10, tags=["meeting"])`
- **THEN** the system SHALL return up to 10 notes tagged `meeting`, ordered by `modified_at` descending

### Requirement: Symmetric, non-ranking docstrings on the two search tools

The system SHALL document `keyword_search` and `semantic_search` so that neither is described as the "primary" tool. Each docstring SHALL state the tool's intended use case and direct the agent to the other tool when its use case fits better.

#### Scenario: `keyword_search` docstring

- **WHEN** an MCP client lists tools
- **THEN** the `keyword_search` description SHALL identify it as the right choice for exact identifiers, proper nouns, code symbols, and known phrases
- **AND** SHALL NOT describe itself as the primary or default search tool
- **AND** SHALL mention `semantic_search` as the alternative for conceptual queries

#### Scenario: `semantic_search` docstring

- **WHEN** an MCP client lists tools
- **THEN** the `semantic_search` description SHALL identify it as the right choice for conceptual or paraphrased queries
- **AND** SHALL NOT describe itself as the primary or default search tool
- **AND** SHALL mention `keyword_search` as the alternative for exact-match queries

### Requirement: Usage logs capture new parameters

Calls that include the new `tags` or `frontmatter` parameters SHALL record them in `usage_logs.params` (subject to the existing string-truncation behavior of `_tracked`).

#### Scenario: Tag filter logged

- **WHEN** an agent calls `semantic_search(query="x", tags=["a","b"])`
- **THEN** the corresponding `usage_logs` row SHALL have `params` containing both `query` and the `tags` list

