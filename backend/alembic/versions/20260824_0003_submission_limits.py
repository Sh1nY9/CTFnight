"""Enforce the immutable challenge-attempt storage ceiling.

Revision ID: 20260824_0003
Revises: 20260824_0002
"""

import sqlalchemy as sa

from alembic import op

revision = "20260824_0003"
down_revision = "20260824_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Earlier previews accepted larger values. Runtime behavior already clamps
    # them, so normalize before installing the database-level invariant.
    op.execute(sa.text("UPDATE challenges SET max_attempts = 1000 WHERE max_attempts > 1000"))
    if op.get_bind().dialect.name == "sqlite":
        # Rebuilding this referenced table with foreign_keys=ON can execute
        # child ON DELETE actions. Equivalent insert/update triggers preserve
        # existing child rows while enforcing the same invariant on SQLite.
        op.execute(
            sa.text(
                """
                CREATE TRIGGER ck_challenges_max_attempts_upper_insert
                BEFORE INSERT ON challenges
                FOR EACH ROW WHEN NEW.max_attempts > 1000
                BEGIN
                    SELECT RAISE(ABORT, 'ck_challenges_max_attempts_upper');
                END
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE TRIGGER ck_challenges_max_attempts_upper_update
                BEFORE UPDATE OF max_attempts ON challenges
                FOR EACH ROW WHEN NEW.max_attempts > 1000
                BEGIN
                    SELECT RAISE(ABORT, 'ck_challenges_max_attempts_upper');
                END
                """
            )
        )
    else:
        op.create_check_constraint(
            "ck_challenges_max_attempts_upper",
            "challenges",
            "max_attempts <= 1000",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(sa.text("DROP TRIGGER IF EXISTS ck_challenges_max_attempts_upper_update"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS ck_challenges_max_attempts_upper_insert"))
    else:
        op.drop_constraint("ck_challenges_max_attempts_upper", "challenges", type_="check")
