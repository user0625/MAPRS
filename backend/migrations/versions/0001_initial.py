"""Initial persistent task execution schema."""

from alembic import op
from backend.api.task_store import SQLModel

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    SQLModel.metadata.create_all(bind=bind)


def downgrade():
    bind = op.get_bind()
    SQLModel.metadata.drop_all(bind=bind)
