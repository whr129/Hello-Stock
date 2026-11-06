"""Durable market alert decisions, delivery, and sentiment.

Revision ID: 0007_market_alerts
Revises: 0006_market_research_only_cleanup
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_market_alerts"
down_revision: str | None = "0006_market_research_only_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "summaries", sa.Column("article_revision", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "market_mentions",
        sa.Column("article_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.drop_constraint("uq_market_mentions_article_signal", "market_mentions", type_="unique")
    op.create_unique_constraint(
        "uq_market_mentions_article_signal",
        "market_mentions",
        ["article_id", "article_revision", "ticker", "theme"],
    )
    op.add_column(
        "articles", sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column("articles", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE articles SET updated_at = created_at")
    op.alter_column("articles", "updated_at", nullable=False, server_default=sa.func.now())
    op.add_column(
        "articles",
        sa.Column(
            "provider_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_table(
        "market_alert_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_key", sa.String(255), nullable=False),
        sa.Column("scope", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("tickers", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("aliases", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("revision_key", sa.String(128), nullable=False),
        sa.Column("revision_history", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("event_key"),
    )
    op.create_table(
        "market_alert_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id", sa.Integer(), sa.ForeignKey("market_alert_events.id"), nullable=False
        ),
        sa.Column("event_revision", sa.Integer(), nullable=False),
        sa.Column("input_digest", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("model_revision", sa.String(255), nullable=False),
        sa.Column("input_payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("result", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("delivery_choice", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("budget_month", sa.String(7), nullable=True),
        sa.Column("reserved_usd", sa.Numeric(20, 12), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(20, 12), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "event_id", "input_digest", "kind", "model_revision", name="uq_alert_decision_input"
        ),
        sa.CheckConstraint("reserved_usd >= 0 AND cost_usd >= 0", name="ck_alert_decision_cost"),
        sa.CheckConstraint(
            "status IN ('pending','assessing','completed','failed','blocked')",
            name="ck_alert_decision_status",
        ),
    )
    op.create_index("ix_market_alert_decisions_event_id", "market_alert_decisions", ["event_id"])
    op.create_index("ix_market_alert_decisions_status", "market_alert_decisions", ["status"])
    op.create_table(
        "market_alert_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id", sa.Integer(), sa.ForeignKey("market_alert_events.id"), nullable=False
        ),
        sa.Column(
            "decision_id", sa.Integer(), sa.ForeignKey("market_alert_decisions.id"), nullable=False
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("destination", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("chunks", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("chunks_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "decision_id", "revision", "kind", "destination", name="uq_alert_delivery_decision"
        ),
        sa.UniqueConstraint(
            "event_id", "revision", "kind", "destination", name="uq_alert_delivery_event"
        ),
        sa.CheckConstraint(
            "status IN ('pending','sending','delivered','failed','unknown','expired')",
            name="ck_alert_delivery_status",
        ),
    )
    op.create_index("ix_market_alert_deliveries_event_id", "market_alert_deliveries", ["event_id"])
    op.create_index("ix_market_alert_deliveries_status", "market_alert_deliveries", ["status"])
    op.create_table(
        "market_alert_budgets",
        sa.Column("month", sa.String(7), primary_key=True),
        sa.Column("spent_usd", sa.Numeric(20, 12), nullable=False, server_default="0"),
        sa.Column("reserved_usd", sa.Numeric(20, 12), nullable=False, server_default="0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("spent_usd >= 0 AND reserved_usd >= 0", name="ck_alert_budget_amounts"),
    )
    op.create_table(
        "market_sentiment_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model_revision", sa.String(255), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("watermark", sa.String(128), nullable=False),
        sa.Column("windows", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("state", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gaps", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.UniqueConstraint(
            "ticker", "provider", "model_revision", "as_of", name="uq_sentiment_snapshot_window"
        ),
    )
    op.create_index(
        "ix_market_sentiment_snapshots_ticker", "market_sentiment_snapshots", ["ticker"]
    )
    op.create_index("ix_market_sentiment_snapshots_as_of", "market_sentiment_snapshots", ["as_of"])


def downgrade() -> None:
    op.drop_constraint("uq_market_mentions_article_signal", "market_mentions", type_="unique")
    op.create_unique_constraint(
        "uq_market_mentions_article_signal", "market_mentions", ["article_id", "ticker", "theme"]
    )
    op.drop_column("market_mentions", "article_revision")
    op.drop_column("summaries", "article_revision")
    op.drop_column("articles", "updated_at")
    op.drop_column("articles", "revision")
    op.drop_table("market_sentiment_snapshots")
    op.drop_table("market_alert_budgets")
    op.drop_table("market_alert_deliveries")
    op.drop_table("market_alert_decisions")
    op.drop_table("market_alert_events")
    op.drop_column("articles", "provider_metadata")
