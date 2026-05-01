import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_session
from src.mcp_server.auth import hash_key
from src.models.db import APIKey, NoteEmbedding, NoteLink, NoteMetadata, OAuthClient, OAuthToken, UsageLog


def _reembed_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="reembed-confirm")


def _humanize_delta(dt: datetime | None) -> str:
    if dt is None:
        return "never"
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    seconds = int((now - dt).total_seconds())
    if seconds < 45:
        return "just now"
    if seconds < 3600:
        m = max(1, seconds // 60)
        return f"{m} min ago"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = seconds // 86400
    return f"{d} day{'s' if d != 1 else ''} ago"

router = APIRouter(prefix="/admin", tags=["panel"])
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "templates")
)


async def _graph_stats(session: AsyncSession) -> dict:
    """Return totals + top hub notes for the dashboard's Graph widget."""
    total_links = (
        await session.execute(select(func.count(NoteLink.id)))
    ).scalar() or 0
    dangling_links = (
        await session.execute(
            select(func.count(NoteLink.id)).where(NoteLink.target_note_id.is_(None))
        )
    ).scalar() or 0

    # Orphans: notes that appear in neither source_note_id nor target_note_id.
    orphans_stmt = text("""
        SELECT count(*) FROM notes_metadata nm
        WHERE nm.id NOT IN (
            SELECT source_note_id FROM note_links WHERE source_note_id IS NOT NULL
            UNION
            SELECT target_note_id FROM note_links WHERE target_note_id IS NOT NULL
        )
    """)
    orphan_count = (await session.execute(orphans_stmt)).scalar() or 0

    # Top 5 hub notes by inbound resolved-link count.
    hubs_stmt = text("""
        SELECT nm.file_path, nm.title, count(nl.id) AS hits
        FROM note_links nl
        JOIN notes_metadata nm ON nm.id = nl.target_note_id
        WHERE nl.target_note_id IS NOT NULL
        GROUP BY nm.id, nm.file_path, nm.title
        ORDER BY hits DESC
        LIMIT 5
    """)
    hub_rows = (await session.execute(hubs_stmt)).fetchall()
    top_hubs = [
        {"path": r.file_path, "title": r.title, "hits": int(r.hits)}
        for r in hub_rows
    ]

    return {
        "total_links": int(total_links),
        "dangling_links": int(dangling_links),
        "orphan_count": int(orphan_count),
        "top_hubs": top_hubs,
    }


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: AsyncSession = Depends(get_session)):
    notes_count = (await session.execute(select(func.count(NoteMetadata.id)))).scalar() or 0
    notes_with_embeddings = (await session.execute(
        select(func.count(func.distinct(NoteEmbedding.note_id)))
    )).scalar() or 0
    keys_count = (await session.execute(
        select(func.count(APIKey.id)).where(APIKey.is_active == True)
    )).scalar() or 0

    requests_today = (await session.execute(
        text("SELECT count(*) FROM usage_logs WHERE created_at >= date_trunc('day', now())")
    )).scalar() or 0

    # Embedding coverage
    embedding_pct = round(notes_with_embeddings / notes_count * 100) if notes_count else 0

    # Reindex stats (last 24h)
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    reindexed_24h = (await session.execute(
        select(func.count(NoteMetadata.id)).where(NoteMetadata.indexed_at >= cutoff_24h)
    )).scalar() or 0
    last_indexed_at = (await session.execute(
        select(func.max(NoteMetadata.indexed_at))
    )).scalar()

    # Recent usage
    result = await session.execute(
        select(UsageLog).order_by(UsageLog.created_at.desc()).limit(10)
    )
    def _usage_detail(tool: str, params: dict | None) -> str | None:
        if not params:
            return None
        if tool in ("search_notes", "keyword_search", "semantic_search"):
            return params.get("query")
        if tool in ("read_note", "create_note", "edit_note"):
            return params.get("path")
        if tool in ("list_notes", "get_recent"):
            return params.get("folder") or None
        return None
    recent_usage = [
        {
            "tool": l.tool,
            "detail": _usage_detail(l.tool, l.params),
            "created_at": l.created_at.isoformat(),
        }
        for l in result.scalars().all()
    ]

    graph = await _graph_stats(session)
    from src.services.indexer import link_backfill_in_progress

    return templates.TemplateResponse(request, "dashboard.html", {
        "active": "dashboard",
        "stats": {
            "notes_indexed": notes_count,
            "notes_with_embeddings": notes_with_embeddings,
            "embedding_pct": embedding_pct,
            "active_keys": keys_count,
            "requests_today": requests_today,
        },
        "recent_usage": recent_usage,
        "reindexed_24h": reindexed_24h,
        "last_indexed_iso": last_indexed_at.isoformat() if last_indexed_at else None,
        "last_indexed_rel": _humanize_delta(last_indexed_at),
        "index_interval": settings.index_interval_seconds,
        "graph": graph,
        "graph_backfill_running": link_backfill_in_progress,
    })


