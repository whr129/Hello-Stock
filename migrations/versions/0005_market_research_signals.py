"""market research signals

Revision ID: 0005_market_research_signals
Revises: 0004_memory_pipeline
Create Date: 2026-05-16
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_market_research_signals"
down_revision: str | None = "0004_memory_pipeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_entities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=True),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("sector", sa.String(length=128), nullable=True),
        sa.Column("industry", sa.String(length=128), nullable=True),
        sa.Column("aliases", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("exchange", sa.String(length=32), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", name="uq_market_entities_ticker"),
    )
    op.create_index("ix_market_entities_ticker", "market_entities", ["ticker"], unique=False)
    op.create_index("ix_market_entities_sector", "market_entities", ["sector"], unique=False)
    op.create_index("ix_market_entities_active", "market_entities", ["active"], unique=False)

    op.create_table(
        "market_mentions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("ticker", sa.String(length=16), nullable=True),
        sa.Column("theme", sa.String(length=128), nullable=True),
        sa.Column("source_family", sa.String(length=64), nullable=False, server_default="news"),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("summary_id", sa.Integer(), nullable=True),
        sa.Column("mention_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sentiment", sa.Float(), nullable=True),
        sa.Column("novelty", sa.Float(), nullable=True),
        sa.Column("trust_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"]),
        sa.ForeignKeyConstraint(["entity_id"], ["market_entities.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.ForeignKeyConstraint(["summary_id"], ["summaries.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("article_id", "ticker", "theme", name="uq_market_mentions_article_signal"),
    )
    op.create_index("ix_market_mentions_ticker", "market_mentions", ["ticker"], unique=False)
    op.create_index("ix_market_mentions_ticker_theme", "market_mentions", ["ticker", "theme"], unique=False)
    op.create_index("ix_market_mentions_created_at", "market_mentions", ["created_at"], unique=False)
    op.create_index("ix_market_mentions_source_family", "market_mentions", ["source_family"], unique=False)

    op.create_table(
        "market_signal_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=True),
        sa.Column("theme", sa.String(length=128), nullable=True),
        sa.Column("window", sa.String(length=16), nullable=False),
        sa.Column("mention_velocity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("source_diversity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("recency_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("semantic_similarity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("price_momentum", sa.Float(), nullable=False, server_default="0"),
        sa.Column("volume_signal", sa.Float(), nullable=False, server_default="0"),
        sa.Column("theme_persistence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("trust_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "component_scores",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_market_signal_snapshots_ticker", "market_signal_snapshots", ["ticker"], unique=False)
    op.create_index("ix_market_signal_snapshots_window", "market_signal_snapshots", ["window"], unique=False)
    op.create_index("ix_market_signal_snapshots_total_score", "market_signal_snapshots", ["total_score"], unique=False)
    op.create_index("ix_market_signal_snapshots_created_at", "market_signal_snapshots", ["created_at"], unique=False)
    op.create_index(
        "ix_market_signal_snapshots_ticker_theme_created",
        "market_signal_snapshots",
        ["ticker", "theme", "created_at"],
        unique=False,
    )

    op.create_table(
        "market_theme_memories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("theme", sa.String(length=128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("related_tickers", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("related_sectors", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_market_theme_memories_theme", "market_theme_memories", ["theme"], unique=False)
    op.create_index("ix_market_theme_memories_last_seen_at", "market_theme_memories", ["last_seen_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_market_theme_memories_last_seen_at", table_name="market_theme_memories")
    op.drop_index("ix_market_theme_memories_theme", table_name="market_theme_memories")
    op.drop_table("market_theme_memories")
    op.drop_index("ix_market_signal_snapshots_ticker_theme_created", table_name="market_signal_snapshots")
    op.drop_index("ix_market_signal_snapshots_created_at", table_name="market_signal_snapshots")
    op.drop_index("ix_market_signal_snapshots_total_score", table_name="market_signal_snapshots")
    op.drop_index("ix_market_signal_snapshots_window", table_name="market_signal_snapshots")
    op.drop_index("ix_market_signal_snapshots_ticker", table_name="market_signal_snapshots")
    op.drop_table("market_signal_snapshots")
    op.drop_index("ix_market_mentions_source_family", table_name="market_mentions")
    op.drop_index("ix_market_mentions_created_at", table_name="market_mentions")
    op.drop_index("ix_market_mentions_ticker_theme", table_name="market_mentions")
    op.drop_index("ix_market_mentions_ticker", table_name="market_mentions")
    op.drop_table("market_mentions")
    op.drop_index("ix_market_entities_active", table_name="market_entities")
    op.drop_index("ix_market_entities_sector", table_name="market_entities")
    op.drop_index("ix_market_entities_ticker", table_name="market_entities")
    op.drop_table("market_entities")
