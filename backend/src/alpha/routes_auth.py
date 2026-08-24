from __future__ import annotations

import hmac
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .dependencies import get_db, require_user, settings_from, user_dict
from .errors import ApiError
from .limits import MAX_ACTIVE_SESSIONS_PER_USER, MAX_PARTICIPANT_USERS
from .models import Membership, RegistrationCode, SessionToken, Team, User, utcnow
from .schemas import ChangePasswordRequest, LoginRequest, RegisterRequest
from .security import (
    csrf_context,
    hash_invite,
    hash_password,
    hash_registration_access,
    hash_session,
    issue_csrf,
    keyed_hash,
    password_needs_rehash,
    random_token,
    verify_password,
)
from .services import (
    add_audit,
    add_coalesced_audit,
    add_coalesced_outbox,
    add_outbox,
    aware,
    current_event,
    state_allows_registration,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("alpha.auth")
_DUMMY_PASSWORD_HASH = hash_password("alpha-dummy-password-not-used-for-login")
_REGISTRATION_ACCESS_DENIED_MESSAGE = "유효한 등록 접근 코드가 필요합니다."


def expired_session_cleanup_statement(now: datetime, batch_size: int):
    expired_ids = (
        select(SessionToken.id)
        .where(SessionToken.expires_at <= now)
        .order_by(SessionToken.expires_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    return (
        delete(SessionToken)
        .where(SessionToken.id.in_(expired_ids))
        .execution_options(synchronize_session=False)
    )


def cleanup_expired_sessions(db: Session, now: datetime, batch_size: int) -> int:
    result = db.execute(expired_session_cleanup_statement(now, batch_size))
    return result.rowcount or 0


def _check_auth_rate(request: Request, scope: str, identity: str) -> None:
    settings = settings_from(request)
    client_ip = request.client.host if request.client else "unknown"
    identity_hash = keyed_hash(settings.secret_key.get_secret_value(), "auth-identity", identity.lower())
    tiers = [
        (
            f"auth:{scope}:identity:{identity_hash}",
            settings.auth_rate_limit,
            settings.auth_rate_window_seconds,
        ),
        (
            f"auth:{scope}:ip:{client_ip}",
            settings.auth_ip_rate_limit,
            settings.auth_rate_window_seconds,
        ),
    ]
    if scope == "register":
        tiers.append(
            (
                "auth:register:global",
                settings.registration_global_rate_limit,
                settings.registration_global_rate_window_seconds,
            )
        )
    try:
        for key, limit, window in tiers:
            result = request.app.state.store.check_rate(key, limit, window)
            if not result.allowed:
                raise ApiError(
                    429,
                    "authentication_rate_limited",
                    "인증 요청이 너무 많습니다. 잠시 후 다시 시도하세요.",
                    {"Retry-After": str(result.retry_after)},
                )
    except ApiError:
        raise
    except Exception as exc:
        logger.exception("authentication_rate_store_unavailable")
        raise ApiError(503, "rate_limit_unavailable", "인증 보호 서비스를 사용할 수 없습니다.") from exc


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    settings = settings_from(request)
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    if settings.environment == "production":
        response.delete_cookie("alpha_session", path="/", secure=True, samesite="lax")


def _set_csrf_cookie(
    response: Response,
    request: Request,
    *,
    session_token: str | None = None,
) -> str:
    settings = settings_from(request)
    secret = settings.secret_key.get_secret_value()
    if session_token is None:
        session_token = request.cookies.get(settings.session_cookie_name, "")
    browser_token = ""
    if session_token:
        response.delete_cookie(
            settings.browser_cookie_name,
            path="/",
            secure=settings.secure_cookies,
            samesite="lax",
        )
    else:
        browser_token = request.cookies.get(settings.browser_cookie_name, "") or random_token(24)
        response.set_cookie(
            settings.browser_cookie_name,
            browser_token,
            max_age=settings.csrf_ttl_seconds,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="lax",
            path="/",
        )
    context = csrf_context(
        secret,
        session_token=session_token,
        browser_token=browser_token,
    )
    token = issue_csrf(secret, context)
    response.set_cookie(
        settings.csrf_cookie_name,
        token,
        max_age=settings.csrf_ttl_seconds,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    if settings.environment == "production":
        response.delete_cookie("alpha_csrf", path="/", secure=True, samesite="lax")
        response.delete_cookie("alpha_browser", path="/", secure=True, samesite="lax")
    response.headers["X-CSRF-Token"] = token
    return token


def _new_session(db: Session, request: Request, user: User) -> str:
    settings = settings_from(request)
    now = utcnow()
    cleanup_expired_sessions(db, now, settings.session_cleanup_batch_size)
    # The caller holds (or has just inserted) the User row. Preserve only the
    # newest N-1 active sessions before adding this one, so repeated successful
    # logins cannot grow the table without bound.
    excess_active_ids = (
        select(SessionToken.id)
        .where(SessionToken.user_id == user.id, SessionToken.expires_at > now)
        .order_by(SessionToken.created_at.desc(), SessionToken.id.desc())
        .offset(MAX_ACTIVE_SESSIONS_PER_USER - 1)
    )
    db.execute(
        delete(SessionToken)
        .where(SessionToken.id.in_(excess_active_ids))
        .execution_options(synchronize_session=False)
    )
    token = random_token()
    db.add(
        SessionToken(
            token_hash=hash_session(settings.secret_key.get_secret_value(), token),
            user_id=user.id,
            credential_version=user.credential_version,
            expires_at=now + timedelta(hours=settings.session_ttl_hours),
        )
    )
    return token


def _registration_code_or_denied(
    db: Session,
    request: Request,
    event,
    access_code: str | None,
    *,
    for_update: bool = False,
) -> RegistrationCode | None:
    if event.registration_access_mode != "code":
        return None
    secret = settings_from(request).secret_key.get_secret_value()
    # Hash an empty candidate as well so missing and unknown values follow the
    # same indexed lookup and generic denial path.
    candidate_hash = hash_registration_access(secret, access_code or "")
    stmt = select(RegistrationCode).where(
        RegistrationCode.event_id == event.id,
        RegistrationCode.token_hash == candidate_hash,
    )
    code = db.scalar(stmt.with_for_update() if for_update else stmt)
    now = utcnow()
    expires_at = aware(code.expires_at) if code is not None else None
    if (
        code is None
        or not code.active
        or code.revoked_at is not None
        or (expires_at is not None and expires_at <= now)
        or (code.max_uses is not None and code.use_count >= code.max_uses)
    ):
        raise ApiError(
            403,
            "registration_access_denied",
            _REGISTRATION_ACCESS_DENIED_MESSAGE,
        )
    return code


@router.get("/csrf")
def csrf(request: Request, response: Response) -> dict[str, str]:
    token = _set_csrf_cookie(response, request)
    return {"csrf_token": token}


@router.post("/register", status_code=201)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    _check_auth_rate(request, "register", str(payload.email))
    email = str(payload.email).strip().lower()
    username = payload.username.strip()

    # Reject obviously closed/duplicate registrations before doing expensive
    # password work, but return the connection to the pool before Argon2 runs.
    event = current_event(db)
    if event is None or not state_allows_registration(event):
        raise ApiError(409, "registration_closed", "현재 참가 등록을 받고 있지 않습니다.")
    _registration_code_or_denied(db, request, event, payload.access_code)
    exists = db.scalar(
        select(User.id).where(
            (func.lower(User.email) == email) | (func.lower(User.username) == username.lower())
        )
    )
    if exists:
        raise ApiError(409, "account_exists", "이미 사용 중인 이메일 또는 사용자 이름입니다.")
    participant_count = (
        db.scalar(select(func.count()).select_from(User).where(User.role == "participant")) or 0
    )
    if participant_count >= MAX_PARTICIPANT_USERS:
        raise ApiError(409, "participant_capacity_reached", "참가자 등록 정원에 도달했습니다.")
    db.rollback()
    password_hash = hash_password(payload.password)

    # Revalidate under a shared event lock after Argon2. This prevents an admin
    # transition to live from racing past a registration without holding a DB
    # connection during the expensive hash.
    event = current_event(db, for_update=True)
    if event is None or not state_allows_registration(event):
        raise ApiError(409, "registration_closed", "현재 참가 등록을 받고 있지 않습니다.")
    registration_code = _registration_code_or_denied(
        db,
        request,
        event,
        payload.access_code,
        for_update=True,
    )
    exists = db.scalar(
        select(User.id).where(
            (func.lower(User.email) == email) | (func.lower(User.username) == username.lower())
        )
    )
    if exists:
        raise ApiError(409, "account_exists", "이미 사용 중인 이메일 또는 사용자 이름입니다.")
    participant_count = (
        db.scalar(select(func.count()).select_from(User).where(User.role == "participant")) or 0
    )
    if participant_count >= MAX_PARTICIPANT_USERS:
        raise ApiError(409, "participant_capacity_reached", "참가자 등록 정원에 도달했습니다.")

    user = User(email=email, username=username, password_hash=password_hash)
    db.add(user)
    try:
        db.flush()
        if event.team_mode == "individual":
            secret = settings_from(request).secret_key.get_secret_value()
            solo_team = Team(
                name=f"{username}-{user.id.hex[:8]}",
                invite_hash=hash_invite(secret, random_token(18)),
                creator_id=user.id,
            )
            db.add(solo_team)
            db.flush()
            db.add(Membership(user_id=user.id, team_id=solo_team.id, role="owner"))
            add_audit(db, user.id, "team.solo_created", "team", solo_team.id)
        token = _new_session(db, request, user)
        add_audit(db, user.id, "auth.register", "user", user.id)
        add_outbox(db, "user.registered", "user", user.id, {"user_id": str(user.id)})
        if registration_code is not None:
            # The Event -> RegistrationCode locks remain held until this user,
            # session, audit event, outbox event and use are committed together.
            registration_code.use_count += 1
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "account_exists", "이미 사용 중인 이메일 또는 사용자 이름입니다.") from exc
    _set_session_cookie(response, request, token)
    _set_csrf_cookie(response, request, session_token=token)
    return user_dict(db, user)


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    email = str(payload.email).strip().lower()
    _check_auth_rate(request, "login", email)
    candidate = db.scalar(select(User).where(func.lower(User.email) == email))
    candidate_id = candidate.id if candidate else None
    candidate_hash = candidate.password_hash if candidate else _DUMMY_PASSWORD_HASH
    candidate_active = bool(candidate and candidate.active)
    db.rollback()

    password_valid = verify_password(candidate_hash, payload.password)
    if candidate_id is None or not password_valid or not candidate_active:
        raise ApiError(401, "invalid_credentials", "이메일 또는 비밀번호가 올바르지 않습니다.")
    replacement_hash = hash_password(payload.password) if password_needs_rehash(candidate_hash) else None

    # Lock only after verification. If a concurrent password change replaces
    # the hash, release the connection again before re-verifying. The bounded
    # loop prevents a continuously changing credential from pinning a worker.
    verified_hash = candidate_hash
    for _attempt in range(3):
        user = db.scalar(
            select(User)
            .where(User.id == candidate_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None or not user.active:
            raise ApiError(401, "invalid_credentials", "이메일 또는 비밀번호가 올바르지 않습니다.")
        current_hash = user.password_hash
        if hmac.compare_digest(current_hash, verified_hash):
            if replacement_hash is not None:
                user.password_hash = replacement_hash
            break
        db.rollback()
        if not verify_password(current_hash, payload.password):
            raise ApiError(401, "invalid_credentials", "이메일 또는 비밀번호가 올바르지 않습니다.")
        verified_hash = current_hash
        replacement_hash = hash_password(payload.password) if password_needs_rehash(current_hash) else None
    else:
        raise ApiError(409, "credential_changed", "자격 증명이 변경되었습니다. 다시 시도하세요.")
    token = _new_session(db, request, user)
    add_coalesced_audit(db, user.id, "auth.login", "user", user.id)
    db.commit()
    _set_session_cookie(response, request, token)
    _set_csrf_cookie(response, request, session_token=token)
    return user_dict(db, user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Response:
    settings = settings_from(request)
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        token_hash = hash_session(settings.secret_key.get_secret_value(), token)
        session = db.scalar(select(SessionToken).where(SessionToken.token_hash == token_hash))
        if session:
            user_id = session.user_id
            # Match change-password's User -> Session lock order, then recheck
            # the session in case credential rotation deleted it while waiting.
            user_exists = db.scalar(select(User.id).where(User.id == user_id).with_for_update())
            current_session = db.scalar(
                select(SessionToken).where(SessionToken.token_hash == token_hash).with_for_update()
            )
            if user_exists is not None and current_session is not None:
                db.delete(current_session)
                add_coalesced_audit(db, user_id, "auth.logout", "user", user_id)
                db.commit()
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.secure_cookies,
        samesite="lax",
    )
    if settings.environment == "production":
        response.delete_cookie("alpha_session", path="/", secure=True, samesite="lax")
    _set_csrf_cookie(response, request, session_token="")
    response.status_code = 204
    return response


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)) -> dict:
    return user_dict(db, require_user(request, db))


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    settings = settings_from(request)
    session_identity = request.cookies.get(settings.session_cookie_name, "missing-session")
    _check_auth_rate(request, "change-password", session_identity)
    authenticated_user = require_user(request, db)
    authenticated_user_id = authenticated_user.id
    password_hash_snapshot = authenticated_user.password_hash
    db.rollback()
    # The session token rotates after every success, so a second stable key is
    # required to prevent rotation from resetting the password-change budget.
    _check_auth_rate(request, "change-password-user", str(authenticated_user_id))

    if not verify_password(password_hash_snapshot, payload.current_password):
        raise ApiError(401, "invalid_current_password", "현재 비밀번호가 올바르지 않습니다.")
    replacement_hash = hash_password(payload.new_password)

    verified_hash = password_hash_snapshot
    session_hash = hash_session(settings.secret_key.get_secret_value(), session_identity)
    for _attempt in range(3):
        user = db.scalar(
            select(User)
            .where(User.id == authenticated_user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None or not user.active:
            raise ApiError(401, "invalid_session", "세션이 만료되었거나 유효하지 않습니다.")
        current_session = db.scalar(
            select(SessionToken)
            .where(
                SessionToken.token_hash == session_hash,
                SessionToken.user_id == authenticated_user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            current_session is None
            or aware(current_session.expires_at) <= utcnow()
            or current_session.credential_version != user.credential_version
        ):
            raise ApiError(401, "invalid_session", "세션이 만료되었거나 유효하지 않습니다.")
        current_hash = user.password_hash
        if hmac.compare_digest(current_hash, verified_hash):
            break
        db.rollback()
        if not verify_password(current_hash, payload.current_password):
            raise ApiError(401, "invalid_current_password", "현재 비밀번호가 올바르지 않습니다.")
        verified_hash = current_hash
    else:
        raise ApiError(409, "credential_changed", "자격 증명이 변경되었습니다. 다시 시도하세요.")

    user.password_hash = replacement_hash
    user.password_change_required = False
    user.credential_version += 1
    db.execute(
        delete(SessionToken)
        .where(SessionToken.user_id == user.id)
        .execution_options(synchronize_session=False)
    )
    token = _new_session(db, request, user)
    add_coalesced_audit(db, user.id, "auth.password_changed", "user", user.id)
    add_coalesced_outbox(db, "user.password_changed", "user", user.id, {"user_id": str(user.id)})
    db.commit()
    _set_session_cookie(response, request, token)
    _set_csrf_cookie(response, request, session_token=token)
    return user_dict(db, user)