@router.get("/keys", response_class=HTMLResponse)
async def keys_page(request: Request, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(APIKey).order_by(APIKey.created_at.desc()))
    keys = []
    for k in result.scalars().all():
        keys.append({
            "id": k.id,
            "name": k.name,
            "key_prefix": k.key_prefix,
            "permission": k.permission,
            "is_active": k.is_active,
            "created_at": k.created_at.isoformat(),
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        })
    new_key = request.query_params.get("new_key")
    return templates.TemplateResponse(request, "keys.html", {
        "active": "keys", "keys": keys, "new_key": new_key,
    })


@router.post("/keys/create")
async def create_key_form(
    name: str = Form(...),
    permission: str = Form("read"),
    session: AsyncSession = Depends(get_session),
):
    raw_key = f"omcp_{secrets.token_hex(24)}"
    api_key = APIKey(
        name=name,
        key_hash=hash_key(raw_key),
        key_prefix=raw_key[:12],
        permission=permission,
    )
    session.add(api_key)
    await session.commit()
    return RedirectResponse(f"/admin/keys?new_key={raw_key}", status_code=303)


@router.post("/keys/delete-revoked")
async def delete_all_revoked(session: AsyncSession = Depends(get_session)):
    from sqlalchemy import delete as sa_delete, update as sa_update
    # Null out usage_log references before deleting to satisfy FK constraint
    revoked_ids = (await session.execute(
        select(APIKey.id).where(APIKey.is_active == False)
    )).scalars().all()
    if revoked_ids:
        await session.execute(
            sa_update(UsageLog).where(UsageLog.key_id.in_(revoked_ids)).values(key_id=None)
        )
        await session.execute(sa_delete(APIKey).where(APIKey.id.in_(revoked_ids)))
        await session.commit()
    return RedirectResponse("/admin/keys", status_code=303)


@router.post("/keys/{key_id}/revoke")
async def revoke_key_form(key_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(APIKey).where(APIKey.id == key_id))
    api_key = result.scalar_one_or_none()
    if api_key:
        api_key.is_active = False
        await session.commit()
    return RedirectResponse("/admin/keys", status_code=303)


@router.post("/keys/{key_id}/delete")
async def delete_key_form(key_id: int, session: AsyncSession = Depends(get_session)):
    from sqlalchemy import update as sa_update
    result = await session.execute(select(APIKey).where(APIKey.id == key_id, APIKey.is_active == False))
    api_key = result.scalar_one_or_none()
    if api_key:
        await session.execute(
            sa_update(UsageLog).where(UsageLog.key_id == key_id).values(key_id=None)
        )
        await session.delete(api_key)
        await session.commit()
    return RedirectResponse("/admin/keys", status_code=303)


