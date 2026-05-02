"""memory pipeline

Revision ID: 0004_memory_pipeline
Revises: 0003_runtime_observability
Create Date: 2026-05-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_memory_pipeline"
down_revision: str | None = "0003_runtime_observability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("memory_cursor_event_id", sa.Integer(), nullable=False, server_default="0"))

    op.create_table(
        "conversation_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_events_user_id", "conversation_events", ["user_id"], unique=False)
    op.create_index("ix_conversation_events_chat_id", "conversation_events", ["chat_id"], unique=False)
    op.create_index("ix_conversation_events_role", "conversation_events", ["role"], unique=False)

    op.create_table(
        "memory_consolidation_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_start_event_id", sa.Integer(), nullable=False),
        sa.Column("source_end_event_id", sa.Integer(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memory_consolidation_jobs_user_id",
        "memory_consolidation_jobs",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_memory_consolidation_jobs_status",
        "memory_consolidation_jobs",
        ["status"],
        unique=False,
    )

    op.add_column(
        "long_term_memories",
        sa.Column("category", sa.String(length=64), nullable=False, server_default="general"),
    )
    op.add_column(
        "long_term_memories",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
    )
    op.add_column("long_term_memories", sa.Column("source_job_id", sa.Integer(), nullable=True))
    op.add_column("long_term_memories", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_long_term_memories_status", "long_term_memories", ["status"], unique=False)
    op.create_foreign_key(
        "fk_long_term_memories_source_job_id",
        "long_term_memories",
        "memory_consolidation_jobs",
        ["source_job_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_long_term_memories_source_job_id", "long_term_memories", type_="foreignkey")
    op.drop_index("ix_long_term_memories_status", table_name="long_term_memories")
    op.drop_column("long_term_memories", "last_seen_at")
    op.drop_column("long_term_memories", "source_job_id")
    op.drop_column("long_term_memories", "status")
    op.drop_column("long_term_memories", "category")
    op.drop_index("ix_memory_consolidation_jobs_status", table_name="memory_consolidation_jobs")
    op.drop_index("ix_memory_consolidation_jobs_user_id", table_name="memory_consolidation_jobs")
    op.drop_table("memory_consolidation_jobs")
    op.drop_index("ix_conversation_events_role", table_name="conversation_events")
    op.drop_index("ix_conversation_events_chat_id", table_name="conversation_events")
    op.drop_index("ix_conversation_events_user_id", table_name="conversation_events")
    op.drop_table("conversation_events")
    op.drop_column("users", "memory_cursor_event_id")
