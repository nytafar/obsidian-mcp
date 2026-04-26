## Context

The MCP server today exposes two write tools: `create_note` and
`edit_note` (`src/mcp_server/tools.py:278` and `:584`). Both go through
`src/services/vault.py::write_file`, which performs a non-atomic
`Path.write_text()`. Higher-level vault operations — rename, move,
delete, frontmatter mutation — are absent, so agents have to assemble
them from full-content rewrites, which is fragile (YAML quoting bugs,
no rollback if the agent's match window is wrong) and dangerous (no
preflight, no atomic guarantee).

The vault is on a file server with daily backups; it is not a git repo.
That sets the rollback story: we do not need (and explicitly do not
want) per-edit version history. We do need to keep the per-tool-call
write atomic at the file level so a crash cannot truncate a note.

The wikilink graph (`note_links` table, populated at index time by
`src/services/links.py`) is in place as of the previous change. It gives
us the substrate for keeping the link graph consistent across moves and
for the opt-in incoming-wikilink rewrite.

The indexer skips any dot-prefixed directory (`src/services/indexer.py:53`),
which means `.trash/` is automatically excluded from `notes_metadata`,
`note_embeddings`, and `note_links` — soft-delete needs no special
indexer wiring.

## Goals / Non-Goals

**Goals:**
- Round out the write surface: `move_note`, `delete_note`, `set_frontmatter`.
- Make `edit_note` robust enough that an agent can reach for it without
  worrying about footguns — atomic writes, dry-run preflight, opt-in
  multi-match, heading-section edits.
- Keep the link graph consistent across renames/moves automatically;
  rewrite incoming wikilinks only when the agent opts in.
- Soft-delete by default so accidents are recoverable without restoring
  from yesterday's backup.

**Non-Goals:**
- Version history (git auto-commit, `note_revisions` table).
- File watching or sub-5-minute reindex.
- Cross-tool-call transactions / two-phase commit.
- Bulk-edit / multi-note-in-one-call surface.
- Heading-aware chunking (separate roadmap item).
- Auto-cleanup of dangling backlinks after delete (use `find_orphans`
  instead).

## Decisions

### D1: Single `write_file` chokepoint with tmp-file + `os.replace`

`src/services/vault.py::write_file` becomes the only path through which
note content reaches disk. Implementation:

```python
def write_file(rel_path: str, content: str) -> None:
    full = validate_path(rel_path)
    full.parent.mkdir(parents=True, exist_ok=True)
    tmp = full.with_name(f".tmp-{full.name}-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, full)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
```

