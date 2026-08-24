from __future__ import annotations

import logging
from datetime import UTC

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .dependencies import get_db, membership_for, require_user, settings_from
from .errors import ApiError
from .limits import MAX_MEMBERS_PER_TEAM, MAX_TEAM_MUTATIONS_PER_USER_EVENT
from .models import AuditEvent, Membership, Submission, Team, User
from .schemas import TeamCreateRequest, TeamJoinRequest, TeamMemberRequest
from .security import hash_invite, keyed_hash, random_token
from .services import (
    add_audit,
    add_outbox,
    current_event,
    state_allows_registration,
    team_display_name,
)

router = APIRouter(prefix="/teams", tags=["teams"])
logger = logging.getLogger("alpha.teams")
_TEAM_MUTATION_ACTIONS = (
    "team.created",
    "team.joined",
    "team.invite_rotated",
    "team.left",
    "team.owner_transferred",
    "team.member_removed",
)


def _check_team_mutation_rate(request: Request) -> None:
    settings = settings_from(request)
    secret = settings.secret_key.get_secret_value()
    session_identity = request.cookies.get(settings.session_cookie_name, "anonymous")
    client_ip = request.client.host if request.client else "unknown"
    session_key = keyed_hash(secret, "team-mutation-session", session_identity)
    ip_key = keyed_hash(secret, "team-mutation-ip", client_ip)
    limits = (
        (f"team-mutation:session:{session_key}", settings.team_mutation_rate_limit),
        (f"team-mutation:ip:{ip_key}", settings.team_mutation_ip_rate_limit),
    )
    try:
        for key, limit in limits:
            result = request.app.state.store.check_rate(
                key, limit, settings.team_mutation_rate_window_seconds
            )
            if not result.allowed:
                raise ApiError(
                    429,
                    "team_mutation_rate_limited",
                    "팀 구성을 너무 빠르게 변경하고 있습니다. 잠시 후 다시 시도하세요.",
                    {"Retry-After": str(result.retry_after)},
                )
    except ApiError:
        raise
    except Exception as exc:
        logger.exception("team_mutation_rate_store_unavailable")
        raise ApiError(503, "rate_limit_unavailable", "팀 변경 보호 서비스를 사용할 수 없습니다.") from exc


