"""Add event registration access codes.

Revision ID: 20260824_0004
Revises: 20260824_0003
"""

import sqlalchemy as sa

from alembic import op

revision = "20260824_0004"
down_revision = "20260824_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "registration_access_mode",
            sa.String(20),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
    )
    if op.get_bind().dialect.name == "sqlite":
        # SQLite cannot add a CHECK constraint without rebuilding the events
        # table. Rebuilding a referenced table while foreign keys are enabled
        # can execute child ON DELETE actions, so use equivalent triggers.
        op.execute(
            sa.text(
                """
                CREATE TRIGGER ck_events_registration_access_mode_insert
                BEFORE INSERT ON events
                FOR EACH ROW WHEN NEW.registration_access_mode NOT IN ('open', 'code')
                BEGIN
                    SELECT RAISE(ABORT, 'ck_events_registration_access_mode');
                END
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE TRIGGER ck_events_registration_access_mode_update
                BEFORE UPDATE OF registration_access_mode ON events
                FOR EACH ROW WHEN NEW.registration_access_mode NOT IN ('open', 'code')
                BEGIN
                    SELECT RAISE(ABORT, 'ck_events_registration_access_mode');
                END
                """
            )
        )
    else:
        op.create_check_constraint(
            "ck_events_registration_access_mode",
            "events",
            "registration_access_mode IN ('open', 'code')",
        )

    op.create_table(
        "registration_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("max_uses", sa.Integer()),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "max_uses IS NULL OR (max_uses >= 1 AND max_uses <= 10000)",
            name="ck_registration_codes_max_uses",
        ),
        sa.CheckConstraint(
            "use_count >= 0 AND (max_uses IS NULL OR use_count <= max_uses)",
            name="ck_registration_codes_use_count",
        ),
        sa.CheckConstraint(
            "length(label) >= 1 AND length(label) <= 80",
            name="ck_registration_codes_label_length",
        ),
        sa.CheckConstraint(
            "length(token_hash) = 64",
            name="ck_registration_codes_token_hash_length",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR active = false",
            name="ck_registration_codes_revoked_inactive",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_registration_codes_event_id", "registration_codes", ["event_id"])
    op.create_index(
        "ix_registration_codes_token_hash",
        "registration_codes",
        ["token_hash"],
        unique=True,
    )
    op.create_index("ix_registration_codes_expires_at", "registration_codes", ["expires_at"])
    op.create_index("ix_registration_codes_active", "registration_codes", ["active"])
    op.create_index("ix_registration_codes_created_by", "registration_codes", ["created_by"])
    op.create_index(
        "ix_registration_codes_event_created",
        "registration_codes",
        ["event_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("registration_codes")
    if op.get_bind().dialect.name == "sqlite":
        op.execute(sa.text("DROP TRIGGER IF EXISTS ck_events_registration_access_mode_update"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS ck_events_registration_access_mode_insert"))
    else:
        op.drop_constraint("ck_events_registration_access_mode", "events", type_="check")
    op.drop_column("events", "registration_access_mode")
