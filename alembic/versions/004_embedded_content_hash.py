"""Add embedded_content_hash to notes_metadata for stale embedding detection

Revision ID: 004
Revises: 003
Create Date: 2026-03-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notes_metadata",
        sa.Column("embedded_content_hash", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notes_metadata", "embedded_content_hash")
