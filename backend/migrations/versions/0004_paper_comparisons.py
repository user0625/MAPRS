"""Persistent multi-paper comparisons, evidence snapshots, and event streams."""

from alembic import op
import sqlalchemy as sa

revision = "0004_paper_comparisons"
down_revision = "0003_ask_page_scope"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "paper_comparisons",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("focus", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("current_step", sa.String()),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("report_path", sa.String()),
        sa.Column("structured_path", sa.String()),
        sa.Column("artifacts", sa.JSON(), nullable=False),
        sa.Column("retry_of", sa.String()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("cancel_requested_at", sa.DateTime()),
        sa.Column("last_event_id", sa.Integer(), nullable=False),
    )
    op.create_index("ix_paper_comparisons_status", "paper_comparisons", ["status"])
    op.create_index("ix_paper_comparisons_created_at", "paper_comparisons", ["created_at"])
    op.create_index("ix_paper_comparisons_retry_of", "paper_comparisons", ["retry_of"])
    op.create_table(
        "comparison_papers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("comparison_id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_task_id", sa.String(), nullable=False),
        sa.Column("paper_id", sa.String()),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column("year", sa.Integer()),
        sa.Column("state_json_path", sa.String(), nullable=False),
        sa.UniqueConstraint("comparison_id", "position"),
        sa.UniqueConstraint("comparison_id", "source_task_id"),
    )
    op.create_index("ix_comparison_papers_comparison_id", "comparison_papers", ["comparison_id"])
    op.create_index("ix_comparison_papers_source_task_id", "comparison_papers", ["source_task_id"])
    op.create_table(
        "comparison_evidence",
        sa.Column("evidence_id", sa.String(), primary_key=True),
        sa.Column("comparison_id", sa.String(), nullable=False),
        sa.Column("source_task_id", sa.String(), nullable=False),
        sa.Column("paper_id", sa.String()),
        sa.Column("paper_title", sa.String(), nullable=False),
        sa.Column("chunk_id", sa.String(), nullable=False),
        sa.Column("page_start", sa.Integer()),
        sa.Column("page_end", sa.Integer()),
        sa.Column("section", sa.String()),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("score", sa.Float()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_comparison_evidence_comparison_id", "comparison_evidence", ["comparison_id"])
    op.create_index("ix_comparison_evidence_source_task_id", "comparison_evidence", ["source_task_id"])
    op.create_table(
        "comparison_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("comparison_id", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("status", sa.String()),
        sa.Column("step", sa.String()),
        sa.Column("message", sa.String()),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("comparison_id", "sequence"),
    )
    op.create_index("ix_comparison_events_comparison_id", "comparison_events", ["comparison_id"])
    op.create_index("ix_comparison_events_event_type", "comparison_events", ["event_type"])
    op.create_index("ix_comparison_events_created_at", "comparison_events", ["created_at"])


def downgrade():
    op.drop_table("comparison_events")
    op.drop_table("comparison_evidence")
    op.drop_table("comparison_papers")
    op.drop_table("paper_comparisons")
