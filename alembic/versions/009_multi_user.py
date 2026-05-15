"""Multi-user foundations: users table + nullable user_id FKs

Revision ID: 009
Revises: 008
Create Date: 2026-05-15

Phase 1 of multi-user mode. Adds the users table and a nullable
user_id FK on every per-tenant table. No data migration here; backfill
happens at first bootstrap registration. Single-user mode keeps
user_id NULL everywhere, which every read/write code path treats as
"no filter".
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(255), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("vault_path", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column("api_keys", sa.Column("user_id", sa.Integer, nullable=True))
    op.create_foreign_key(
        "fk_api_keys_user_id",
        "api_keys",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])

    op.add_column("oauth_clients", sa.Column("user_id", sa.Integer, nullable=True))
    op.create_foreign_key(
        "fk_oauth_clients_user_id",
        "oauth_clients",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_oauth_clients_user_id", "oauth_clients", ["user_id"])

    op.add_column("oauth_tokens", sa.Column("user_id", sa.Integer, nullable=True))
    op.create_foreign_key(
        "fk_oauth_tokens_user_id",
        "oauth_tokens",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_oauth_tokens_user_id", "oauth_tokens", ["user_id"])

    op.add_column("oauth_codes", sa.Column("user_id", sa.Integer, nullable=True))
    op.create_foreign_key(
        "fk_oauth_codes_user_id",
        "oauth_codes",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_oauth_codes_user_id", "oauth_codes", ["user_id"])

    op.add_column("notes_metadata", sa.Column("user_id", sa.Integer, nullable=True))
    op.create_foreign_key(
        "fk_notes_metadata_user_id",
        "notes_metadata",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_notes_metadata_user_id", "notes_metadata", ["user_id"])

    # Replace the implicit single-column UNIQUE on file_path (auto-named
    # notes_metadata_file_path_key by PG) with a composite (user_id, file_path)
    # unique constraint. We declare it NULLS NOT DISTINCT (PG 15+ feature) so
    # single-user-mode rows — where every user_id is NULL — collide on
    # file_path alone. Without this, the indexer's ON CONFLICT (user_id,
    # file_path) DO UPDATE would silently insert duplicate rows on every
    # pass, because PG's default is NULLS DISTINCT (each NULL is unique).
    op.drop_constraint("notes_metadata_file_path_key", "notes_metadata", type_="unique")
    op.create_unique_constraint(
        "uq_notes_metadata_user_id_file_path",
        "notes_metadata",
        ["user_id", "file_path"],
        postgresql_nulls_not_distinct=True,
    )

    op.add_column("usage_logs", sa.Column("user_id", sa.Integer, nullable=True))
    op.create_foreign_key(
        "fk_usage_logs_user_id",
        "usage_logs",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_usage_logs_user_id", "usage_logs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_usage_logs_user_id", table_name="usage_logs")
    op.drop_constraint("fk_usage_logs_user_id", "usage_logs", type_="foreignkey")
    op.drop_column("usage_logs", "user_id")

    op.drop_constraint("uq_notes_metadata_user_id_file_path", "notes_metadata", type_="unique")
    op.create_unique_constraint(
        "notes_metadata_file_path_key",
        "notes_metadata",
        ["file_path"],
    )

    op.drop_index("ix_notes_metadata_user_id", table_name="notes_metadata")
    op.drop_constraint("fk_notes_metadata_user_id", "notes_metadata", type_="foreignkey")
    op.drop_column("notes_metadata", "user_id")

    op.drop_index("ix_oauth_codes_user_id", table_name="oauth_codes")
    op.drop_constraint("fk_oauth_codes_user_id", "oauth_codes", type_="foreignkey")
    op.drop_column("oauth_codes", "user_id")

    op.drop_index("ix_oauth_tokens_user_id", table_name="oauth_tokens")
    op.drop_constraint("fk_oauth_tokens_user_id", "oauth_tokens", type_="foreignkey")
    op.drop_column("oauth_tokens", "user_id")

    op.drop_index("ix_oauth_clients_user_id", table_name="oauth_clients")
    op.drop_constraint("fk_oauth_clients_user_id", "oauth_clients", type_="foreignkey")
    op.drop_column("oauth_clients", "user_id")

    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_constraint("fk_api_keys_user_id", "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "user_id")

    op.drop_table("users")
