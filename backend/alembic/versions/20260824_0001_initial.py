"""Initial CTFnight schema.

Revision ID: 20260824_0001
Revises:
"""

import sqlalchemy as sa

from alembic import op

revision = "20260824_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("username", sa.String(40), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("password_change_required", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('participant', 'admin')", name="ck_users_role"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("uq_users_email_ci", "users", [sa.text("lower(email)")], unique=True)
    op.create_index("uq_users_username_ci", "users", [sa.text("lower(username)")], unique=True)

    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("description_md", sa.Text(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("registration_at", sa.DateTime(timezone=True)),
        sa.Column("start_at", sa.DateTime(timezone=True)),
        sa.Column("freeze_at", sa.DateTime(timezone=True)),
        sa.Column("end_at", sa.DateTime(timezone=True)),
        sa.Column("team_mode", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('draft', 'registration', 'live', 'frozen', 'ended', 'archived')",
            name="ck_events_state",
        ),
        sa.CheckConstraint("team_mode IN ('team', 'individual')", name="ck_events_team_mode"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_slug", "events", ["slug"], unique=True)
    op.create_index("ix_events_state", "events", ["state"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("invite_hash", sa.String(64), nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_teams_name", "teams", ["name"], unique=True)
    op.create_index("ix_teams_invite_hash", "teams", ["invite_hash"], unique=True)
    op.create_index("uq_teams_name_ci", "teams", [sa.text("lower(name)")], unique=True)

    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'member')", name="ck_memberships_role"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_memberships_user"),
        sa.UniqueConstraint("team_id", "user_id", name="uq_memberships_team_user"),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    op.create_index("ix_memberships_team_id", "memberships", ["team_id"])

    op.create_table(
        "challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("description_md", sa.Text(), nullable=False),
        sa.Column("connection_info", sa.Text()),
        sa.Column("scoring_type", sa.String(20), nullable=False),
        sa.Column("initial_points", sa.Integer(), nullable=False),
        sa.Column("minimum_points", sa.Integer(), nullable=False),
        sa.Column("decay", sa.Integer(), nullable=False),
        sa.Column("visible", sa.Boolean(), nullable=False),
        sa.Column("visible_at", sa.DateTime(timezone=True)),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("flag_type", sa.String(20), nullable=False),
        sa.Column("flag_hash", sa.String(64)),
        sa.Column("flag_regex", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scoring_type IN ('fixed', 'dynamic')", name="ck_challenges_scoring_type"),
        sa.CheckConstraint("flag_type IN ('exact', 'regex')", name="ck_challenges_flag_type"),
        sa.CheckConstraint("initial_points > 0", name="ck_challenges_initial_points"),
        sa.CheckConstraint("minimum_points > 0", name="ck_challenges_minimum_points"),
        sa.CheckConstraint("decay > 0", name="ck_challenges_decay"),
        sa.CheckConstraint("max_attempts >= 0", name="ck_challenges_max_attempts"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "slug", name="uq_challenges_event_slug"),
    )
    op.create_index("ix_challenges_event_id", "challenges", ["event_id"])
    op.create_index("ix_challenges_category", "challenges", ["category"])
    op.create_index("ix_challenges_visible", "challenges", ["visible"])

    op.create_table(
        "challenge_prerequisites",
        sa.Column("challenge_id", sa.Uuid(), nullable=False),
        sa.Column("prerequisite_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("challenge_id <> prerequisite_id", name="ck_challenge_prerequisite_not_self"),
        sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prerequisite_id"], ["challenges.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("challenge_id", "prerequisite_id"),
    )

    op.create_table(
        "announcements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("publish_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_announcements_event_id", "announcements", ["event_id"])
    op.create_index("ix_announcements_publish_at", "announcements", ["publish_at"])

    op.create_table(
        "submissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("challenge_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("submitted_hash", sa.String(64), nullable=False),
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column("awarded_points", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("ip_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "challenge_id", "idempotency_key", name="uq_submissions_idempotency"),
    )
    op.create_index("ix_submissions_team_id", "submissions", ["team_id"])
    op.create_index("ix_submissions_challenge_id", "submissions", ["challenge_id"])
    op.create_index("ix_submissions_user_id", "submissions", ["user_id"])
    op.create_index("ix_submissions_correct", "submissions", ["correct"])
    op.create_index("ix_submissions_created_at", "submissions", ["created_at"])
    op.create_index(
        "ix_submissions_team_challenge_created",
        "submissions",
        ["team_id", "challenge_id", "created_at"],
    )

    op.create_table(
        "solves",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("challenge_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("solved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("submission_id"),
        sa.UniqueConstraint("team_id", "challenge_id", name="uq_solves_team_challenge"),
    )
    op.create_index("ix_solves_team_id", "solves", ["team_id"])
    op.create_index("ix_solves_challenge_id", "solves", ["challenge_id"])
    op.create_index("ix_solves_solved_at", "solves", ["solved_at"])

    op.create_table(
        "score_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("challenge_id", sa.Uuid()),
        sa.Column("solve_id", sa.Uuid()),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('solve', 'award')", name="ck_score_events_kind"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["solve_id"], ["solves.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("solve_id"),
    )
    op.create_index("ix_score_events_event_id", "score_events", ["event_id"])
    op.create_index("ix_score_events_team_id", "score_events", ["team_id"])
    op.create_index("ix_score_events_challenge_id", "score_events", ["challenge_id"])
    op.create_index("ix_score_events_active", "score_events", ["active"])
    op.create_index("ix_score_events_created_at", "score_events", ["created_at"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid()),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("target_type", sa.String(80), nullable=False),
        sa.Column("target_id", sa.String(80)),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.String(120), nullable=False),
        sa.Column("aggregate_type", sa.String(80), nullable=False),
        sa.Column("aggregate_id", sa.String(80), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outbox_events_topic", "outbox_events", ["topic"])
    op.create_index("ix_outbox_events_created_at", "outbox_events", ["created_at"])
    op.create_index("ix_outbox_events_delivered_at", "outbox_events", ["delivered_at"])


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_table("audit_events")
    op.drop_table("score_events")
    op.drop_table("solves")
    op.drop_table("submissions")
    op.drop_table("announcements")
    op.drop_table("challenge_prerequisites")
    op.drop_table("challenges")
    op.drop_table("memberships")
    op.drop_table("teams")
    op.drop_table("sessions")
    op.drop_table("events")
    op.drop_table("users")
