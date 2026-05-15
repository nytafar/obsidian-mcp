from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    connect_args={
        # 60s — embedding INSERTs into a vector(1024) column with an HNSW
        # index can take a few seconds each on a large vault. 10s (the old
        # value) caused QueryCanceledError on occasional notes and may have
        # left the indexer's session in a stuck state.
        "server_settings": {"statement_timeout": "60000"}
    },
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
