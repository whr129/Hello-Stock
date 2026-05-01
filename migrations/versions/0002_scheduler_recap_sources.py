"""scheduler recap and provider sources

Revision ID: 0002_scheduler_recap_sources
Revises: 0001_initial
Create Date: 2026-05-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_scheduler_recap_sources"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "preferences",
        sa.Column("last_daily_recap_sent_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column("sources", sa.Column("provider", sa.String(length=32), nullable=True))
    op.add_column("sources", sa.Column("external_account", sa.Text(), nullable=True))
    op.add_column(
        "sources",
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "field_mapping",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("sources", sa.Column("fetch_mode", sa.String(length=32), nullable=True))
    op.add_column(
        "sources",
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("sources", sa.Column("last_error", sa.Text(), nullable=True))
    op.create_index("ix_sources_provider", "sources", ["provider"], unique=False)

    op.execute("UPDATE sources SET provider = 'rss' WHERE provider IS NULL")
    op.execute(
        "UPDATE sources SET external_account = url WHERE external_account IS NULL OR external_account = ''"
    )
    op.execute("UPDATE sources SET config = jsonb_build_object('feed_url', url) WHERE config = '{}'::jsonb")
    op.execute("UPDATE sources SET fetch_mode = 'rss' WHERE fetch_mode IS NULL")

    op.alter_column("sources", "provider", nullable=False)
    op.alter_column("sources", "external_account", nullable=False)


def downgrade() -> None:
    op.drop_index("ix_sources_provider", table_name="sources")
    op.drop_column("sources", "last_error")
    op.drop_column("sources", "last_success_at")
    op.drop_column("sources", "last_fetched_at")
    op.drop_column("sources", "fetch_mode")
    op.drop_column("sources", "field_mapping")
    op.drop_column("sources", "config")
    op.drop_column("sources", "external_account")
    op.drop_column("sources", "provider")
    op.drop_column("preferences", "last_daily_recap_sent_at")
