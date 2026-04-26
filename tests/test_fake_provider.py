"""Verifies FakeProvider behavior and that get_provider() can be overridden."""
import pytest

from src.config import settings
from src.services import embeddings
from tests.fakes import FakeProvider


@pytest.mark.asyncio
async def test_fake_provider_dim_matches_settings():
    fake = FakeProvider()
    v = await fake.embed_one("hello")
    assert len(v) == settings.embedding_dimensions


@pytest.mark.asyncio
async def test_fake_provider_deterministic():
    fake = FakeProvider()
    a = await fake.embed_one("same")
    b = await fake.embed_one("same")
    assert a == b


@pytest.mark.asyncio
async def test_fake_provider_distinct_inputs_distinct_outputs():
    fake = FakeProvider()
    a = await fake.embed_one("alpha")
    b = await fake.embed_one("beta")
    assert a != b


@pytest.mark.asyncio
async def test_get_provider_override(monkeypatch):
    monkeypatch.setattr(embeddings, "get_provider", lambda: FakeProvider())
    out = await embeddings.get_embedding("test")
    assert len(out) == settings.embedding_dimensions