@router.get("/oauth", response_class=HTMLResponse)
async def oauth_page(request: Request, session: AsyncSession = Depends(get_session)):
    now = datetime.now(timezone.utc)
    result = await session.execute(select(OAuthClient).order_by(OAuthClient.created_at.desc()))
    clients = []
    for c in result.scalars().all():
        # Only show tokens that are currently usable — expired or revoked ones
        # are DB-level noise and are cleaned up by the indexer loop.
        token_result = await session.execute(
            select(OAuthToken)
            .where(
                OAuthToken.client_id == c.client_id,
                OAuthToken.revoked == False,
                OAuthToken.expires_at > now,
            )
            .order_by(OAuthToken.created_at.desc())
        )
        tokens = []
        for t in token_result.scalars().all():
            tokens.append({
                "id": t.id,
                "token_type": t.token_type,
                "scope": t.scope,
                "revoked": t.revoked,
                "expired": False,
                "expires_at": t.expires_at.isoformat(),
                "created_at": t.created_at.isoformat(),
            })
        clients.append({
            "client_id": c.client_id,
            "client_name": c.client_name,
            "created_at": c.created_at.isoformat(),
            "tokens": tokens,
        })
    return templates.TemplateResponse(request, "oauth.html", {
        "active": "oauth", "clients": clients,
    })


