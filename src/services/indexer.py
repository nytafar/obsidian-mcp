import asyncio
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert

from src.config import settings
from src.database import async_session
from src.models.db import NoteEmbedding, NoteLink, NoteMetadata, OAuthCode, OAuthToken
from src.services.embeddings import embed_note
from src.services.links import build_vault_index, extract_links, resolve_target
from src.services.vault import _vault_root, extract_tags, parse_frontmatter

# Module-level flag the dashboard reads to surface "link extraction in
# progress" while the one-shot backfill is running.
link_backfill_in_progress: bool = False


def _sanitize_frontmatter(fm: dict) -> dict:
    """Convert non-JSON-serializable values (dates, etc) to strings."""
    sanitized = {}
    for k, v in fm.items():
        if isinstance(v, (str, int, float, bool, type(None))):
            sanitized[k] = v
        elif isinstance(v, list):
            sanitized[k] = [str(i) if not isinstance(i, (str, int, float, bool, type(None))) else i for i in v]
        elif isinstance(v, dict):
            sanitized[k] = _sanitize_frontmatter(v)
        else:
            sanitized[k] = str(v)
    return sanitized

logger = logging.getLogger(__name__)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def index_vault():
    """Scan vault, upsert notes_metadata with tsvector, remove deleted files."""
    vault = _vault_root()
    logger.info("Starting vault index scan...")

    # Collect all .md files (skip dot-dirs)
    files: dict[str, Path] = {}
    for p in vault.rglob("*.md"):
        rel = p.relative_to(vault)
        if any(part.startswith(".") for part in rel.parts):
            continue
        files[str(rel)] = p

    logger.info(f"Found {len(files)} markdown files")

    async with async_session() as session:
        # Get existing hashes
        result = await session.execute(
            select(NoteMetadata.file_path, NoteMetadata.content_hash)
        )
        existing = {row.file_path: row.content_hash for row in result.fetchall()}

        # Determine changes
        to_upsert = []
        for rel_path, full_path in files.items():
            try:
                raw = full_path.read_text(encoding="utf-8", errors="strict")
            except UnicodeDecodeError:
                logger.warning(f"Skipping non-UTF8 file: {rel_path}")
                continue
            except Exception as e:
                logger.warning(f"Failed to read {rel_path}: {e}")
                continue

            h = _content_hash(raw)
            if rel_path in existing and existing[rel_path] == h:
                continue  # No change

            frontmatter, content = parse_frontmatter(raw)
            title = frontmatter.get("title") or full_path.stem
            tags = extract_tags(raw, frontmatter)
            stat = full_path.stat()

            to_upsert.append({
                "file_path": rel_path,
                "title": title,
                "tags": tags,
                "frontmatter": _sanitize_frontmatter(frontmatter),
                "content_hash": h,
                "file_size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            })

        # Upsert changed files
        if to_upsert:
            for batch_start in range(0, len(to_upsert), 100):
                batch = to_upsert[batch_start:batch_start + 100]
                stmt = insert(NoteMetadata).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["file_path"],
                    set_={
                        "title": stmt.excluded.title,
                        "tags": stmt.excluded.tags,
                        "frontmatter": stmt.excluded.frontmatter,
                        "content_hash": stmt.excluded.content_hash,
                        "file_size": stmt.excluded.file_size,
                        "modified_at": stmt.excluded.modified_at,
                        "indexed_at": text("now()"),
                    },
                )
                await session.execute(stmt)
            await session.commit()
            logger.info(f"Upserted {len(to_upsert)} notes")

        # Update tsvectors for changed notes
        if to_upsert:
            paths = [n["file_path"] for n in to_upsert]
            for path in paths:
                full_path = vault / path
                try:
                    try:
                        raw = full_path.read_text(encoding="utf-8", errors="strict")
                    except UnicodeDecodeError:
                        logger.warning(f"Skipping non-UTF8 file: {path}")
                        continue
                    _, content = parse_frontmatter(raw)
                    await session.execute(
                        text("""
                            UPDATE notes_metadata
                            SET content_tsvector = to_tsvector('english', :content)
                            WHERE file_path = :path
                        """),
                        {"content": content[:100000], "path": path},  # Truncate very large files
                    )
                except Exception as e:
                    logger.warning(f"Failed to update tsvector for {path}: {e}")
            await session.commit()
            logger.info(f"Updated tsvectors for {len(paths)} notes")

        # Remove deleted files
        deleted_paths = set(existing.keys()) - set(files.keys())
        if deleted_paths:
            await session.execute(
                delete(NoteMetadata).where(NoteMetadata.file_path.in_(deleted_paths))
            )
            await session.commit()
            logger.info(f"Removed {len(deleted_paths)} deleted notes")

        # ── Link extraction for changed notes ───────────────────────────
        # We rebuild the vault_index here (post-commit), then for each
        # changed note delete-and-reinsert its rows in `note_links`. New or
        # renamed notes also get a re-resolution pass that updates any
        # previously-dangling rows now matching their path.
        if to_upsert or deleted_paths:
            await _update_links_for_changed(
                session, vault, [n["file_path"] for n in to_upsert]
            )

    logger.info("Vault index scan complete")


async def _update_links_for_changed(session, vault: Path, changed_paths: list[str]):
    """Re-extract and upsert links for the given changed paths.

    Builds a fresh `vault_index` from `notes_metadata`, then for every changed
    note: deletes existing rows, extracts links, resolves targets, inserts.
    Finally, runs a re-resolution pass to attach previously-dangling rows
    whose `target_path` matches any of the changed notes.
    """
    # Build vault_index once for the entire pass.
    rows = (await session.execute(
        select(NoteMetadata.file_path, NoteMetadata.id)
    )).all()
    vault_index = build_vault_index([(r.file_path, r.id) for r in rows])
    paths_to_id: dict[str, int] = vault_index["paths"]

    if changed_paths:
        # Process changed notes' outgoing links.
        change_ids = [paths_to_id[p] for p in changed_paths if p in paths_to_id]
        if change_ids:
            await session.execute(
                delete(NoteLink).where(NoteLink.source_note_id.in_(change_ids))
            )
            new_rows: list[dict] = []
            for path in changed_paths:
                src_id = paths_to_id.get(path)
                if src_id is None:
                    continue
                full_path = vault / path
                try:
                    raw = full_path.read_text(encoding="utf-8", errors="strict")
                except (UnicodeDecodeError, FileNotFoundError, OSError):
                    continue
                _, content = parse_frontmatter(raw)
                for link in extract_links(content):
                    target_id = resolve_target(link.target, path, vault_index)
                    new_rows.append({
                        "source_note_id": src_id,
                        "target_note_id": target_id,
                        "target_path": link.target[:1024],
                        "link_text": link.link_text,
                        "kind": link.kind,
                        "position": link.position,
                    })
            if new_rows:
                for batch_start in range(0, len(new_rows), 1000):
                    await session.execute(
                        insert(NoteLink).values(
                            new_rows[batch_start:batch_start + 1000]
                        )
                    )
            await session.commit()
            logger.info(
                f"Re-extracted links for {len(change_ids)} notes "
                f"({len(new_rows)} link rows)"
            )

    # Re-resolution pass: any newly-arrived note may resolve previously
    # dangling rows. We patch `target_note_id` for rows whose `target_path`
    # matches one of the changed paths in a few canonical forms.
    for path in changed_paths:
        nid = paths_to_id.get(path)
        if nid is None:
            continue
        stem = os.path.splitext(os.path.basename(path))[0]
        path_no_ext = path[:-3] if path.endswith(".md") else path
        await session.execute(
            text("""
                UPDATE note_links
                SET target_note_id = :nid
                WHERE target_note_id IS NULL
                  AND target_path IN (:full, :stem, :no_ext)
            """),
            {"nid": nid, "full": path, "stem": stem, "no_ext": path_no_ext},
        )
    if changed_paths:
        await session.commit()


async def link_backfill_pass():
    """One-shot backfill that populates `note_links` for every note.

    Runs on startup if the table is empty. Iterates all notes, extracts
    links, resolves targets, batches inserts, and logs progress.
    """
    global link_backfill_in_progress
    vault = _vault_root()
    async with async_session() as session:
        existing = (await session.execute(
            select(func.count(NoteLink.id))
        )).scalar() or 0
        if existing > 0:
            return

        rows = (await session.execute(
            select(NoteMetadata.id, NoteMetadata.file_path)
        )).all()
        if not rows:
            return

        link_backfill_in_progress = True
        logger.info(f"Starting link backfill across {len(rows)} notes")

        vault_index = build_vault_index([(r.file_path, r.id) for r in rows])

        try:
            buffer: list[dict] = []
            for i, row in enumerate(rows, start=1):
                full_path = vault / row.file_path
                try:
                    raw = full_path.read_text(encoding="utf-8", errors="strict")
                except (UnicodeDecodeError, FileNotFoundError, OSError):
                    continue
                _, content = parse_frontmatter(raw)
                for link in extract_links(content):
                    target_id = resolve_target(link.target, row.file_path, vault_index)
                    buffer.append({
                        "source_note_id": row.id,
                        "target_note_id": target_id,
                        "target_path": link.target[:1024],
                        "link_text": link.link_text,
                        "kind": link.kind,
                        "position": link.position,
                    })
                if len(buffer) >= 1000:
                    await session.execute(insert(NoteLink).values(buffer))
                    await session.commit()
                    buffer.clear()
                if i % 500 == 0:
                    logger.info(f"Link backfill: {i}/{len(rows)} notes")

            if buffer:
                await session.execute(insert(NoteLink).values(buffer))
                await session.commit()

            logger.info(f"Link backfill complete: {len(rows)} notes scanned")
        finally:
            link_backfill_in_progress = False


async def embed_vault():
    """Embed notes that don't have embeddings yet or have changed."""
    vault = _vault_root()
    logger.info("Starting embedding pass...")

    async with async_session() as session:
        # Find notes without embeddings or with stale embeddings
        result = await session.execute(
            text("""
                SELECT nm.id, nm.file_path, nm.content_hash
                FROM notes_metadata nm
                WHERE nm.embedded_content_hash IS NULL
                   OR nm.embedded_content_hash != nm.content_hash
                ORDER BY nm.modified_at DESC
            """)
        )
        unembedded = result.fetchall()

        if not unembedded:
            logger.info("All notes already embedded")
            return

        logger.info(f"Embedding {len(unembedded)} notes...")
        total_chunks = 0
        for i, row in enumerate(unembedded):
            try:
                full_path = vault / row.file_path
                try:
                    raw = full_path.read_text(encoding="utf-8", errors="strict")
                except UnicodeDecodeError:
                    logger.warning(f"Skipping non-UTF8 file: {row.file_path}")
                    continue
                _, content = parse_frontmatter(raw)

                # Get the NoteMetadata object
                note_result = await session.execute(
                    select(NoteMetadata).where(NoteMetadata.id == row.id)
                )
                note = note_result.scalar_one()

                chunks = await embed_note(session, note, content)
                total_chunks += chunks
                await session.commit()

                if (i + 1) % 50 == 0:
                    logger.info(f"Embedded {i + 1}/{len(unembedded)} notes ({total_chunks} chunks)")
            except Exception as e:
                logger.warning(f"Failed to embed {row.file_path}: {e}")
                await session.rollback()

        logger.info(f"Embedding complete: {len(unembedded)} notes, {total_chunks} chunks")


async def cleanup_expired_tokens():
    """Delete expired/revoked OAuth codes and tokens older than 7 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    async with async_session() as session:
        # Clean up expired/used auth codes
        result = await session.execute(
            delete(OAuthCode).where(
                or_(
                    OAuthCode.expires_at < cutoff,
                    OAuthCode.used == True,
                )
            )
        )
        codes_deleted = result.rowcount

        # Clean up expired/revoked tokens
        result = await session.execute(
            delete(OAuthToken).where(
                or_(
                    OAuthToken.expires_at < cutoff,
                    OAuthToken.revoked == True,
                )
            )
        )
        tokens_deleted = result.rowcount

        await session.commit()

        if codes_deleted or tokens_deleted:
            logger.info(f"Token cleanup: {codes_deleted} codes, {tokens_deleted} tokens removed")


def _is_paused() -> bool:
    """Check if a panel-driven action has paused the indexer."""
    try:
        from src.control_panel import routes as panel_routes
        return bool(getattr(panel_routes, "indexer_paused", False))
    except Exception:
        return False


async def run_indexer_loop():
    """Run indexer on startup and then periodically."""
    try:
        await index_vault()
    except Exception as e:
        logger.error(f"Initial index failed: {e}")

    try:
        await link_backfill_pass()
    except Exception as e:
        logger.error(f"Link backfill failed: {e}")

    try:
        await embed_vault()
    except Exception as e:
        logger.error(f"Initial embedding failed: {e}")

    consecutive_failures = 0
    while True:
        await asyncio.sleep(settings.index_interval_seconds)
        if _is_paused():
            continue
        try:
            await index_vault()
            await embed_vault()
            await cleanup_expired_tokens()
            consecutive_failures = 0
        except Exception as e:
            consecutive_failures += 1
            logger.error(f"Periodic task failed ({consecutive_failures} consecutive): {e}")
            if consecutive_failures >= 5:
                logger.critical("Indexer has failed 5+ consecutive times — manual intervention required")
