"""market research only cleanup

Revision ID: 0006_market_research_only_cleanup
Revises: 0005_market_research_signals
Create Date: 2026-05-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_market_research_only_cleanup"
down_revision: str | None = "0005_market_research_signals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_watched_tickers_symbol", table_name="watched_tickers")
    op.drop_table("watched_tickers")
    op.drop_table("preferences")
    op.drop_column("users", "local_region")
    op.drop_column("users", "timezone")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default="America/Toronto",
        ),
    )
    op.add_column(
        "users",
        sa.Column("local_region", sa.String(length=128), nullable=False, server_default="Waterloo"),
    )
    op.alter_column("users", "timezone", server_default=None)
    op.alter_column("users", "local_region", server_default=None)

    op.create_table(
        "preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), unique=True, nullable=False),
        sa.Column("topics", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column(
            "blocked_keywords",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "preferred_categories",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{world,local,markets}",
        ),
        sa.Column("digest_style", sa.String(length=64), nullable=False, server_default="concise"),
        sa.Column("delivery_time", sa.String(length=16), nullable=True),
        sa.Column("last_daily_recap_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "risk_context",
            sa.String(length=64),
            nullable=False,
            server_default="informational",
        ),
    )
    op.create_table(
        "watched_tickers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "symbol", name="uq_user_ticker"),
    )
    op.create_index("ix_watched_tickers_symbol", "watched_tickers", ["symbol"])
