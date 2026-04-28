"""Add usage_logs.oauth_token_id for OAuth attribution

Revision ID: 007
Revises: 006
Create Date: 2026-04-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "usage_logs",
        sa.Column("oauth_token_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_usage_logs_oauth_token_id",
        "usage_logs",
        "oauth_tokens",
        ["oauth_token_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_usage_logs_oauth_token_id",
        "usage_logs",
        ["oauth_token_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_usage_logs_oauth_token_id", table_name="usage_logs")
    op.drop_constraint("fk_usage_logs_oauth_token_id", "usage_logs", type_="foreignkey")
    op.drop_column("usage_logs", "oauth_token_id")
