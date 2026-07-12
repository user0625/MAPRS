"""Persistent Ask Paper conversations and durable streams."""

from alembic import op
import sqlalchemy as sa

revision = "0002_ask_paper"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("paper_conversations", sa.Column("id", sa.String(), primary_key=True),
        sa.Column("task_id", sa.String(), nullable=False), sa.Column("title", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_paper_conversations_task_id", "paper_conversations", ["task_id"])
    op.create_table("paper_messages", sa.Column("id", sa.String(), primary_key=True),
        sa.Column("conversation_id", sa.String(), nullable=False), sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False), sa.Column("status", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=False), sa.Column("section", sa.String()),
        sa.Column("citation_ids", sa.JSON(), nullable=False), sa.Column("error", sa.Text()),
        sa.Column("retry_of", sa.String()), sa.Column("cancel_requested_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_paper_messages_conversation_id", "paper_messages", ["conversation_id"])
    op.create_table("message_evidence", sa.Column("evidence_id", sa.String(), primary_key=True),
        sa.Column("message_id", sa.String(), nullable=False), sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("chunk_id", sa.String()), sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_start", sa.Integer()), sa.Column("page_end", sa.Integer()),
        sa.Column("section", sa.String()), sa.Column("score", sa.Float()), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_message_evidence_message_id", "message_evidence", ["message_id"])
    op.create_table("message_stream_events", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.String(), nullable=False), sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False), sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.UniqueConstraint("message_id", "sequence"))
    op.create_index("ix_message_stream_events_message_id", "message_stream_events", ["message_id"])


def downgrade():
    op.drop_table("message_stream_events")
    op.drop_table("message_evidence")
    op.drop_table("paper_messages")
    op.drop_table("paper_conversations")
