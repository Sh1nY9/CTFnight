"""Bind sessions to the user's credential generation.

Revision ID: 20260824_0002
Revises: 20260824_0001
"""

import sqlalchemy as sa

from alembic import op

revision = "20260824_0002"
down_revision = "20260824_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("credential_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "sessions",
        sa.Column("credential_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("sessions", "credential_version")
    op.drop_column("users", "credential_version")
