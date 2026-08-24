from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Float, Integer, case, cast, func, literal, null, select, union_all
from sqlalchemy.orm import Session, selectinload

from .errors import ApiError
from .limits import MAX_PUBLIC_SCOREBOARD_ENTRIES
from .models import (
    Announcement,
    AuditEvent,
    Challenge,
    Event,
    OutboxEvent,
    ScoreEvent,
    Solve,
    Team,
    User,
    utcnow,
)
from .schemas import FlagInput
from .security import hash_flag, validate_regex

logger = logging.getLogger("alpha.services")
EVENT_STATES = ["draft", "registration", "live", "frozen", "ended", "archived"]


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def current_event_query(*, archived_only: bool = False, for_update: bool = False, for_share: bool = False):
    if for_update and for_share:
        raise ValueError("event lock mode must be exclusive or shared")
    stmt = select(Event)
    stmt = stmt.where(Event.state == "archived") if archived_only else stmt.where(Event.state != "archived")
    stmt = stmt.order_by(Event.created_at.desc()).limit(1)
    if for_update:
        return stmt.with_for_update()
    if for_share:
        return stmt.with_for_update(read=True)
    return stmt


def current_event(db: Session, *, for_update: bool = False, for_share: bool = False) -> Event | None:
    event = db.scalar(current_event_query(for_update=for_update, for_share=for_share))
    if event is None:
        event = db.scalar(current_event_query(archived_only=True, for_update=for_update, for_share=for_share))
    return event


def event_dict(event: Event) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "name": event.name,
        "slug": event.slug,
        "description_md": event.description_md,
        "state": event.state,
        "registration_at": iso(event.registration_at),
        "start_at": iso(event.start_at),
        "freeze_at": iso(event.freeze_at),
        "end_at": iso(event.end_at),
        "team_mode": event.team_mode,
        "registration_access_mode": event.registration_access_mode,
    }


def state_allows_registration(event: Event) -> bool:
    now = utcnow()
    registration_at = aware(event.registration_at)
    end_at = aware(event.end_at)
    return (
        event.state == "registration"
        and (registration_at is None or registration_at <= now)
        and (end_at is None or now < end_at)
    )


def state_allows_submission(event: Event) -> bool:
    now = utcnow()
    start_at = aware(event.start_at)
    end_at = aware(event.end_at)
    return (
        event.state in {"live", "frozen"}
        and (start_at is None or start_at <= now)
        and (end_at is None or now < end_at)
    )


def state_allows_challenge_access(event: Event) -> bool:
    if event.state in {"ended", "archived"}:
        return True
    start_at = aware(event.start_at)
    return event.state in {"live", "frozen"} and (start_at is None or start_at <= utcnow())


def assert_state_transition(old: str, new: str) -> None:
    if new == old:
        return
    if old == "archived" or EVENT_STATES.index(new) != EVENT_STATES.index(old) + 1:
        raise ApiError(
            409,
            "invalid_state_transition",
            "이벤트 상태는 정확히 다음 단계로만 전환할 수 있습니다.",
        )


def assert_event_mutable(event: Event) -> None:
    if event.state == "archived":
        raise ApiError(409, "event_archived", "보관된 이벤트는 읽기 전용입니다.")


def scoreboard_freeze_locked(event: Event) -> bool:
    freeze_at = aware(event.freeze_at)
    return event.state in {"frozen", "ended", "archived"} or bool(
        event.state == "live" and freeze_at and freeze_at <= utcnow()
    )


def public_scoreboard_is_frozen(event: Event, now: datetime | None = None) -> bool:
    now = now or utcnow()
    freeze_at = aware(event.freeze_at)
    return event.state == "frozen" or bool(event.state == "live" and freeze_at and freeze_at <= now)


def public_scoreboard_cache_phase(event: Event, now: datetime | None = None) -> str:
    if event.state in {"ended", "archived"}:
        return "final"
    return "frozen" if public_scoreboard_is_frozen(event, now) else "live"


