"""Initial platform schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-04
"""

import app.models  # noqa: F401
from alembic import op
from app.models import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