@router.post("/oauth/{client_id}/delete")
async def delete_oauth_client(client_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
    client = result.scalar_one_or_none()
    if client:
        await session.delete(client)
        await session.commit()
    return RedirectResponse("/admin/oauth", status_code=303)


@router.post("/oauth/token/{token_id}/revoke")
async def revoke_oauth_token(token_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(OAuthToken).where(OAuthToken.id == token_id))
    token = result.scalar_one_or_none()
    if token:
        token.revoked = True
        await session.commit()
    return RedirectResponse("/admin/oauth", status_code=303)


@router.post("/oauth/token/{token_id}/scope")
async def update_oauth_token_scope(
    token_id: int,
    scope: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    if scope not in ("read", "readwrite"):
        return RedirectResponse("/admin/oauth", status_code=303)
    result = await session.execute(select(OAuthToken).where(OAuthToken.id == token_id))
    token = result.scalar_one_or_none()
    if token and not token.revoked:
        token.scope = scope
        await session.commit()
    return RedirectResponse("/admin/oauth", status_code=303)


@router.get("/usage", response_class=HTMLResponse)
async def usage_page(request: Request, session: AsyncSession = Depends(get_session)):
    # Recent logs with attributed actor (API key name+prefix or OAuth client name)
    result = await session.execute(
        text("""
            SELECT
                ul.id,
                ul.tool,
                ul.duration_ms,
                ul.created_at,
                ak.name        AS api_key_name,
                ak.key_prefix  AS api_key_prefix,
                oc.client_name AS oauth_client_name
            FROM usage_logs ul
            LEFT JOIN api_keys ak ON ul.key_id = ak.id
            LEFT JOIN oauth_tokens ot ON ul.oauth_token_id = ot.id
            LEFT JOIN oauth_clients oc ON ot.client_id = oc.client_id
            ORDER BY ul.created_at DESC
            LIMIT 100
        """)
    )
    logs = []
    for r in result.fetchall():
        if r.api_key_name:
            actor_name = r.api_key_name
            actor_detail = r.api_key_prefix
        elif r.oauth_client_name:
            actor_name = r.oauth_client_name
            actor_detail = "OAuth"
        else:
            actor_name = None
            actor_detail = None
        logs.append({
            "tool": r.tool,
            "duration_ms": r.duration_ms,
            "created_at": r.created_at.isoformat(),
            "actor_name": actor_name,
            "actor_detail": actor_detail,
        })

    # Chart data: requests per day for last 7 days
    chart_result = await session.execute(
        text("""
            SELECT date_trunc('day', created_at)::date AS day, count(*) AS cnt
            FROM usage_logs
            WHERE created_at >= now() - interval '7 days'
            GROUP BY day ORDER BY day
        """)
    )
    chart_rows = chart_result.fetchall()
    chart_data = {
        "labels": [r.day.strftime("%m/%d") for r in chart_rows],
        "values": [r.cnt for r in chart_rows],
    }

    return templates.TemplateResponse(request, "usage.html", {
        "active": "usage", "logs": logs, "chart_data": chart_data,
    })


@router.get("/vault", response_class=HTMLResponse)
async def vault_page(request: Request):
    folder = request.query_params.get("folder", "")
    selected_note = request.query_params.get("note")

    vault = Path(settings.vault_path)
    base = vault / folder if folder else vault

    # Breadcrumbs
    breadcrumbs = []
    if folder:
        parts = Path(folder).parts
        for i, part in enumerate(parts):
            breadcrumbs.append({
                "name": part,
                "path": str(Path(*parts[: i + 1])),
            })

    # List folders and files
    folders = []
    notes = []
    if base.is_dir():
        for item in sorted(base.iterdir()):
            if item.name.startswith("."):
                continue
            rel = str(item.relative_to(vault))
            if item.is_dir():
                folders.append({"name": item.name, "path": rel})
            elif item.suffix == ".md":
                notes.append({"name": item.stem, "path": rel})

    # Selected note content
    note_content = None
    note_title = None
    note_tags = []
    if selected_note:
        from src.services.vault import read_file
        try:
            data = read_file(selected_note)
            note_content = data["content"]
            note_title = data["title"]
            note_tags = data["tags"]
        except Exception:
            note_content = "Error reading note"

    return templates.TemplateResponse(request, "vault.html", {
        "active": "vault",
        "current_folder": folder,
        "breadcrumbs": breadcrumbs,
        "folders": folders,
        "notes": notes,
        "selected_note": selected_note,
        "note_content": note_content,
        "note_title": note_title,
        "note_tags": note_tags,
    })


def _mask_openai_key(key: str | None) -> str:
    """Return a display-safe prefix/suffix of an OpenAI key.

    Format: `key[:8] + "..." + key[-4:]`. Returns "(not set)" if missing.
    Short keys (less than 13 chars) collapse to a fully redacted form so the
    full key is never recoverable from the rendered HTML.
    """
    if not key:
        return "(not set)"
    if len(key) < 13:
        return "***"
    return f"{key[:8]}...{key[-4:]}"


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, session: AsyncSession = Depends(get_session)):
    notes_count = (await session.execute(select(func.count(NoteMetadata.id)))).scalar() or 0
    embeddings_count = (await session.execute(select(func.count(NoteEmbedding.id)))).scalar() or 0
    notes_with_emb = (await session.execute(
        select(func.count(func.distinct(NoteEmbedding.note_id)))
    )).scalar() or 0

    # Test DB connection
    db_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    provider = settings.embedding_provider
    provider_card = {
        "name": provider,
        "dimensions": settings.embedding_dimensions,
    }
    if provider == "openai":
        provider_card["model"] = settings.openai_embedding_model
        provider_card["base_url"] = settings.openai_base_url
        provider_card["masked_key"] = _mask_openai_key(settings.openai_api_key)
    else:
        provider_card["model"] = settings.embedding_model
        provider_card["ollama_url"] = settings.ollama_url

    # Test the active provider's reachability
    provider_ok = False
    try:
        if provider == "ollama":
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{settings.ollama_url}/api/tags")
                provider_ok = r.status_code == 200
        else:
            # For OpenAI we don't make a request on every page load — the
            # presence of a non-empty key is the practical signal here.
            provider_ok = bool((settings.openai_api_key or "").strip())
    except Exception:
        pass

    return templates.TemplateResponse(request, "settings.html", {
        "active": "settings",
        "stats": {
            "notes_indexed": notes_count,
            "embeddings": embeddings_count,
            "notes_with_embeddings": notes_with_emb,
        },
        "index_interval": settings.index_interval_seconds,
        "db_ok": db_ok,
        "provider_ok": provider_ok,
        "provider": provider_card,
        "vault_path": settings.vault_path,
    })


# Keep strong references to background tasks to prevent GC
_background_tasks: set = set()


def _spawn(coro):
    import asyncio
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@router.post("/settings/reindex")
async def trigger_reindex(request: Request):
    _spawn(_reindex_background())
    # Dashboard's inline button POSTs with Accept: application/json for no-reload feedback.
    # Settings page uses a plain form, which expects a redirect.
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"status": "started"})
    return RedirectResponse("/admin/settings", status_code=303)


