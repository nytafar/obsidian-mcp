"""Shared SQL filter helper for NoteMetadata queries.

This is the single supported way to apply `folder`, `tags`, and `frontmatter`
filters to a `select` over `NoteMetadata`. Inlining the equivalents in callers
risks divergence (escape rules, containment semantics).
"""

from sqlalchemy import Select

from src.models.db import NoteMetadata


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def apply_note_filters(
    stmt: Select,
    *,
    folder: str | None = None,
    tags: list[str] | None = None,
    frontmatter: dict | None = None,
) -> Select:
    """Append optional `folder`, `tags`, `frontmatter` predicates to a select over NoteMetadata.

    - `folder`: prefix match on `file_path`. LIKE wildcards (`%`, `_`, `\\`) are escaped.
    - `tags`: ARRAY containment (`notes_metadata.tags @> ARRAY[...]`). AND semantics.
    - `frontmatter`: JSONB containment (`notes_metadata.frontmatter @> :json`). Strict types.

    None or empty argument means "no filter" — the predicate is not appended.
    """
    if folder:
        escaped = _escape_like(folder)
        stmt = stmt.where(NoteMetadata.file_path.like(f"{escaped}%", escape="\\"))
    if tags:
        stmt = stmt.where(NoteMetadata.tags.contains(tags))
    if frontmatter:
        stmt = stmt.where(NoteMetadata.frontmatter.contains(frontmatter))
    return stmt
