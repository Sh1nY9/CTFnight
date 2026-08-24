from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


challenge_prerequisites = Table(
    "challenge_prerequisites",
    Base.metadata,
    Column("challenge_id", ForeignKey("challenges.id", ondelete="CASCADE"), primary_key=True),
    Column("prerequisite_id", ForeignKey("challenges.id", ondelete="CASCADE"), primary_key=True),
    CheckConstraint("challenge_id <> prerequisite_id", name="ck_challenge_prerequisite_not_self"),
)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('participant', 'admin')", name="ck_users_role"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(20), default="participant")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    password_change_required: Mapped[bool] = mapped_column(Boolean, default=False)
    credential_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sessions: Mapped[list[SessionToken]] = relationship(back_populates="user", cascade="all, delete-orphan")
    membership: Mapped[Membership | None] = relationship(back_populates="user", uselist=False)


class SessionToken(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    credential_version: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="sessions")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    invite_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    creator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    memberships: Mapped[list[Membership]] = relationship(back_populates="team", cascade="all, delete-orphan")


# App-level event locking serializes normal registration/team flows; these
# functional unique indexes remain the database-level defense for every writer.
Index("uq_users_email_ci", func.lower(User.email), unique=True)
Index("uq_users_username_ci", func.lower(User.username), unique=True)
Index("uq_teams_name_ci", func.lower(Team.name), unique=True)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_memberships_user"),
        UniqueConstraint("team_id", "user_id", name="uq_memberships_team_user"),
        CheckConstraint("role IN ('owner', 'member')", name="ck_memberships_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="membership")
    team: Mapped[Team] = relationship(back_populates="memberships")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(
            "state IN ('draft', 'registration', 'live', 'frozen', 'ended', 'archived')",
            name="ck_events_state",
        ),
        CheckConstraint("team_mode IN ('team', 'individual')", name="ck_events_team_mode"),
        CheckConstraint(
            "registration_access_mode IN ('open', 'code')",
            name="ck_events_registration_access_mode",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    description_md: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    registration_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    freeze_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    team_mode: Mapped[str] = mapped_column(String(20), default="team")
    registration_access_mode: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    challenges: Mapped[list[Challenge]] = relationship(back_populates="event", cascade="all, delete-orphan")
    announcements: Mapped[list[Announcement]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    registration_codes: Mapped[list[RegistrationCode]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class RegistrationCode(Base):
    __tablename__ = "registration_codes"
    __table_args__ = (
        CheckConstraint(
            "max_uses IS NULL OR (max_uses >= 1 AND max_uses <= 10000)",
            name="ck_registration_codes_max_uses",
        ),
        CheckConstraint(
            "use_count >= 0 AND (max_uses IS NULL OR use_count <= max_uses)",
            name="ck_registration_codes_use_count",
        ),
        CheckConstraint(
            "length(label) >= 1 AND length(label) <= 80",
            name="ck_registration_codes_label_length",
        ),
        CheckConstraint(
            "length(token_hash) = 64",
            name="ck_registration_codes_token_hash_length",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR active = false",
            name="ck_registration_codes_revoked_inactive",
        ),
        Index("ix_registration_codes_event_created", "event_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(80))
    max_uses: Mapped[int | None] = mapped_column(Integer)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    event: Mapped[Event] = relationship(back_populates="registration_codes")


class Challenge(Base):
    __tablename__ = "challenges"
    __table_args__ = (
        UniqueConstraint("event_id", "slug", name="uq_challenges_event_slug"),
        CheckConstraint("scoring_type IN ('fixed', 'dynamic')", name="ck_challenges_scoring_type"),
        CheckConstraint("flag_type IN ('exact', 'regex')", name="ck_challenges_flag_type"),
        CheckConstraint("initial_points > 0", name="ck_challenges_initial_points"),
        CheckConstraint("minimum_points > 0", name="ck_challenges_minimum_points"),
        CheckConstraint("decay > 0", name="ck_challenges_decay"),
        CheckConstraint("max_attempts >= 0", name="ck_challenges_max_attempts"),
        CheckConstraint("max_attempts <= 1000", name="ck_challenges_max_attempts_upper"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(80), index=True)
    description_md: Mapped[str] = mapped_column(Text, default="")
    connection_info: Mapped[str | None] = mapped_column(Text)
    scoring_type: Mapped[str] = mapped_column(String(20), default="fixed")
    initial_points: Mapped[int] = mapped_column(Integer, default=100)
    minimum_points: Mapped[int] = mapped_column(Integer, default=100)
    decay: Mapped[int] = mapped_column(Integer, default=20)
    visible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    visible_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_attempts: Mapped[int] = mapped_column(Integer, default=0)
    flag_type: Mapped[str] = mapped_column(String(20), default="exact")
    flag_hash: Mapped[str | None] = mapped_column(String(64))
    flag_regex: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    event: Mapped[Event] = relationship(back_populates="challenges")
    prerequisites: Mapped[list[Challenge]] = relationship(
        secondary=challenge_prerequisites,
        primaryjoin=id == challenge_prerequisites.c.challenge_id,
        secondaryjoin=id == challenge_prerequisites.c.prerequisite_id,
        lazy="selectin",
    )


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        UniqueConstraint("team_id", "challenge_id", "idempotency_key", name="uq_submissions_idempotency"),
        Index("ix_submissions_team_challenge_created", "team_id", "challenge_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("challenges.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    submitted_hash: Mapped[str] = mapped_column(String(64))
    correct: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    awarded_points: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    ip_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Solve(Base):
    __tablename__ = "solves"
    __table_args__ = (UniqueConstraint("team_id", "challenge_id", name="uq_solves_team_challenge"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("challenges.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="RESTRICT"), unique=True
    )
    solved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ScoreEvent(Base):
    __tablename__ = "score_events"
    __table_args__ = (CheckConstraint("kind IN ('solve', 'award')", name="ck_score_events_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    challenge_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("challenges.id", ondelete="CASCADE"), index=True
    )
    solve_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("solves.id", ondelete="CASCADE"), unique=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    points: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body_md: Mapped[str] = mapped_column(Text)
    publish_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    event: Mapped[Event] = relationship(back_populates="announcements")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(120), index=True)
    target_type: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str | None] = mapped_column(String(80))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    topic: Mapped[str] = mapped_column(String(120), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(80))
    aggregate_id: Mapped[str] = mapped_column(String(80))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
