"""Parameterize note_embeddings.embedding column to settings.embedding_dimensions

Revision ID: 006
Revises: 005
Create Date: 2026-04-26

For existing 1024-dim deployments this is a no-op (ALTER to the same width).
Operators changing dim must run the separate `make reset-embeddings` workflow.
"""
from typing import Sequence, Union

from alembic import op

from src.config import settings

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    dim = int(settings.embedding_dimensions)
    op.execute(
        f"ALTER TABLE note_embeddings "
        f"ALTER COLUMN embedding TYPE vector({dim})"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE note_embeddings "
        "ALTER COLUMN embedding TYPE vector(1024)"
    )
