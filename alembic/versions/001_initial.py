"""Initial schema: api_keys, usage_logs, notes_metadata, note_embeddings

Revision ID: 001
Revises:
Create Date: 2026-03-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from pgvector.sqlalchemy import Vector

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # api_keys
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("key_prefix", sa.String(12), nullable=False),
        sa.Column("permission", sa.String(20), nullable=False, server_default="read"),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )

    # usage_logs
    op.create_table(
        "usage_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("key_id", sa.Integer, sa.ForeignKey("api_keys.id"), nullable=False),
        sa.Column("tool", sa.String(100), nullable=False),
        sa.Column("params", JSONB, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("response_size", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_usage_logs_created_at", "usage_logs", ["created_at"])

    # notes_metadata
    op.create_table(
        "notes_metadata",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("file_path", sa.String(1024), unique=True, nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("tags", ARRAY(sa.String), nullable=True),
        sa.Column("frontmatter", JSONB, nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content_tsvector", TSVECTOR, nullable=True),
        sa.Column("file_size", sa.Integer, nullable=True),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_notes_metadata_tsvector", "notes_metadata", ["content_tsvector"], postgresql_using="gin"
    )
    op.create_index(
        "ix_notes_metadata_tags", "notes_metadata", ["tags"], postgresql_using="gin"
    )

    # note_embeddings
    op.create_table(
        "note_embeddings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "note_id",
            sa.Integer,
            sa.ForeignKey("notes_metadata.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("chunk_text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
    )
    op.create_index("ix_note_embeddings_note_id", "note_embeddings", ["note_id"])


def downgrade() -> None:
    op.drop_table("note_embeddings")
    op.drop_table("notes_metadata")
    op.drop_table("usage_logs")
    op.drop_table("api_keys")
    op.execute("DROP EXTENSION IF EXISTS vector")
