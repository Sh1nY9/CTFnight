from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .dependencies import get_db, require_admin, settings_from
from .errors import ApiError
from .models import (
    Announcement,
    AuditEvent,
    Challenge,
    Event,
    Membership,
    OutboxEvent,
    RegistrationCode,
    SessionToken,
    Solve,
    Submission,
    Team,
    User,
    utcnow,
)
from .schemas import (
    AnnouncementCreateRequest,
    AnnouncementUpdateRequest,
    ChallengeCreateRequest,
    ChallengeUpdateRequest,
    EventUpdateRequest,
    RegistrationCodeCreateRequest,
    UserStatusUpdateRequest,
    VisibilityRequest,
)
from .security import hash_registration_access, random_token
from .services import (
    add_audit,
    add_outbox,
    announcement_dict,
    apply_flag,
    assert_event_mutable,
    assert_freeze_at_mutable,
    assert_scoring_mutable,
    assert_state_transition,
    aware,
    challenge_points,
    current_event,
    event_dict,
    iso,
    load_prerequisites,
)

logger = logging.getLogger("alpha.admin")
router = APIRouter(prefix="/admin", tags=["admin"])


def _event_or_404(db: Session, *, for_update: bool = False) -> Event:
    event = current_event(db, for_update=for_update)
    if event is None:
        raise ApiError(404, "event_not_found", "현재 이벤트가 없습니다.")
    return event


def _challenge_or_404(
    db: Session, event: Event, challenge_id: uuid.UUID, *, for_update: bool = False
) -> Challenge:
    stmt = (
        select(Challenge)
        .options(selectinload(Challenge.prerequisites))
        .where(Challenge.id == challenge_id, Challenge.event_id == event.id)
    )
    challenge = db.scalar(stmt.with_for_update() if for_update else stmt)
    if challenge is None:
        raise ApiError(404, "challenge_not_found", "문제를 찾을 수 없습니다.")
    return challenge


def _admin_challenge_dict(db: Session, challenge: Challenge) -> dict:
    solve_count = (
        db.scalar(select(func.count()).select_from(Solve).where(Solve.challenge_id == challenge.id)) or 0
    )
    return {
        "id": str(challenge.id),
        "event_id": str(challenge.event_id),
        "slug": challenge.slug,
        "title": challenge.title,
        "category": challenge.category,
        "description_md": challenge.description_md,
        "connection_info": challenge.connection_info,
        "scoring_type": challenge.scoring_type,
        "initial_points": challenge.initial_points,
        "minimum_points": challenge.minimum_points,
        "decay": challenge.decay,
        "current_points": challenge_points(challenge, solve_count),
        "solve_count": solve_count,
        "visible": challenge.visible,
        "visible_at": iso(challenge.visible_at),
        "max_attempts": challenge.max_attempts,
        "flag_type": challenge.flag_type,
        "has_flag": bool(challenge.flag_hash or challenge.flag_regex),
        "prerequisite_ids": [str(item.id) for item in challenge.prerequisites],
        "created_at": iso(challenge.created_at),
        "updated_at": iso(challenge.updated_at),
    }


def _registration_code_dict(item: RegistrationCode) -> dict:
    return {
        "id": str(item.id),
        "event_id": str(item.event_id),
        "label": item.label,
        "max_uses": item.max_uses,
        "use_count": item.use_count,
        "expires_at": iso(item.expires_at),
        "active": item.active,
        "created_by": str(item.created_by),
        "created_at": iso(item.created_at),
        "revoked_at": iso(item.revoked_at),
    }


def _admin_user_dict(user: User, membership: Membership | None) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "username": user.username,
        "role": user.role,
        "active": user.active,
        "password_change_required": user.password_change_required,
        "team": None if membership is None else {"id": str(membership.team.id), "name": membership.team.name},
        "created_at": iso(user.created_at),
    }


