"""runtime observability

Revision ID: 0003_runtime_observability
Revises: 0002_scheduler_recap_sources
Create Date: 2026-05-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_runtime_observability"
down_revision: str | None = "0002_scheduler_recap_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow", sa.String(length=64), nullable=False),
        sa.Column("trigger", sa.String(length=64), nullable=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runtime_runs_workflow", "runtime_runs", ["workflow"], unique=False)
    op.create_index("ix_runtime_runs_telegram_user_id", "runtime_runs", ["telegram_user_id"], unique=False)
    op.create_index("ix_runtime_runs_chat_id", "runtime_runs", ["chat_id"], unique=False)
    op.create_index("ix_runtime_runs_status", "runtime_runs", ["status"], unique=False)

    op.create_table(
        "runtime_steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("parent_step_id", sa.Integer(), nullable=True),
        sa.Column("workflow", sa.String(length=64), nullable=False),
        sa.Column("step_name", sa.String(length=128), nullable=False),
        sa.Column("step_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["parent_step_id"], ["runtime_steps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runtime_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runtime_steps_run_id", "runtime_steps", ["run_id"], unique=False)
    op.create_index("ix_runtime_steps_workflow", "runtime_steps", ["workflow"], unique=False)
    op.create_index("ix_runtime_steps_step_name", "runtime_steps", ["step_name"], unique=False)
    op.create_index("ix_runtime_steps_step_type", "runtime_steps", ["step_type"], unique=False)
    op.create_index("ix_runtime_steps_status", "runtime_steps", ["status"], unique=False)

    op.create_table(
        "runtime_errors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("step_id", sa.Integer(), nullable=True),
        sa.Column("workflow", sa.String(length=64), nullable=False),
        sa.Column("step_name", sa.String(length=128), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runtime_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_id"], ["runtime_steps.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runtime_errors_run_id", "runtime_errors", ["run_id"], unique=False)
    op.create_index("ix_runtime_errors_workflow", "runtime_errors", ["workflow"], unique=False)
    op.create_index("ix_runtime_errors_step_name", "runtime_errors", ["step_name"], unique=False)

    op.create_table(
        "runtime_alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("error_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("target", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["error_id"], ["runtime_errors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runtime_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runtime_alerts_run_id", "runtime_alerts", ["run_id"], unique=False)
    op.create_index("ix_runtime_alerts_status", "runtime_alerts", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_runtime_alerts_status", table_name="runtime_alerts")
    op.drop_index("ix_runtime_alerts_run_id", table_name="runtime_alerts")
    op.drop_table("runtime_alerts")
    op.drop_index("ix_runtime_errors_step_name", table_name="runtime_errors")
    op.drop_index("ix_runtime_errors_workflow", table_name="runtime_errors")
    op.drop_index("ix_runtime_errors_run_id", table_name="runtime_errors")
    op.drop_table("runtime_errors")
    op.drop_index("ix_runtime_steps_status", table_name="runtime_steps")
    op.drop_index("ix_runtime_steps_step_type", table_name="runtime_steps")
    op.drop_index("ix_runtime_steps_step_name", table_name="runtime_steps")
    op.drop_index("ix_runtime_steps_workflow", table_name="runtime_steps")
    op.drop_index("ix_runtime_steps_run_id", table_name="runtime_steps")
    op.drop_table("runtime_steps")
    op.drop_index("ix_runtime_runs_status", table_name="runtime_runs")
    op.drop_index("ix_runtime_runs_chat_id", table_name="runtime_runs")
    op.drop_index("ix_runtime_runs_telegram_user_id", table_name="runtime_runs")
    op.drop_index("ix_runtime_runs_workflow", table_name="runtime_runs")
    op.drop_table("runtime_runs")
