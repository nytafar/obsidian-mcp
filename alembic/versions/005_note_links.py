"""Add note_links table for wikilink/markdown-link graph

Revision ID: 005
Revises: 004
Create Date: 2026-04-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "note_links",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "source_note_id",
            sa.Integer,
            sa.ForeignKey("notes_metadata.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_note_id",
            sa.Integer,
            sa.ForeignKey("notes_metadata.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("target_path", sa.String(1024), nullable=False),
        sa.Column("link_text", sa.Text, nullable=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default="link"),
        sa.Column("position", sa.Integer, nullable=True),
    )
    op.create_index("ix_note_links_source", "note_links", ["source_note_id"])
    op.create_index("ix_note_links_target", "note_links", ["target_note_id"])
    op.create_index("ix_note_links_target_path", "note_links", ["target_path"])


def downgrade() -> None:
    op.drop_index("ix_note_links_target_path", table_name="note_links")
    op.drop_index("ix_note_links_target", table_name="note_links")
    op.drop_index("ix_note_links_source", table_name="note_links")
    op.drop_table("note_links")