def _invalidate_scoreboard(request: Request, event_id: uuid.UUID) -> None:
    try:
        request.app.state.store.increment(f"scoreboard:{event_id}:generation")
    except Exception:
        logger.exception("scoreboard_generation_increment_failed")
    for phase in ("live", "frozen", "final"):
        try:
            request.app.state.store.delete(f"scoreboard:{event_id}:public:{phase}")
        except Exception:
            logger.exception("scoreboard_cache_invalidation_failed")


@router.get("/event")
def admin_event(request: Request, db: Session = Depends(get_db)) -> dict:
    require_admin(request, db)
    return event_dict(_event_or_404(db))


@router.put("/event")
def update_event(payload: EventUpdateRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = require_admin(request, db)
    event = _event_or_404(db, for_update=True)
    assert_event_mutable(event)
    values = payload.model_dump(exclude_unset=True)
    if "slug" in values and values["slug"] != event.slug:
        raise ApiError(409, "event_slug_locked", "이벤트 slug는 생성한 뒤 변경할 수 없습니다.")
    if "freeze_at" in values:
        assert_freeze_at_mutable(event, values["freeze_at"])
    if "team_mode" in values and values["team_mode"] != event.team_mode:
        participant_count = (
            db.scalar(select(func.count()).select_from(User).where(User.role == "participant")) or 0
        )
        membership_count = db.scalar(select(func.count()).select_from(Membership)) or 0
        if participant_count or membership_count:
            raise ApiError(409, "team_mode_locked", "참가자 등록 후에는 팀 모드를 변경할 수 없습니다.")
    if "state" in values:
        assert_state_transition(event.state, values["state"])
        if values["state"] == "frozen":
            proposed_freeze = aware(values.get("freeze_at", event.freeze_at))
            now = utcnow()
            if proposed_freeze is None or proposed_freeze > now:
                values["freeze_at"] = now
    for key, value in values.items():
        setattr(event, key, value)
    start_at = aware(event.start_at)
    end_at = aware(event.end_at)
    registration_at = aware(event.registration_at)
    freeze_at = aware(event.freeze_at)
    if start_at and end_at and start_at >= end_at:
        raise ApiError(422, "invalid_event_times", "종료 시각은 시작 시각보다 뒤여야 합니다.")
    if registration_at and start_at and registration_at > start_at:
        raise ApiError(422, "invalid_event_times", "등록 시작 시각은 이벤트 시작보다 늦을 수 없습니다.")
    if freeze_at and start_at and freeze_at < start_at:
        raise ApiError(422, "invalid_event_times", "점수판 동결 시각은 이벤트 시작보다 빠를 수 없습니다.")
    if freeze_at and end_at and freeze_at > end_at:
        raise ApiError(422, "invalid_event_times", "점수판 동결 시각은 이벤트 종료보다 늦을 수 없습니다.")
    add_audit(db, admin.id, "event.updated", "event", event.id, {"fields": sorted(values)})
    add_outbox(db, "event.updated", "event", event.id, {"event_id": str(event.id), "state": event.state})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "event_conflict", "이벤트 설정이 다른 데이터와 충돌합니다.") from exc
    _invalidate_scoreboard(request, event.id)
    return event_dict(event)


