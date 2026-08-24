from __future__ import annotations

from collections.abc import Generator
from datetime import UTC

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .config import Settings
from .errors import ApiError
from .models import Membership, SessionToken, Team, User, utcnow
from .security import hash_session
from .services import current_event, team_display_name


def get_db(request: Request) -> Generator[Session, None, None]:
    yield from request.app.state.db.session()


def settings_from(request: Request) -> Settings:
    return request.app.state.settings


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def current_user(request: Request, db: Session) -> User:
    settings = settings_from(request)
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise ApiError(401, "authentication_required", "로그인이 필요합니다.")
    token_hash = hash_session(settings.secret_key.get_secret_value(), token)
    session = db.scalar(
        select(SessionToken)
        .options(joinedload(SessionToken.user))
        .where(SessionToken.token_hash == token_hash)
    )
    if (
        session is None
        or _aware(session.expires_at) <= utcnow()
        or not session.user.active
        or session.credential_version != session.user.credential_version
    ):
        raise ApiError(401, "invalid_session", "세션이 만료되었거나 유효하지 않습니다.")
    return session.user


def require_user(request: Request, db: Session) -> User:
    return current_user(request, db)


def require_admin(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if user.role != "admin":
        raise ApiError(403, "admin_required", "관리자 권한이 필요합니다.")
    if user.password_change_required:
        raise ApiError(
            403, "password_change_required", "관리 기능을 사용하기 전에 초기 비밀번호를 변경하세요."
        )
    return user


def membership_for(db: Session, user: User, *, include_members: bool = False) -> Membership | None:
    team_loader = joinedload(Membership.team)
    options = (
        team_loader.selectinload(Team.memberships).joinedload(Membership.user)
        if include_members
        else team_loader
    )
    return db.scalar(select(Membership).options(options).where(Membership.user_id == user.id))


def require_membership(db: Session, user: User) -> Membership:
    membership = membership_for(db, user)
    if membership is None:
        raise ApiError(409, "team_required", "먼저 팀을 만들거나 참가해야 합니다.")
    return membership


def user_dict(db: Session, user: User) -> dict:
    membership = membership_for(db, user)
    event = current_event(db)
    return {
        "id": str(user.id),
        "email": user.email,
        "username": user.username,
        "role": user.role,
        "active": user.active,
        "password_change_required": user.password_change_required,
        "team": None
        if membership is None
        else {
            "id": str(membership.team.id),
            "name": team_display_name(db, event, membership.team),
            "role": membership.role,
        },
    }
