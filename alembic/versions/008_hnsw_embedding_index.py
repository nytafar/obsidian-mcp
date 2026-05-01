"""Add HNSW index on note_embeddings.embedding for cosine distance

Revision ID: 008
Revises: 007
Create Date: 2026-05-01

Replaces sequential scan on the `<=>` operator with a logarithmic-time
index. Requires pgvector extension >= 0.5.0 (HNSW added 2023-08-28).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    ver = conn.execute(
        sa.text("SELECT extversion FROM pg_extension WHERE extname='vector'")
    ).scalar()
    if ver is None:
        raise RuntimeError("pgvector extension not installed")
    parts = tuple(int(p) for p in ver.split(".")[:2])
    if parts < (0, 5):
        raise RuntimeError(f"pgvector {ver} lacks HNSW; need >= 0.5.0")
    op.execute("SET LOCAL maintenance_work_mem = '512MB'")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_note_embeddings_embedding_hnsw "
        "ON note_embeddings USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_note_embeddings_embedding_hnsw")