@router.get("/registration-codes")
def registration_codes(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    require_admin(request, db)
    event = _event_or_404(db)
    rows = list(
        db.scalars(
            select(RegistrationCode)
            .where(RegistrationCode.event_id == event.id)
            .order_by(RegistrationCode.created_at.desc(), RegistrationCode.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return {
        "items": [_registration_code_dict(item) for item in rows],
        "limit": limit,
        "offset": offset,
    }


@router.post("/registration-codes", status_code=201)
def create_registration_code(
    payload: RegistrationCodeCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    admin = require_admin(request, db)
    event = _event_or_404(db, for_update=True)
    assert_event_mutable(event)
    access_code = random_token(24)
    item = RegistrationCode(
        event_id=event.id,
        token_hash=hash_registration_access(
            settings_from(request).secret_key.get_secret_value(),
            access_code,
        ),
        label=payload.label,
        max_uses=payload.max_uses,
        expires_at=payload.expires_at,
        active=True,
        created_by=admin.id,
    )
    db.add(item)
    try:
        db.flush()
        add_audit(
            db,
            admin.id,
            "registration_code.created",
            "registration_code",
            item.id,
            {"event_id": str(event.id), "label": item.label, "max_uses": item.max_uses},
        )
        add_outbox(
            db,
            "registration_code.created",
            "registration_code",
            item.id,
            {"event_id": str(event.id), "registration_code_id": str(item.id)},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(
            409,
            "registration_code_conflict",
            "등록 접근 코드를 생성하지 못했습니다. 다시 시도하세요.",
        ) from exc
    return _registration_code_dict(item) | {"access_code": access_code}


@router.delete("/registration-codes/{registration_code_id}", status_code=204)
def revoke_registration_code(
    registration_code_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    admin = require_admin(request, db)
    event = _event_or_404(db, for_update=True)
    assert_event_mutable(event)
    item = db.scalar(
        select(RegistrationCode)
        .where(
            RegistrationCode.id == registration_code_id,
            RegistrationCode.event_id == event.id,
        )
        .with_for_update()
    )
    if item is None:
        raise ApiError(404, "registration_code_not_found", "등록 접근 코드를 찾을 수 없습니다.")
    if item.active or item.revoked_at is None:
        item.active = False
        item.revoked_at = item.revoked_at or utcnow()
        add_audit(
            db,
            admin.id,
            "registration_code.revoked",
            "registration_code",
            item.id,
            {"event_id": str(event.id)},
        )
        add_outbox(
            db,
            "registration_code.revoked",
            "registration_code",
            item.id,
            {"event_id": str(event.id), "registration_code_id": str(item.id)},
        )
        db.commit()


@router.get("/challenges")
def admin_challenges(request: Request, db: Session = Depends(get_db)) -> dict:
    require_admin(request, db)
    event = _event_or_404(db)
    rows = list(
        db.scalars(
            select(Challenge)
            .options(selectinload(Challenge.prerequisites))
            .where(Challenge.event_id == event.id)
            .order_by(Challenge.category, Challenge.title)
        )
    )
    return {"items": [_admin_challenge_dict(db, item) for item in rows]}


@router.post("/challenges", status_code=201)
def create_challenge(
    payload: ChallengeCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    admin = require_admin(request, db)
    event = _event_or_404(db, for_update=True)
    assert_event_mutable(event)
    settings = settings_from(request)
    challenge = Challenge(
        event_id=event.id,
        slug=payload.slug,
        title=payload.title,
        category=payload.category,
        description_md=payload.description_md,
        connection_info=payload.connection_info,
        scoring_type=payload.scoring_type,
        initial_points=payload.initial_points,
        minimum_points=payload.minimum_points,
        decay=payload.decay,
        visible=payload.visible,
        visible_at=payload.visible_at,
        max_attempts=payload.max_attempts,
    )
    apply_flag(
        challenge,
        payload.flag,
        settings.secret_key.get_secret_value(),
        settings.max_flag_length,
    )
    challenge.prerequisites = load_prerequisites(db, event.id, payload.prerequisite_ids)
    db.add(challenge)
    try:
        db.flush()
        add_audit(db, admin.id, "challenge.created", "challenge", challenge.id)
        add_outbox(
            db,
            "challenge.created",
            "challenge",
            challenge.id,
            {"event_id": str(event.id), "challenge_id": str(challenge.id)},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "challenge_slug_exists", "같은 slug를 가진 문제가 이미 있습니다.") from exc
    _invalidate_scoreboard(request, event.id)
    return _admin_challenge_dict(db, challenge)


@router.put("/challenges/{challenge_id}")
def update_challenge(
    challenge_id: uuid.UUID,
    payload: ChallengeUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    admin = require_admin(request, db)
    event = _event_or_404(db, for_update=True)
    assert_event_mutable(event)
    challenge = _challenge_or_404(db, event, challenge_id, for_update=True)
    values = payload.model_dump(exclude_unset=True, exclude={"flag", "prerequisite_ids"})
    assert_scoring_mutable(event, challenge, values)
    for key, value in values.items():
        setattr(challenge, key, value)
    if challenge.minimum_points > challenge.initial_points:
        raise ApiError(422, "invalid_scoring", "최소 점수는 초기 점수를 초과할 수 없습니다.")
    if payload.flag is not None:
        settings = settings_from(request)
        apply_flag(
            challenge,
            payload.flag,
            settings.secret_key.get_secret_value(),
            settings.max_flag_length,
        )
    if payload.prerequisite_ids is not None:
        challenge.prerequisites = load_prerequisites(db, event.id, payload.prerequisite_ids, challenge.id)
    add_audit(
        db,
        admin.id,
        "challenge.updated",
        "challenge",
        challenge.id,
        {"fields": sorted(payload.model_fields_set)},
    )
    add_outbox(
        db,
        "challenge.updated",
        "challenge",
        challenge.id,
        {"event_id": str(event.id), "challenge_id": str(challenge.id)},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "challenge_conflict", "문제 설정이 다른 데이터와 충돌합니다.") from exc
    _invalidate_scoreboard(request, event.id)
    return _admin_challenge_dict(db, challenge)


@router.delete("/challenges/{challenge_id}", status_code=204)
def delete_challenge(
    challenge_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    admin = require_admin(request, db)
    event = _event_or_404(db, for_update=True)
    assert_event_mutable(event)
    challenge = _challenge_or_404(db, event, challenge_id, for_update=True)
    submission_count = (
        db.scalar(select(func.count()).select_from(Submission).where(Submission.challenge_id == challenge.id))
        or 0
    )
    if submission_count:
        raise ApiError(
            409,
            "challenge_has_submissions",
            "제출 이력이 있는 문제는 삭제할 수 없습니다. 대신 공개 상태를 해제하세요.",
        )
    db.delete(challenge)
    add_audit(db, admin.id, "challenge.deleted", "challenge", challenge.id)
    add_outbox(
        db,
        "challenge.deleted",
        "challenge",
        challenge.id,
        {"event_id": str(event.id), "challenge_id": str(challenge.id)},
    )
    db.commit()
    _invalidate_scoreboard(request, event.id)


@router.post("/challenges/{challenge_id}/visibility")
def set_visibility(
    challenge_id: uuid.UUID,
    payload: VisibilityRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    admin = require_admin(request, db)
    event = _event_or_404(db, for_update=True)
    assert_event_mutable(event)
    challenge = _challenge_or_404(db, event, challenge_id, for_update=True)
    challenge.visible = payload.visible
    add_audit(
        db, admin.id, "challenge.visibility_changed", "challenge", challenge.id, {"visible": payload.visible}
    )
    add_outbox(
        db,
        "challenge.visibility_changed",
        "challenge",
        challenge.id,
        {"challenge_id": str(challenge.id), "visible": payload.visible},
    )
    db.commit()
    return _admin_challenge_dict(db, challenge)


@router.get("/submissions")
def admin_submissions(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    challenge_id: uuid.UUID | None = None,
    correct: bool | None = None,
    before_created_at: datetime | None = None,
    before_id: uuid.UUID | None = None,
) -> dict:
    require_admin(request, db)
    event = _event_or_404(db)
    if (before_created_at is None) != (before_id is None):
        raise ApiError(422, "invalid_submission_cursor", "제출 cursor의 시각과 ID를 함께 지정하세요.")
    if before_created_at is not None and before_created_at.tzinfo is None:
        raise ApiError(422, "invalid_submission_cursor", "제출 cursor 시각에는 timezone이 필요합니다.")
    if before_created_at is not None and offset:
        raise ApiError(422, "invalid_submission_cursor", "cursor와 offset을 함께 사용할 수 없습니다.")
    stmt = (
        select(Submission, User, Team, Challenge)
        .join(User, User.id == Submission.user_id)
        .join(Team, Team.id == Submission.team_id)
        .join(Challenge, Challenge.id == Submission.challenge_id)
        .where(Challenge.event_id == event.id)
        # A stable tie-breaker is required for offset pagination and complete CSV exports.
        .order_by(Submission.created_at.desc(), Submission.id.desc())
    )
    if challenge_id is not None:
        stmt = stmt.where(Submission.challenge_id == challenge_id)
    if correct is not None:
        stmt = stmt.where(Submission.correct.is_(correct))
    if before_created_at is not None and before_id is not None:
        stmt = stmt.where(
            or_(
                Submission.created_at < before_created_at,
                and_(Submission.created_at == before_created_at, Submission.id < before_id),
            )
        )
    rows = list(db.execute(stmt.offset(offset).limit(limit)))
    return {
        "items": [
            {
                "id": str(submission.id),
                "team": {"id": str(team.id), "name": team.name},
                "user": {"id": str(user.id), "username": user.username},
                "challenge": {"id": str(challenge.id), "title": challenge.title},
                "correct": submission.correct,
                "awarded_points": submission.awarded_points,
                "submitted_fingerprint": submission.submitted_hash[:12],
                "ip_fingerprint": submission.ip_hash[:12],
                "created_at": iso(submission.created_at),
            }
            for submission, user, team, challenge in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/announcements")
def admin_announcements(request: Request, db: Session = Depends(get_db)) -> dict:
    require_admin(request, db)
    event = _event_or_404(db)
    rows = list(
        db.scalars(
            select(Announcement)
            .where(Announcement.event_id == event.id)
            .order_by(Announcement.publish_at.desc())
        )
    )
    return {"items": [announcement_dict(item) for item in rows]}


@router.post("/announcements", status_code=201)
def create_announcement(
    payload: AnnouncementCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    admin = require_admin(request, db)
    event = _event_or_404(db, for_update=True)
    assert_event_mutable(event)
    item = Announcement(
        event_id=event.id,
        title=payload.title,
        body_md=payload.body_md,
        publish_at=payload.publish_at or utcnow(),
    )
    db.add(item)
    db.flush()
    add_audit(db, admin.id, "announcement.created", "announcement", item.id)
    add_outbox(db, "announcement.created", "announcement", item.id, {"event_id": str(event.id)})
    db.commit()
    return announcement_dict(item)


@router.put("/announcements/{announcement_id}")
def update_announcement(
    announcement_id: uuid.UUID,
    payload: AnnouncementUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    admin = require_admin(request, db)
    event = _event_or_404(db, for_update=True)
    assert_event_mutable(event)
    item = db.scalar(
        select(Announcement)
        .where(Announcement.id == announcement_id, Announcement.event_id == event.id)
        .with_for_update()
    )
    if item is None:
        raise ApiError(404, "announcement_not_found", "공지를 찾을 수 없습니다.")
    values = payload.model_dump(exclude_unset=True)
    if "publish_at" in values and values["publish_at"] is None:
        values["publish_at"] = utcnow()
    for key, value in values.items():
        setattr(item, key, value)
    add_audit(
        db,
        admin.id,
        "announcement.updated",
        "announcement",
        item.id,
        {"fields": sorted(payload.model_fields_set)},
    )
    add_outbox(db, "announcement.updated", "announcement", item.id, {"event_id": str(event.id)})
    db.commit()
    return announcement_dict(item)


@router.delete("/announcements/{announcement_id}", status_code=204)
def delete_announcement(
    announcement_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    admin = require_admin(request, db)
    event = _event_or_404(db, for_update=True)
    assert_event_mutable(event)
    item = db.scalar(
        select(Announcement)
        .where(Announcement.id == announcement_id, Announcement.event_id == event.id)
        .with_for_update()
    )
    if item is None:
        raise ApiError(404, "announcement_not_found", "공지를 찾을 수 없습니다.")
    db.delete(item)
    add_audit(db, admin.id, "announcement.deleted", "announcement", item.id)
    add_outbox(db, "announcement.deleted", "announcement", item.id, {"event_id": str(event.id)})
    db.commit()


@router.get("/users")
def admin_users(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    require_admin(request, db)
    rows = list(db.scalars(select(User).order_by(User.created_at).offset(offset).limit(limit)))
    memberships = (
        {
            item.user_id: item
            for item in db.scalars(
                select(Membership)
                .options(selectinload(Membership.team))
                .where(Membership.user_id.in_([row.id for row in rows]))
            )
        }
        if rows
        else {}
    )
    return {
        "items": [_admin_user_dict(user, memberships.get(user.id)) for user in rows],
        "limit": limit,
        "offset": offset,
    }


@router.put("/users/{user_id}/status")
def update_user_status(
    user_id: uuid.UUID,
    payload: UserStatusUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    admin = require_admin(request, db)
    # User is the first mutable aggregate in this flow. Selecting by role in
    # the locking query revalidates that an administrator can never be targeted.
    user = db.scalar(
        select(User)
        .where(User.id == user_id, User.role == "participant")
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise ApiError(
            409,
            "participant_required",
            "참가자 계정만 상태를 변경할 수 있습니다.",
        )
    previous_active = user.active
    if previous_active == payload.active:
        if not payload.active:
            # Preserve idempotence while defensively cleaning up any session
            # row created outside the normal active-user authentication path.
            db.execute(
                delete(SessionToken)
                .where(SessionToken.user_id == user.id)
                .execution_options(synchronize_session=False)
            )
            db.commit()
        membership = db.scalar(
            select(Membership).options(selectinload(Membership.team)).where(Membership.user_id == user.id)
        )
        return _admin_user_dict(user, membership)
    user.active = payload.active
    if not payload.active:
        user.credential_version += 1
        db.execute(
            delete(SessionToken)
            .where(SessionToken.user_id == user.id)
            .execution_options(synchronize_session=False)
        )
    add_audit(
        db,
        admin.id,
        "user.status_changed",
        "user",
        user.id,
        {
            "active": payload.active,
            "previous_active": previous_active,
            "reason": payload.reason,
        },
    )
    add_outbox(
        db,
        "user.status_changed",
        "user",
        user.id,
        {"user_id": str(user.id), "active": payload.active},
    )
    db.commit()
    membership = db.scalar(
        select(Membership).options(selectinload(Membership.team)).where(Membership.user_id == user.id)
    )
    return _admin_user_dict(user, membership)


@router.get("/teams")
def admin_teams(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    require_admin(request, db)
    rows = list(db.scalars(select(Team).order_by(Team.created_at).offset(offset).limit(limit)))
    return {
        "items": [
            {
                "id": str(team.id),
                "name": team.name,
                "member_count": db.scalar(
                    select(func.count()).select_from(Membership).where(Membership.team_id == team.id)
                )
                or 0,
                "created_at": iso(team.created_at),
            }
            for team in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/audit")
def admin_audit(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    require_admin(request, db)
    rows = list(
        db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).offset(offset).limit(limit))
    )
    return {
        "items": [
            {
                "id": str(item.id),
                "actor_id": str(item.actor_id) if item.actor_id else None,
                "action": item.action,
                "target_type": item.target_type,
                "target_id": item.target_id,
                "metadata": item.metadata_json,
                "created_at": iso(item.created_at),
            }
            for item in rows
        ]
    }


@router.get("/outbox")
def admin_outbox(
    request: Request,
    db: Session = Depends(get_db),
    pending_only: bool = True,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    require_admin(request, db)
    stmt = select(OutboxEvent).order_by(OutboxEvent.created_at).limit(limit)
    if pending_only:
        stmt = stmt.where(OutboxEvent.delivered_at.is_(None))
    rows = list(db.scalars(stmt))
    return {
        "items": [
            {
                "id": str(item.id),
                "topic": item.topic,
                "aggregate_type": item.aggregate_type,
                "aggregate_id": item.aggregate_id,
                "payload": item.payload_json,
                "created_at": iso(item.created_at),
                "delivered_at": iso(item.delivered_at),
                "attempts": item.attempts,
            }
            for item in rows
        ]
    }
