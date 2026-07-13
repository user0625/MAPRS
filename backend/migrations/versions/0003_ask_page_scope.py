"""Persist optional Ask Paper page ranges."""

from alembic import op
import sqlalchemy as sa

revision = "0003_ask_page_scope"
down_revision = "0002_ask_paper"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("paper_messages", sa.Column("page_start", sa.Integer()))
    op.add_column("paper_messages", sa.Column("page_end", sa.Integer()))


def downgrade():
    op.drop_column("paper_messages", "page_end")
    op.drop_column("paper_messages", "page_start")
