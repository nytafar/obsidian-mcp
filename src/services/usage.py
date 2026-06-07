import logging
from contextvars import ContextVar

logger = logging.getLogger(__name__)

current_correlation_id: ContextVar[str | None] = ContextVar(
    "current_correlation_id", default=None
)


async def log_usage(
    tool: str,
    params: dict | None,
    duration_ms: int | None,
    response_size: int | None,
    *,
    user_id: int | None = None,
) -> None:
    try:
        from src.auth.session import current_user_id
        from src.database import async_session
        from src.mcp_server.auth import current_api_key_id, current_oauth_token_id
        from src.models.db import UsageLog

        usage_params = dict(params or {})
        correlation_id = current_correlation_id.get()
        if correlation_id and "correlation_id" not in usage_params:
            usage_params["correlation_id"] = correlation_id
        if user_id is None:
            user_id = current_user_id.get()

        async with async_session() as session:
            session.add(UsageLog(
                key_id=current_api_key_id.get(),
                oauth_token_id=current_oauth_token_id.get(),
                user_id=user_id,
                tool=tool,
                params=usage_params,
                duration_ms=duration_ms,
                response_size=response_size,
            ))
            await session.commit()
    except Exception as e:
        logger.warning("Failed to log usage for %s: %s", tool, e)
