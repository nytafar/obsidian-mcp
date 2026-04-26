import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from starlette.types import ASGIApp, Receive, Scope, Send
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src.api.routes import router as api_router
from src.config import settings
from src.control_panel.routes import router as panel_router
from src.database import async_session
from src.limiter import limiter
from src.mcp_server.auth import APIKeyMiddleware
from src.mcp_server.server import mcp
from src.oauth.routes import router as oauth_router
from src.services.indexer import run_indexer_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Initialize the MCP app (creates session manager lazily)
_mcp_starlette = mcp.streamable_http_app()


async def _check_embedding_dim() -> None:
    """Compare the live `note_embeddings.embedding` column dim against
    `settings.embedding_dimensions`. Exit non-zero on mismatch.
    """
    configured = int(settings.embedding_dimensions)
    async with async_session() as session:
        result = await session.execute(
            text(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = 'note_embeddings'::regclass "
                "AND attname = 'embedding'"
            )
        )
        row = result.first()
    if row is None:
        # Table not yet migrated; let alembic handle it on first run.
        return
    column_dim = int(row[0])
    if column_dim != configured:
        logging.getLogger(__name__).critical(
            "Embedding dim mismatch: configured=%d, column=%d. "
            "Run `make reset-embeddings` to recreate the column at the "
            "configured dimension.",
            configured,
            column_dim,
        )
        sys.exit(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _check_embedding_dim()
    indexer_task = asyncio.create_task(run_indexer_loop())
    async with mcp.session_manager.run():
        yield
    indexer_task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(indexer_task), timeout=10.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass


app = FastAPI(title="Obsidian MCP", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Honor X-Forwarded-Proto/For from upstream reverse proxy so that scheme-aware
# redirects (e.g. trailing-slash on /mcp) keep the https:// scheme.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# GZip compression for responses >= 1000 bytes
app.add_middleware(GZipMiddleware, minimum_size=1000)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


# OAuth routes (public endpoints)
app.include_router(oauth_router)

# API routes (protected by Traefik OAuth)
app.include_router(api_router)

# Control panel routes at /admin (protected by Traefik OAuth)
app.include_router(panel_router)


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


# MCP handler used by both /mcp mount and root proxy
async def mcp_handler(scope, receive, send):
    await mcp.session_manager.handle_request(scope, receive, send)


# Mount MCP at /mcp with API key auth
app.mount("/mcp", APIKeyMiddleware(mcp_handler))


class RootMCPProxyMiddleware:
    """Intercept POST/GET/DELETE to / with Bearer token and route to MCP.

    Some MCP clients (e.g. OpenWebUI) strip the path and send requests to root.
    Without this, those requests hit the OAuth-protected panel router and fail.
    Supports both omcp_ API keys and OAuth Bearer tokens.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and scope["path"] in ("/", ""):
            headers = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode()
            if auth.startswith("Bearer "):
                await APIKeyMiddleware(mcp_handler)(scope, receive, send)
                return
        await self.app(scope, receive, send)


app.add_middleware(RootMCPProxyMiddleware)
