"""Make usage_logs.key_id nullable for OAuth token usage

Revision ID: 003
Revises: 002
Create Date: 2026-03-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("usage_logs", "key_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM usage_logs WHERE key_id IS NULL")
    op.alter_column("usage_logs", "key_id", existing_type=sa.Integer(), nullable=False)
