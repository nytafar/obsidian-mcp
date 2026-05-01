"""Drop and recreate `note_embeddings.embedding` at the configured dim and
clear every `embedded_content_hash` so the indexer re-embeds the entire
vault on its next pass.

Invoked by `make reset-embeddings`. Reads `EMBEDDING_DIMENSIONS` from
settings.
"""
import asyncio
import sys

from sqlalchemy import text

from src.config import settings
from src.database import async_session, engine


async def reset() -> None:
    dim = int(settings.embedding_dimensions)
    print(f"Resetting embeddings to vector({dim})...")
    async with async_session() as session:
        # The app's per-connection 10s statement_timeout is too tight for
        # the CREATE INDEX step. Lift it for this transaction.
        await session.execute(text("SET LOCAL statement_timeout = '5min'"))
        # ALTER COLUMN TYPE on a vector column with a dependent HNSW index
        # is unsafe across pgvector versions — drop and recreate explicitly.
        await session.execute(
            text("DROP INDEX IF EXISTS ix_note_embeddings_embedding_hnsw")
        )
        await session.execute(text("DELETE FROM note_embeddings"))
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
    await engine.dispose()
    print(
        f"Done. Column is vector({dim}); all notes flagged for re-embedding "
        "on the next indexer pass."
    )


if __name__ == "__main__":
    try:
        asyncio.run(reset())
    except Exception as e:
        print(f"reset_embeddings failed: {e}", file=sys.stderr)
        sys.exit(1)