def assert_freeze_at_mutable(event: Event, proposed: datetime | None) -> None:
    if scoreboard_freeze_locked(event) and aware(proposed) != aware(event.freeze_at):
        raise ApiError(
            409,
            "scoreboard_freeze_locked",
            "점수판 동결이 시작된 뒤에는 freeze_at을 변경할 수 없습니다.",
        )


def assert_scoring_mutable(event: Event, challenge: Challenge, proposed: dict[str, Any]) -> None:
    fields = {"scoring_type", "initial_points", "minimum_points", "decay"}
    changed = any(field in proposed and proposed[field] != getattr(challenge, field) for field in fields)
    if changed and scoreboard_freeze_locked(event):
        raise ApiError(
            409,
            "frozen_scoring_locked",
            "점수판 동결 또는 이벤트 종료 뒤에는 점수 산식을 변경할 수 없습니다.",
        )


def add_audit(
    db: Session,
    actor_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: uuid.UUID | str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            metadata_json=metadata or {},
        )
    )


def add_outbox(
    db: Session,
    topic: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID | str,
    payload: dict[str, Any],
) -> None:
    db.add(
        OutboxEvent(
            topic=topic,
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            payload_json=payload,
        )
    )


def add_coalesced_audit(
    db: Session,
    actor_id: uuid.UUID,
    action: str,
    target_type: str,
    target_id: uuid.UUID | str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Keep one row for a repetitive authenticated action.

    Callers lock the actor's User row first. That shared lock contract keeps
    concurrent requests from creating duplicate aggregate rows.
    """

    now = utcnow()
    existing = db.scalar(
        select(AuditEvent)
        .where(AuditEvent.actor_id == actor_id, AuditEvent.action == action)
        .order_by(AuditEvent.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if existing is None:
        values = dict(metadata or {})
        values.update({"occurrences": 1, "first_seen_at": iso(now), "last_seen_at": iso(now)})
        db.add(
            AuditEvent(
                actor_id=actor_id,
                action=action,
                target_type=target_type,
                target_id=str(target_id),
                metadata_json=values,
                created_at=now,
            )
        )
        return
    values = dict(existing.metadata_json or {})
    occurrences = values.get("occurrences", 1)
    values.update(metadata or {})
    values["occurrences"] = occurrences + 1 if isinstance(occurrences, int) else 2
    values.setdefault("first_seen_at", iso(existing.created_at))
    values["last_seen_at"] = iso(now)
    existing.target_type = target_type
    existing.target_id = str(target_id)
    existing.metadata_json = values
    existing.created_at = now


def add_coalesced_outbox(
    db: Session,
    topic: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID | str,
    payload: dict[str, Any],
) -> None:
    """Coalesce repetitive notifications while their prior event is pending."""

    aggregate_id_text = str(aggregate_id)
    now = utcnow()
    existing = db.scalar(
        select(OutboxEvent)
        .where(
            OutboxEvent.topic == topic,
            OutboxEvent.aggregate_type == aggregate_type,
            OutboxEvent.aggregate_id == aggregate_id_text,
            OutboxEvent.delivered_at.is_(None),
        )
        .order_by(OutboxEvent.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if existing is None:
        values = dict(payload)
        values["occurrences"] = 1
        db.add(
            OutboxEvent(
                topic=topic,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id_text,
                payload_json=values,
                created_at=now,
            )
        )
        return
    values = dict(existing.payload_json or {})
    occurrences = values.get("occurrences", 1)
    values.update(payload)
    values["occurrences"] = occurrences + 1 if isinstance(occurrences, int) else 2
    existing.payload_json = values
    existing.created_at = now


def dynamic_points(initial: int, minimum: int, decay: int, solve_count: int) -> int:
    if solve_count <= 0:
        return initial
    value = initial - ((initial - minimum) * (solve_count**2) / (decay**2))
    return max(minimum, int(round(value)))


def challenge_points(challenge: Challenge, solve_count: int) -> int:
    if challenge.scoring_type == "fixed":
        return challenge.initial_points
    return dynamic_points(challenge.initial_points, challenge.minimum_points, challenge.decay, solve_count)


def apply_flag(challenge: Challenge, flag: FlagInput, secret: str, max_length: int) -> None:
    if len(flag.value) > max_length:
        raise ApiError(422, "flag_too_long", "플래그가 설정된 최대 길이를 초과했습니다.")
    if flag.type == "exact":
        challenge.flag_type = "exact"
        challenge.flag_hash = hash_flag(secret, flag.value)
        challenge.flag_regex = None
    else:
        try:
            validate_regex(flag.value)
        except Exception as exc:
            raise ApiError(422, "invalid_flag_regex", "정규식 플래그 형식이 올바르지 않습니다.") from exc
        challenge.flag_type = "regex"
        challenge.flag_hash = None
        challenge.flag_regex = flag.value


def load_prerequisites(
    db: Session,
    event_id: uuid.UUID,
    ids: list[uuid.UUID],
    challenge_id: uuid.UUID | None = None,
) -> list[Challenge]:
    if len(set(ids)) != len(ids):
        raise ApiError(422, "duplicate_prerequisite", "선행 문제 목록에 중복이 있습니다.")
    if challenge_id is not None and challenge_id in ids:
        raise ApiError(422, "self_prerequisite", "문제 자신을 선행 문제로 지정할 수 없습니다.")
    if not ids:
        return []
    rows = list(
        db.scalars(
            select(Challenge)
            .options(selectinload(Challenge.prerequisites))
            .where(Challenge.event_id == event_id, Challenge.id.in_(ids))
        )
    )
    if len(rows) != len(ids):
        raise ApiError(
            422, "invalid_prerequisite", "같은 이벤트에 존재하는 문제만 선행 문제로 지정할 수 있습니다."
        )
    if challenge_id is not None:
        by_id = {
            row.id: row
            for row in db.scalars(
                select(Challenge)
                .options(selectinload(Challenge.prerequisites))
                .where(Challenge.event_id == event_id)
            )
        }

        def reaches_target(node: Challenge, seen: set[uuid.UUID]) -> bool:
            if node.id == challenge_id:
                return True
            if node.id in seen:
                return False
            seen.add(node.id)
            return any(reaches_target(by_id.get(item.id, item), seen) for item in node.prerequisites)

        if any(reaches_target(row, set()) for row in rows):
            raise ApiError(422, "prerequisite_cycle", "선행 문제 관계에 순환이 생깁니다.")
    order = {value: index for index, value in enumerate(ids)}
    return sorted(rows, key=lambda row: order[row.id])


def team_display_name(db: Session, event: Event | None, team: Team) -> str:
    if event is not None and event.team_mode == "individual":
        return db.scalar(select(User.username).where(User.id == team.creator_id)) or "개인 참가자"
    return team.name


def public_scoreboard(db: Session, event: Event, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    freeze_at = aware(event.freeze_at)
    frozen = public_scoreboard_is_frozen(event, now)
    cutoff = freeze_at if frozen else None

    count_stmt = (
        select(Solve.challenge_id.label("challenge_id"), func.count(Solve.id).label("solve_count"))
        .join(Challenge, Challenge.id == Solve.challenge_id)
        .where(Challenge.event_id == event.id)
        .group_by(Solve.challenge_id)
    )
    if cutoff is not None:
        count_stmt = count_stmt.where(Solve.solved_at <= cutoff)
    challenge_counts = count_stmt.subquery()

    solve_count_float = cast(challenge_counts.c.solve_count, Float)
    decay_float = cast(Challenge.decay, Float)
    raw_dynamic_points = cast(Challenge.initial_points, Float) - (
        cast(Challenge.initial_points - Challenge.minimum_points, Float)
        * solve_count_float
        * solve_count_float
        / (decay_float * decay_float)
    )
    rounded_dynamic_points = cast(func.round(raw_dynamic_points), Integer)
    dynamic_score = case(
        (rounded_dynamic_points < Challenge.minimum_points, Challenge.minimum_points),
        else_=rounded_dynamic_points,
    )
    solve_points = case(
        (Challenge.scoring_type == "fixed", Challenge.initial_points),
        else_=dynamic_score,
    )
    solve_aggregate = (
        select(
            Solve.team_id.label("team_id"),
            cast(func.sum(solve_points), Integer).label("score"),
            cast(func.count(Solve.id), Integer).label("solves"),
            func.max(Solve.solved_at).label("last_solve_at"),
        )
        .join(Challenge, Challenge.id == Solve.challenge_id)
        .join(challenge_counts, challenge_counts.c.challenge_id == Solve.challenge_id)
        .where(Challenge.event_id == event.id)
        .group_by(Solve.team_id)
    )
    if cutoff is not None:
        solve_aggregate = solve_aggregate.where(Solve.solved_at <= cutoff)

    award_aggregate = (
        select(
            ScoreEvent.team_id.label("team_id"),
            cast(func.sum(ScoreEvent.points), Integer).label("score"),
            literal(0, type_=Integer).label("solves"),
            cast(null(), DateTime(timezone=True)).label("last_solve_at"),
        )
        .where(
            ScoreEvent.event_id == event.id,
            ScoreEvent.kind == "award",
            ScoreEvent.active.is_(True),
        )
        .group_by(ScoreEvent.team_id)
    )
    if cutoff is not None:
        award_aggregate = award_aggregate.where(ScoreEvent.created_at <= cutoff)

    contributions = union_all(solve_aggregate, award_aggregate).subquery()
    team_totals = (
        select(
            contributions.c.team_id,
            cast(func.sum(contributions.c.score), Integer).label("score"),
            cast(func.sum(contributions.c.solves), Integer).label("solves"),
            func.max(contributions.c.last_solve_at).label("last_solve_at"),
        )
        .group_by(contributions.c.team_id)
        .subquery()
    )
    total_entries = db.scalar(select(func.count()).select_from(team_totals)) or 0
    if event.team_mode == "individual":
        team_name = func.coalesce(User.username, literal("개인 참가자")).label("team_name")
        ranking_stmt = (
            select(Team.id, team_name, team_totals.c.score, team_totals.c.solves, team_totals.c.last_solve_at)
            .join(team_totals, team_totals.c.team_id == Team.id)
            .outerjoin(User, User.id == Team.creator_id)
        )
    else:
        team_name = Team.name.label("team_name")
        ranking_stmt = select(
            Team.id,
            team_name,
            team_totals.c.score,
            team_totals.c.solves,
            team_totals.c.last_solve_at,
        ).join(team_totals, team_totals.c.team_id == Team.id)
    null_last_first = case((team_totals.c.last_solve_at.is_(None), 0), else_=1)
    ranking_stmt = ranking_stmt.order_by(
        team_totals.c.score.desc(),
        team_totals.c.solves.desc(),
        null_last_first,
        team_totals.c.last_solve_at.asc(),
        func.lower(team_name),
    ).limit(MAX_PUBLIC_SCOREBOARD_ENTRIES)
    rows = list(db.execute(ranking_stmt).mappings())
    entries = [
        {
            "rank": rank,
            "team_id": str(row["id"]),
            "team_name": row["team_name"],
            "score": row["score"],
            "solves": row["solves"],
            "last_solve_at": iso(row["last_solve_at"]),
        }
        for rank, row in enumerate(rows, start=1)
    ]
    return {
        "event": {"id": str(event.id), "name": event.name, "state": event.state},
        "frozen": frozen,
        "generated_at": iso(now),
        "total_entries": total_entries,
        "truncated": total_entries > MAX_PUBLIC_SCOREBOARD_ENTRIES,
        "entries": entries,
    }


def announcement_dict(item: Announcement) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "title": item.title,
        "body_md": item.body_md,
        "publish_at": iso(item.publish_at),
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
    }
