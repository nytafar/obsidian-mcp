"""Pytest configuration.

Forces an in-process default config so importing `src.config` succeeds even
when `.env` is missing on the test host. Individual tests can monkeypatch
specific settings as needed.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Provide minimal env defaults so `Settings()` instantiation doesn't pick up
# stray env from the host. Tests that need a particular value set it via
# monkeypatch + a `Settings` reload helper.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("VAULT_PATH", "/tmp/test-vault")


@pytest.fixture(autouse=True)
def _reset_provider_cache():
    """Clear the cached provider singleton between tests so changes to
    `settings.embedding_provider` propagate."""
    from src.services import embeddings

    embeddings.get_provider.cache_clear()
    yield
    embeddings.get_provider.cache_clear()