`os.replace` is atomic on the same filesystem (POSIX `rename(2)`).
`.tmp-` prefix means the indexer's existing dot-dir filter doesn't
catch it (the file itself isn't in a dot-dir), but `.tmp-` files at the
top level of folders won't end in `.md` and the indexer only collects
`*.md`, so they're already filtered. Worst-case orphan tmp files just
sit there harmlessly until cleaned up.

**Alternative considered:** writing to a sibling `.tmp/` directory.
Rejected — `os.replace` requires same-filesystem source/dest; keeping
the tmp file next to the destination is simpler and guarantees that.

### D2: Edit modes stay one tool, four mutually exclusive parameter shapes

`edit_note(path, content, append=False, find=None, section=None,
replace_all=False, dry_run=False)`. The mode is selected by which of
`append`, `find`, `section` is set; full-replace is the default. We
validate up front and reject any combination that selects more than one
mode.

**Alternative considered:** splitting into `edit_note_full`,
`edit_note_append`, `edit_note_find`, `edit_note_section`. Rejected —
the symmetry of "edit a note" is the right grouping for the agent's
mental model; four near-identical tools balloon the listed surface
and the docstrings would be 80% duplicate. The mutex check is cheap.

### D3: Section mode uses ATX headings; depth-aware bounds; path-style for collisions

We match the first ATX heading (`^#{1,6}\s+<text>\s*$`) whose trimmed
text equals `section`. The replaced region is the lines between the
matched heading (exclusive) and the next heading at depth ≤ matched
depth (or EOF). The matched heading line itself is preserved.

If multiple headings in the note match, the tool refuses and instructs
the agent to use the path-style form `Parent/Child` to disambiguate.
Path-style match: `parent_text/child_text` finds a heading whose text
is `child_text` and whose nearest enclosing heading at depth ≤ matched
depth has trimmed text `parent_text`. This handles "two `## Tasks`
headings under different `# Project` parents" cleanly.

**Alternative considered:** an integer `section_occurrence` parameter
("the 2nd `## Tasks`"). Rejected — fragile when headings are added or
reordered; path-style is what humans say in conversation anyway.

**Alternative considered:** Setext headings (`====`/`----`). Rejected —
ATX is the dominant Obsidian style and the simpler regex; we can add
Setext later if we ever see a vault that needs it. Document the ATX-only
limitation in the docstring.

### D4: `replace_all` over `expected_count`

We mirror Claude Code's `Edit` tool: a single `replace_all: bool` flag
that flips the multi-match guard off. `expected_count: int` was
considered (more pedantic, requires the agent to actually look at the
note before editing). Rejected — agents already read notes before
editing; the extra friction outweighs the marginal safety. `replace_all`
is a deliberate "I know what I'm doing" gesture that's easy to spot in
diffs.

### D5: `dry_run` returns a unified diff string

We use `difflib.unified_diff(before.splitlines(keepends=True),
after.splitlines(keepends=True), fromfile=path, tofile=path,
lineterm='')` and join. Three-line context default. Returned as a plain
string in the tool result so the agent can read it without extra
parsing. No-op edits return a short "no changes" message rather than an
empty diff (clearer signal to the agent).

`dry_run` works for all four modes — for find/replace and section, the
diff is naturally minimal; for full-replace and append it shows the
full delta.

### D6: `move_note` uses two-step file-then-DB update; rewrites are opt-in

Move sequence:

1. Validate both paths via `validate_path`.
2. Refuse if destination exists.
3. `dest.parent.mkdir(parents=True, exist_ok=True)`.
4. `os.replace(src, dest)` (same FS) or `shutil.move(src, dest)`.
5. In one transaction:
   - `UPDATE notes_metadata SET file_path = :new WHERE file_path = :old`
   - `UPDATE note_links SET target_path = :new WHERE target_path = :old`
   - `UPDATE note_links SET source_path = :new WHERE source_path = :old`
6. If `rewrite_links=True`: pull the set of source notes via the
   existing `get_backlinks_impl` query (or directly off `note_links`),
   open each, run regex rewrites for `[[Old]]`, `[[Old|alias]]`,
   `[[Old#anchor]]`, `[[Old#^block]]`, and the `![[...]]` embed
   variants, and write back through `write_file` (atomic).

Failure handling: if step 4 succeeds but step 5 fails, the DB is
inconsistent until the next reindex. The reindexer reconciles by
content_hash so eventual consistency holds; we log a warning. Truly
atomic FS+DB moves would require either a write-ahead-log or a
journaled approach — overkill at this scale.

**Alternative considered:** rewrite by parsing wikilinks via
`src/services/links.py` extractors. Rejected for now — the regex
substitution is straightforward and we'd otherwise have to plumb
position information back from the extractor. Worth revisiting if we
add structural rewrites later (e.g. heading anchor renames).

### D7: `delete_note` soft-deletes by default to `.trash/<TS>-<name>`

Path layout: `.trash/<YYYYMMDD-HHMMSS>-<original-basename>`. We don't
mirror the original folder structure inside `.trash/` — flat is simpler
and the timestamp+basename is enough to find a deletion. Subfolder
structure is preserved implicitly in the basename only when basenames
collide (timestamp resolves it).

Soft-delete uses `os.replace` (or `shutil.move` if cross-FS) into
`.trash/`. We do NOT touch the DB directly — the next reindex pass
notices the file is gone from the indexed-paths set and removes the row.
FK cascades on `note_embeddings.note_id` and `note_links.source_path` /
`target_path` (CASCADE on delete) handle the dependent rows.

`permanent=True` runs `os.unlink(full_path)`; same reindex-driven cleanup.

**Alternative considered:** mirror folder structure inside `.trash/`
(e.g. `.trash/Cards/Old.md`). Rejected — adds collision risk (delete
twice → second one fails) and provides little value for a personal
vault that the user will periodically empty.

**Alternative considered:** delete the DB rows synchronously inside
`delete_note`. Rejected — the reindex path is already the source of
truth for "what's in the vault"; duplicating the cleanup logic in a
second place is the kind of bifurcation that drifts. Up to ~5 minutes
of stale rows in `notes_metadata` is acceptable.

### D8: `set_frontmatter` reuses or factors out a parse helper

The indexer already parses frontmatter (`src/services/links.py` /
indexer code path). If a public `parse_frontmatter(text) -> (dict,
body_str)` helper exists, reuse it. If parsing is inline, factor a
helper into `src/services/vault.py` (or a new
`src/services/frontmatter.py`) and update the indexer to call it. We
serialize via `yaml.safe_dump(default_flow_style=False, sort_keys=False,
allow_unicode=True)` and reassemble as `---\n<yaml>---\n<body>`.

For the no-frontmatter case (no `---` fence on line 1), we prepend a
fresh block. We deliberately do not try to "rescue" frontmatter that
isn't on line 1 — Obsidian itself ignores it, so silently rewriting
mid-document `---` blocks would misbehave.

### D9: Permission gating reuses `_require_write()`

All five write tools — including the three new ones — call the existing
`_require_write()` helper. No changes to the auth path.

### D10: Docstring framing follows the existing tool-design rule

Docstrings on the three new tools are written symmetrically and refer to
`get_vault_guide` only with neutral framing ("see `get_vault_guide` for
vault conventions"). This is the same rule the `vault-guide` spec
applies to existing tools.

## Risks / Trade-offs

- **Move + DB inconsistency window.** [Risk] If the file move succeeds
  but the DB update fails, `notes_metadata.file_path` points at a
  non-existent file until the next reindex (≤5 min). → **Mitigation:**
  the reindexer is idempotent and content-hash-driven; it converges.
  Log a warning so we notice if it happens repeatedly.
- **Link rewrite regex correctness.** [Risk] Wikilink syntax has a long
  tail (block anchors, heading anchors, aliases, path-style). A naive
  regex misses cases or rewrites too eagerly (e.g. `[[Foo Bar]]` when
  moving `Foo`). → **Mitigation:** rewrite operates only on the exact
  matched title (anchored on `[[` and the next `|`/`#`/`]]`). Cover
  each variant in the smoke test.
- **`.trash/` growth.** [Risk] Soft-delete accumulates files until the
  user manually empties it. → **Mitigation:** no automated cleanup —
  the user said they'll periodically clean it. Document this in the
  tool docstring so the agent knows.
- **Atomic write requires same-FS tmp.** [Risk] If the vault is ever
  bind-mounted across filesystem boundaries, `os.replace` would fail
  with `EXDEV`. → **Mitigation:** today the vault is on one mount;
  if it moves, fall back to `shutil.move` (non-atomic) and log a
  warning.
- **Section-mode regex on edge cases.** [Risk] Notes that put `#` in
  unusual places (e.g. inside fenced code blocks) could confuse
  heading detection. → **Mitigation:** strip fenced and inline code
  blocks before scanning, the same pre-processing
  `src/services/links.py` already does.
- **Frontmatter round-trip preserves comments?** [Risk] PyYAML's
  `safe_dump` does not preserve YAML comments; if the user's
  frontmatter has comments, they'll be lost on the first
  `set_frontmatter` call. → **Mitigation:** document this in the
  docstring; the alternative (`ruamel.yaml`) adds a dependency for a
  rare case.

## Migration Plan

No data migration. All changes are additive at the database level
(reusing existing tables). Deploy is the standard `make deploy`.

Rollback strategy:
- The new tools can be unregistered by reverting `src/mcp_server/server.py`.
- The `write_file` atomic-write change is backward-compatible — old
  callers see the same interface; revert the function body if needed.
- No DB migrations to roll back.

## Open Questions

- Should `move_note` accept a `to_path` that's just a folder
  (`Cards/Archive/`) and infer the destination basename from the
  source? Convenient for "archive this note" workflows. **Lean: defer**
  — agents can construct the full path easily and the inference rule
  ("trailing slash means folder") is one more thing to remember.
- Should `delete_note` accept a list of paths? **Lean: no** — that's
  `bulk_edit` territory and we're explicitly deferring bulk operations.
- Should `set_frontmatter` support a `merge_lists: bool` for tags
  (append-vs-replace semantics)? **Lean: no for v1** — replace
  semantics are simpler and predictable; agents can read first if they
  want union behavior. Revisit if usage shows this is a footgun.