def _lock_users(db: Session, user_ids) -> dict:
    """Lock users in the canonical order used by multi-user team mutations."""

    unique_ids = sorted(set(user_ids))
    users = list(
        db.scalars(
            select(User)
            .where(User.id.in_(unique_ids))
            .order_by(User.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    return {item.id: item for item in users}


def _lock_memberships(db: Session, user_ids) -> dict:
    """Lock memberships after users, ordered by user UUID to prevent deadlocks."""

    unique_ids = sorted(set(user_ids))
    memberships = list(
        db.scalars(
            select(Membership)
            .where(Membership.user_id.in_(unique_ids))
            .order_by(Membership.user_id, Membership.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    return {item.user_id: item for item in memberships}


def _assert_lifetime_limit(db: Session, user_id, event_id) -> None:
    event_mutations = (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.actor_id == user_id,
                AuditEvent.action.in_(_TEAM_MUTATION_ACTIONS),
                AuditEvent.metadata_json["event_id"].as_string() == str(event_id),
            )
        )
        or 0
    )
    if event_mutations >= MAX_TEAM_MUTATIONS_PER_USER_EVENT:
        raise ApiError(
            409,
            "team_mutation_limit_reached",
            "이 이벤트에서 허용된 팀 구성 변경 횟수에 도달했습니다.",
        )


def _lock_actor_and_assert_lifetime_limit(
    db: Session, user_id, event_id, expected_credential_version: int
) -> None:
    actor = _lock_users(db, [user_id]).get(user_id)
    if actor is None or not actor.active or actor.credential_version != expected_credential_version:
        raise ApiError(401, "invalid_session", "세션이 만료되었거나 유효하지 않습니다.")
    _assert_lifetime_limit(db, user_id, event_id)


def _ensure_team_changes_open(db: Session):
    # Team mutations may proceed concurrently, but the shared event lock keeps
    # a registration -> live transition behind every in-flight mutation.
    event = current_event(db, for_share=True)
    if event is None:
        raise ApiError(409, "team_changes_closed", "현재 팀 구성을 변경할 수 없습니다.")
    if event.team_mode == "individual":
        raise ApiError(409, "individual_mode", "개인전에서는 개인 팀이 자동으로 관리됩니다.")
    if not state_allows_registration(event):
        raise ApiError(409, "team_changes_closed", "현재 팀 구성을 변경할 수 없습니다.")
    return event


def _locked_membership(db: Session, user_id) -> Membership | None:
    return db.scalar(
        select(Membership)
        .where(Membership.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _locked_team(db: Session, team_id) -> Team | None:
    return db.scalar(
        select(Team).where(Team.id == team_id).with_for_update().execution_options(populate_existing=True)
    )


def _reload_team(db: Session, user: User, event) -> dict:
    membership = membership_for(db, user, include_members=True)
    if membership is None:
        raise RuntimeError("committed team membership could not be reloaded")
    return {"team": _team_dict(db, event, membership)}


def _team_dict(db: Session, event, membership: Membership) -> dict:
    def member_sort_key(item: Membership):
        joined_at = item.joined_at
        if joined_at.tzinfo is None:
            joined_at = joined_at.replace(tzinfo=UTC)
        return item.role != "owner", joined_at, str(item.user_id)

    return {
        "id": str(membership.team.id),
        "name": team_display_name(db, event, membership.team),
        "role": membership.role,
        "members": [
            {"id": str(member.user.id), "username": member.user.username, "role": member.role}
            for member in sorted(membership.team.memberships, key=member_sort_key)
        ],
    }


@router.get("/me")
def my_team(request: Request, db: Session = Depends(get_db)) -> dict:
    user = require_user(request, db)
    event = current_event(db)
    membership = membership_for(db, user, include_members=True)
    if membership is None:
        return {"team": None}
    for item in membership.team.memberships:
        _ = item.user
    return {"team": _team_dict(db, event, membership)}


@router.post("", status_code=201)
def create_team(payload: TeamCreateRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    _check_team_mutation_rate(request)
    user = require_user(request, db)
    event = _ensure_team_changes_open(db)
    _lock_actor_and_assert_lifetime_limit(db, user.id, event.id, user.credential_version)
    if _locked_membership(db, user.id):
        raise ApiError(409, "already_on_team", "이미 팀에 속해 있습니다.")
    if db.scalar(select(Team.id).where(func.lower(Team.name) == payload.name.lower())):
        raise ApiError(409, "team_name_exists", "이미 사용 중인 팀 이름입니다.")
    settings = settings_from(request)
    invite = random_token(18)
    team = Team(
        name=payload.name,
        invite_hash=hash_invite(settings.secret_key.get_secret_value(), invite),
        creator_id=user.id,
    )
    db.add(team)
    db.flush()
    membership = Membership(user_id=user.id, team_id=team.id, role="owner", user=user, team=team)
    db.add(membership)
    add_audit(db, user.id, "team.created", "team", team.id, {"event_id": str(event.id)})
    add_outbox(db, "team.created", "team", team.id, {"team_id": str(team.id)})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "team_conflict", "팀을 생성할 수 없습니다.") from exc
    return {"team": _team_dict(db, event, membership), "invite_code": invite}


@router.post("/join")
def join_team(payload: TeamJoinRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    _check_team_mutation_rate(request)
    user = require_user(request, db)
    event = _ensure_team_changes_open(db)
    _lock_actor_and_assert_lifetime_limit(db, user.id, event.id, user.credential_version)
    if _locked_membership(db, user.id):
        raise ApiError(409, "already_on_team", "이미 팀에 속해 있습니다.")
    settings = settings_from(request)
    invite_hash = hash_invite(settings.secret_key.get_secret_value(), payload.invite_code)
    team = db.scalar(select(Team).where(Team.invite_hash == invite_hash).with_for_update())
    if team is None:
        raise ApiError(404, "invalid_invite", "초대 코드가 유효하지 않습니다.")
    member_count = (
        db.scalar(select(func.count()).select_from(Membership).where(Membership.team_id == team.id)) or 0
    )
    if member_count >= MAX_MEMBERS_PER_TEAM:
        raise ApiError(409, "team_capacity_reached", "이 팀은 최대 인원에 도달했습니다.")
    membership = Membership(user_id=user.id, team_id=team.id, role="member", user=user, team=team)
    db.add(membership)
    add_audit(db, user.id, "team.joined", "team", team.id, {"event_id": str(event.id)})
    add_outbox(
        db,
        "team.member_joined",
        "team",
        team.id,
        {"team_id": str(team.id), "user_id": str(user.id)},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "team_join_conflict", "팀 참가 상태가 변경되었습니다.") from exc
    membership = membership_for(db, user, include_members=True)
    if membership is None:
        raise RuntimeError("committed team membership could not be reloaded")
    return {"team": _team_dict(db, event, membership)}


@router.post("/rotate-invite")
def rotate_invite(request: Request, db: Session = Depends(get_db)) -> dict:
    _check_team_mutation_rate(request)
    user = require_user(request, db)
    event = _ensure_team_changes_open(db)
    _lock_actor_and_assert_lifetime_limit(db, user.id, event.id, user.credential_version)
    membership = _locked_membership(db, user.id)
    if membership is None:
        raise ApiError(409, "team_required", "먼저 팀을 만들거나 참가해야 합니다.")
    team = _locked_team(db, membership.team_id)
    if team is None:
        raise ApiError(409, "team_required", "팀 상태를 확인할 수 없습니다.")
    if membership.role != "owner":
        raise ApiError(403, "team_owner_required", "팀 소유자만 초대 코드를 바꿀 수 있습니다.")
    invite = random_token(18)
    settings = settings_from(request)
    team.invite_hash = hash_invite(settings.secret_key.get_secret_value(), invite)
    add_audit(
        db,
        user.id,
        "team.invite_rotated",
        "team",
        membership.team_id,
        {"event_id": str(event.id)},
    )
    db.commit()
    return {"invite_code": invite}


def _lock_owner_and_target(db: Session, actor_id, target_id, event_id, expected_credential_version: int):
    """Acquire Event -> Users -> Memberships -> Team locks, then validate state."""

    user_ids = [actor_id, target_id]
    users = _lock_users(db, user_ids)
    actor = users.get(actor_id)
    if actor is None or not actor.active or actor.credential_version != expected_credential_version:
        raise ApiError(401, "invalid_session", "세션이 만료되었거나 유효하지 않습니다.")
    _assert_lifetime_limit(db, actor_id, event_id)

    memberships = _lock_memberships(db, user_ids)
    actor_membership = memberships.get(actor_id)
    if actor_membership is None:
        raise ApiError(409, "team_required", "먼저 팀을 만들거나 참가해야 합니다.")
    team = _locked_team(db, actor_membership.team_id)
    if team is None:
        raise ApiError(409, "team_required", "팀 상태를 확인할 수 없습니다.")

    # Everything used for authorization is revalidated only after the complete
    # canonical lock chain has been acquired.
    if actor_membership.role != "owner":
        raise ApiError(403, "team_owner_required", "팀 소유자만 팀원을 관리할 수 있습니다.")
    if actor_id == target_id:
        raise ApiError(409, "cannot_target_self", "자기 자신을 대상으로 지정할 수 없습니다.")
    target = users.get(target_id)
    target_membership = memberships.get(target_id)
    if (
        target is None
        or target_membership is None
        or target_membership.team_id != team.id
        or target_membership.role != "member"
    ):
        raise ApiError(404, "team_member_not_found", "대상 팀원을 찾을 수 없습니다.")
    return actor, actor_membership, target, target_membership, team


@router.post("/transfer-owner")
def transfer_owner(payload: TeamMemberRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    _check_team_mutation_rate(request)
    user = require_user(request, db)
    event = _ensure_team_changes_open(db)
    actor, actor_membership, target, target_membership, team = _lock_owner_and_target(
        db, user.id, payload.user_id, event.id, user.credential_version
    )
    if not target.active or target.role != "participant":
        raise ApiError(
            409,
            "owner_target_ineligible",
            "활성 참가자에게만 팀 소유권을 이전할 수 있습니다.",
        )

    actor_membership.role = "member"
    target_membership.role = "owner"
    metadata = {
        "event_id": str(event.id),
        "previous_owner_id": str(actor.id),
        "new_owner_id": str(target.id),
    }
    add_audit(db, actor.id, "team.owner_transferred", "team", team.id, metadata)
    add_outbox(
        db,
        "team.owner_transferred",
        "team",
        team.id,
        {"team_id": str(team.id), **metadata},
    )
    db.commit()
    return _reload_team(db, actor, event)


@router.post("/remove-member")
def remove_member(payload: TeamMemberRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    _check_team_mutation_rate(request)
    user = require_user(request, db)
    event = _ensure_team_changes_open(db)
    actor, _actor_membership, target, target_membership, team = _lock_owner_and_target(
        db, user.id, payload.user_id, event.id, user.credential_version
    )
    target_submission_count = (
        db.scalar(
            select(func.count())
            .select_from(Submission)
            .where(Submission.user_id == target.id, Submission.team_id == team.id)
        )
        or 0
    )
    if target_submission_count:
        raise ApiError(
            409,
            "member_has_activity",
            "제출 이력이 있는 참가자는 팀에서 제거할 수 없습니다.",
        )

    # Every joined member has seen the current invite. Revoking membership
    # without rotating it would let the removed account immediately rejoin.
    invite = random_token(18)
    settings = settings_from(request)
    team.invite_hash = hash_invite(settings.secret_key.get_secret_value(), invite)
    db.delete(target_membership)
    metadata = {
        "event_id": str(event.id),
        "removed_user_id": str(target.id),
    }
    add_audit(db, actor.id, "team.member_removed", "team", team.id, metadata)
    add_outbox(
        db,
        "team.member_removed",
        "team",
        team.id,
        {"team_id": str(team.id), **metadata},
    )
    db.commit()
    return {**_reload_team(db, actor, event), "invite_code": invite}


@router.post("/leave", status_code=204)
def leave_team(request: Request, db: Session = Depends(get_db)) -> None:
    _check_team_mutation_rate(request)
    user = require_user(request, db)
    event = _ensure_team_changes_open(db)
    _lock_actor_and_assert_lifetime_limit(db, user.id, event.id, user.credential_version)
    membership = _locked_membership(db, user.id)
    if membership is None:
        raise ApiError(409, "team_required", "먼저 팀을 만들거나 참가해야 합니다.")
    team = _locked_team(db, membership.team_id)
    if team is None:
        raise ApiError(409, "team_required", "팀 상태를 확인할 수 없습니다.")
    if membership.role == "owner" and len(team.memberships) > 1:
        raise ApiError(409, "owner_cannot_leave", "다른 팀원이 있는 동안 팀 소유자는 탈퇴할 수 없습니다.")
    user_submission_count = (
        db.scalar(
            select(func.count())
            .select_from(Submission)
            .where(Submission.user_id == user.id, Submission.team_id == membership.team_id)
        )
        or 0
    )
    if user_submission_count:
        raise ApiError(409, "member_has_activity", "제출 이력이 있는 참가자는 팀을 옮길 수 없습니다.")
    if membership.role == "owner":
        team_submission_count = (
            db.scalar(
                select(func.count()).select_from(Submission).where(Submission.team_id == membership.team_id)
            )
            or 0
        )
        if team_submission_count:
            raise ApiError(409, "team_has_activity", "제출 이력이 있는 팀은 삭제할 수 없습니다.")
    team_id = membership.team_id
    db.delete(membership)
    if membership.role == "owner":
        db.delete(team)
    add_audit(db, user.id, "team.left", "team", team_id, {"event_id": str(event.id)})
    add_outbox(db, "team.member_left", "team", team_id, {"team_id": str(team_id), "user_id": str(user.id)})
    db.commit()
