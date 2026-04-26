from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://obsidian_mcp:changeme@postgres:5432/obsidian_mcp"
    ollama_url: str = "http://ollama:11434"
    vault_path: str = "/obsidian"
    secret_key: str = "changeme"
    index_interval_seconds: int = 300
    embedding_model: str = "bge-m3"
    embedding_dimensions: int = 1024
    chunk_size: int = 1500
    chunk_overlap: int = 50
    base_url: str = "http://localhost:8000"
    allowed_origins: list[str] = ["http://localhost:8000"]
    allowed_hosts: list[str] = ["localhost"]

    embedding_provider: Literal["ollama", "openai"] = "ollama"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_embedding_model: str = "text-embedding-3-small"

    model_config = {"env_file": ".env"}

    @model_validator(mode="after")
    def _validate_provider_credentials(self) -> "Settings":
        if self.embedding_provider == "openai" and not (self.openai_api_key or "").strip():
            raise ValueError(
                "OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai"
            )
        return self


settings = Settings()