@router.get("/settings/reembed", response_class=HTMLResponse)
async def reembed_confirm_page(request: Request):
    """Generate a one-time signed token and render a confirmation page."""
    token = _reembed_serializer().dumps(secrets.token_hex(16))
    return templates.TemplateResponse(request, "reembed_confirm.html", {
        "active": "settings",
        "token": token,
    })


@router.post("/settings/reembed")
async def trigger_reembed(
    token: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    """Clear all embeddings and re-embed from scratch. Requires a valid signed token."""
    try:
        _reembed_serializer().loads(token, max_age=60)
    except (BadSignature, SignatureExpired):
        raise HTTPException(status_code=400, detail="Invalid or expired confirmation token")

    from src.models.db import NoteEmbedding
    from sqlalchemy import delete
    await session.execute(delete(NoteEmbedding))
    await session.commit()
    _spawn(_reindex_background())
    return RedirectResponse("/admin/settings", status_code=303)


# Indexer pause flag, also surfaced via the reset progress endpoint.
indexer_paused: bool = False


@router.post("/settings/reset-embeddings")
async def reset_embeddings(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Recreate `note_embeddings.embedding` at the configured dim and null
    every `embedded_content_hash` so the indexer re-embeds the vault on
    the next pass. Pauses the indexer during the SQL.

    Returns a JSON status object the dashboard can poll.
    """
    global indexer_paused
    from sqlalchemy import delete
    from src.models.db import NoteEmbedding, NoteMetadata

    indexer_paused = True
    try:
        dim = int(settings.embedding_dimensions)
        # ALTER COLUMN TYPE on a vector column with a dependent HNSW index
        # is unsafe across pgvector versions — drop and recreate explicitly.
        await session.execute(
            text("DROP INDEX IF EXISTS ix_note_embeddings_embedding_hnsw")
        )
        await session.execute(delete(NoteEmbedding))
        await session.execute(
            text(f"ALTER TABLE note_embeddings ALTER COLUMN embedding TYPE vector({dim})")
        )
        await session.execute(
            text("UPDATE notes_metadata SET embedded_content_hash = NULL")
        )
        await session.execute(
            text(
                "CREATE INDEX ix_note_embeddings_embedding_hnsw "
                "ON note_embeddings USING hnsw (embedding vector_cosine_ops) "
                "WITH (m = 16, ef_construction = 64)"
            )
        )
        await session.commit()
    finally:
        indexer_paused = False

    # Kick off an immediate reindex pass; the periodic loop will continue
    # afterwards.
    _spawn(_reindex_background())

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"status": "reset", "dimensions": dim})
    return RedirectResponse("/admin/settings", status_code=303)


@router.get("/settings/reset-embeddings/progress")
async def reset_progress(session: AsyncSession = Depends(get_session)):
    """Return re-embedding progress (notes still pending) for dashboard polling."""
    total = (await session.execute(select(func.count(NoteMetadata.id)))).scalar() or 0
    embedded = (await session.execute(
        text(
            "SELECT count(*) FROM notes_metadata "
            "WHERE embedded_content_hash IS NOT NULL "
            "AND embedded_content_hash = content_hash"
        )
    )).scalar() or 0
    pending = max(0, total - embedded)
    return JSONResponse({
        "paused": indexer_paused,
        "total": int(total),
        "embedded": int(embedded),
        "pending": int(pending),
    })


async def _reindex_background():
    from src.services.indexer import index_vault, embed_vault
    await index_vault()
    await embed_vault()
