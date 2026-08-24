from __future__ import annotations

import logging
import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .dependencies import get_db, membership_for, require_user, settings_from
from .errors import ApiError
from .limits import (
    MAX_CHALLENGE_ATTEMPTS,
    MAX_SUBMISSIONS_PER_TEAM_EVENT,
    SCOREBOARD_BUILD_LEASE_SECONDS,
)
from .models import Announcement, Challenge, Membership, ScoreEvent, Solve, Submission, Team, User, utcnow
from .schemas import SubmitRequest
from .security import compare_exact_flag, compare_regex_flag, hash_ip, hash_submission, keyed_hash
from .services import (
    add_audit,
    add_outbox,
    announcement_dict,
    challenge_points,
    current_event,
    event_dict,
    iso,
    public_scoreboard,
    public_scoreboard_cache_phase,
    state_allows_challenge_access,
    state_allows_submission,
)

logger = logging.getLogger("alpha.participant")
router = APIRouter(tags=["participant"])


def _visibility_context(db: Session, team_id: uuid.UUID | None) -> set[uuid.UUID]:
    if team_id is None:
        return set()
    return set(db.scalars(select(Solve.challenge_id).where(Solve.team_id == team_id)))


def _is_accessible(challenge: Challenge, solved_ids: set[uuid.UUID]) -> bool:
    now = utcnow()
    if not challenge.visible:
        return False
    if challenge.visible_at is not None:
        visible_at = challenge.visible_at
        if visible_at.tzinfo is None:
            visible_at = visible_at.replace(tzinfo=now.tzinfo)
        if visible_at > now:
            return False
    return all(item.id in solved_ids for item in challenge.prerequisites)


def _challenge_stats(
    db: Session,
    challenges: list[Challenge],
    team_id: uuid.UUID | None,
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int], set[uuid.UUID]]:
    ids = [challenge.id for challenge in challenges]
    solve_counts: dict[uuid.UUID, int] = defaultdict(int)
    attempt_counts: dict[uuid.UUID, int] = defaultdict(int)
    solved_ids: set[uuid.UUID] = set()
    if ids:
        for challenge_id, count in db.execute(
            select(Solve.challenge_id, func.count())
            .where(Solve.challenge_id.in_(ids))
            .group_by(Solve.challenge_id)
        ):
            solve_counts[challenge_id] = count
    if team_id is not None and ids:
        solved_ids = set(
            db.scalars(
                select(Solve.challenge_id).where(Solve.team_id == team_id, Solve.challenge_id.in_(ids))
            )
        )
        for challenge_id, count in db.execute(
            select(Submission.challenge_id, func.count())
            .where(Submission.team_id == team_id, Submission.challenge_id.in_(ids))
            .group_by(Submission.challenge_id)
        ):
            attempt_counts[challenge_id] = count
    return solve_counts, attempt_counts, solved_ids


def _challenge_dict(
    challenge: Challenge,
    solve_count: int,
    attempts: int,
    solved: bool,
) -> dict:
    return {
        "id": str(challenge.id),
        "slug": challenge.slug,
        "title": challenge.title,
        "category": challenge.category,
        "description_md": challenge.description_md,
        "connection_info": challenge.connection_info,
        "scoring_type": challenge.scoring_type,
        "current_points": challenge_points(challenge, solve_count),
        "solve_count": solve_count,
        "solved": solved,
        "max_attempts": challenge.max_attempts,
        "attempts": attempts,
        "visible_at": iso(challenge.visible_at),
        "prerequisite_ids": [str(item.id) for item in challenge.prerequisites],
    }


@router.get("/events/current")
def get_current_event(db: Session = Depends(get_db)) -> dict:
    event = current_event(db)
    if event is None:
        raise ApiError(404, "event_not_found", "현재 이벤트가 없습니다.")
    return event_dict(event)


@router.get("/announcements")
def announcements(db: Session = Depends(get_db)) -> dict:
    event = current_event(db)
    if event is None:
        return {"items": []}
    items = list(
        db.scalars(
            select(Announcement)
            .where(Announcement.event_id == event.id, Announcement.publish_at <= utcnow())
            .order_by(Announcement.publish_at.desc())
        )
    )
    return {"items": [announcement_dict(item) for item in items]}


@router.get("/challenges")
def challenges(request: Request, db: Session = Depends(get_db)) -> dict:
    user = require_user(request, db)
    event = current_event(db)
    if event is None or not state_allows_challenge_access(event):
        return {"items": []}
    membership = membership_for(db, user)
    team_id = membership.team_id if membership else None
    rows = list(
        db.scalars(
            select(Challenge)
            .options(selectinload(Challenge.prerequisites))
            .where(Challenge.event_id == event.id)
            .order_by(Challenge.category, Challenge.initial_points, Challenge.title)
        )
    )
    solve_counts, attempt_counts, solved_ids = _challenge_stats(db, rows, team_id)
    visible_rows = [item for item in rows if _is_accessible(item, solved_ids)]
    return {
        "items": [
            _challenge_dict(
                item,
                solve_counts[item.id],
                attempt_counts[item.id],
                item.id in solved_ids,
            )
            for item in visible_rows
        ]
    }


@router.get("/challenges/{challenge_id}")
def challenge_detail(challenge_id: uuid.UUID, request: Request, db: Session = Depends(get_db)) -> dict:
    user = require_user(request, db)
    event = current_event(db)
    membership = membership_for(db, user)
    team_id = membership.team_id if membership else None
    if event is None or not state_allows_challenge_access(event):
        raise ApiError(404, "challenge_not_found", "문제를 찾을 수 없습니다.")
    challenge = db.scalar(
        select(Challenge)
        .options(selectinload(Challenge.prerequisites))
        .where(Challenge.id == challenge_id, Challenge.event_id == event.id)
    )
    if challenge is None:
        raise ApiError(404, "challenge_not_found", "문제를 찾을 수 없습니다.")
    solved_ids = _visibility_context(db, team_id)
    if not _is_accessible(challenge, solved_ids):
        raise ApiError(404, "challenge_not_found", "문제를 찾을 수 없습니다.")
    solve_counts, attempt_counts, _ = _challenge_stats(db, [challenge], team_id)
    return _challenge_dict(
        challenge,
        solve_counts[challenge.id],
        attempt_counts[challenge.id],
        challenge.id in solved_ids,
    )


def _submission_result(db: Session, submission: Submission) -> dict:
    solve = (
        db.scalar(select(Solve).where(Solve.submission_id == submission.id)) if submission.correct else None
    )
    return {
        "correct": submission.correct,
        "message": "정답입니다." if submission.correct else "정답이 아닙니다.",
        "awarded_points": submission.awarded_points,
        "solved_at": iso(solve.solved_at) if solve else None,
    }


def _check_submission_rate(
    request: Request,
    *,
    event_id: uuid.UUID,
    team_id: uuid.UUID,
    challenge_id: uuid.UUID,
    client_ip: str,
) -> None:
    settings = settings_from(request)
    ip_key = keyed_hash(settings.secret_key.get_secret_value(), "submission-ip", client_ip)
    limits = (
        (f"submit:{event_id}:team:{team_id}", settings.submission_rate_limit),
        (f"submit:{event_id}:ip:{ip_key}", settings.submission_ip_rate_limit),
        (
            f"submit:{event_id}:challenge:{challenge_id}",
            settings.submission_challenge_rate_limit,
        ),
    )
    try:
        for key, limit in limits:
            result = request.app.state.store.check_rate(key, limit, settings.submission_rate_window_seconds)
            if not result.allowed:
                raise ApiError(
                    429,
                    "submission_rate_limited",
                    "너무 빠르게 제출하고 있습니다. 잠시 후 다시 시도하세요.",
                    {"Retry-After": str(result.retry_after)},
                )
    except ApiError:
        raise
    except Exception as exc:
        logger.exception("submission_rate_store_unavailable")
        raise ApiError(503, "rate_limit_unavailable", "제출 보호 서비스를 사용할 수 없습니다.") from exc


@router.post("/challenges/{challenge_id}/submit")
def submit_flag(
    challenge_id: uuid.UUID,
    payload: SubmitRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    user = require_user(request, db)
    # All state-changing participant flows use the same lock order:
    # Event -> User -> Membership -> Team -> Challenge (correct only).
    event = current_event(db, for_share=True)
    if event is None or not state_allows_submission(event):
        raise ApiError(409, "submissions_closed", "현재 플래그를 제출할 수 없습니다.")
    membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
    if membership is None:
        raise ApiError(409, "team_required", "먼저 팀을 만들거나 참가해야 합니다.")
    team_id = membership.team_id
    settings = settings_from(request)
    if len(payload.flag) > settings.max_flag_length:
        raise ApiError(422, "flag_too_long", "플래그가 허용 길이를 초과했습니다.")

    existing_submission = db.scalar(
        select(Submission).where(
            Submission.team_id == team_id,
            Submission.challenge_id == challenge_id,
            Submission.idempotency_key == payload.idempotency_key,
        )
    )
    if existing_submission is not None:
        return _submission_result(db, existing_submission)

    client_ip = request.client.host if request.client else "unknown"
    _check_submission_rate(
        request,
        event_id=event.id,
        team_id=team_id,
        challenge_id=challenge_id,
        client_ip=client_ip,
    )

    # Reject bursts in Redis before allowing requests to wait on database row
    # locks. Then re-lock and revalidate the actor's membership so a concurrent
    # team leave cannot submit against the stale team snapshot used for rating.
    locked_user = db.scalar(
        select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True)
    )
    if locked_user is None or not locked_user.active:
        raise ApiError(401, "invalid_session", "세션이 만료되었거나 유효하지 않습니다.")
    # A moderation action may have revoked the browser session after the
    # initial require_user() snapshot but before this row lock. Re-read the
    # session under the now-held User lock so an in-flight request cannot make
    # one final submission after suspension or credential rotation.
    revalidated_user = require_user(request, db)
    if revalidated_user.id != locked_user.id:
        raise ApiError(401, "invalid_session", "세션이 만료되었거나 유효하지 않습니다.")
    user = locked_user
    locked_membership = db.scalar(
        select(Membership)
        .where(Membership.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_membership is None or locked_membership.team_id != team_id:
        raise ApiError(409, "team_membership_changed", "팀 참가 상태가 변경되었습니다. 다시 시도하세요.")
    if db.scalar(select(Team.id).where(Team.id == team_id).with_for_update()) is None:
        raise ApiError(409, "team_required", "먼저 팀을 만들거나 참가해야 합니다.")
    existing_submission = db.scalar(
        select(Submission).where(
            Submission.team_id == team_id,
            Submission.challenge_id == challenge_id,
            Submission.idempotency_key == payload.idempotency_key,
        )
    )
    if existing_submission is not None:
        return _submission_result(db, existing_submission)

    challenge = db.scalar(
        select(Challenge)
        .options(selectinload(Challenge.prerequisites))
        .where(Challenge.id == challenge_id, Challenge.event_id == event.id)
    )
    solved_ids = _visibility_context(db, team_id)
    if challenge is None or not _is_accessible(challenge, solved_ids):
        raise ApiError(404, "challenge_not_found", "문제를 찾을 수 없습니다.")

    existing_solve = db.scalar(
        select(Solve).where(Solve.team_id == team_id, Solve.challenge_id == challenge.id)
    )
    if existing_solve is not None:
        return {
            "correct": True,
            "message": "이미 해결한 문제입니다.",
            "awarded_points": 0,
            "solved_at": iso(existing_solve.solved_at),
        }

    attempts = (
        db.scalar(
            select(func.count())
            .select_from(Submission)
            .where(
                Submission.team_id == team_id,
                Submission.challenge_id == challenge.id,
            )
        )
        or 0
    )
    effective_challenge_limit = min(
        challenge.max_attempts or MAX_CHALLENGE_ATTEMPTS,
        MAX_CHALLENGE_ATTEMPTS,
    )
    if attempts >= effective_challenge_limit:
        code = "attempt_limit_reached" if challenge.max_attempts else "submission_storage_limit_reached"
        raise ApiError(409, code, "이 문제의 최대 제출 횟수에 도달했습니다.")

    event_attempts = (
        db.scalar(
            select(func.count())
            .select_from(Submission)
            .join(Challenge, Submission.challenge_id == Challenge.id)
            .where(
                Submission.team_id == team_id,
                Challenge.event_id == event.id,
            )
        )
        or 0
    )
    if event_attempts >= MAX_SUBMISSIONS_PER_TEAM_EVENT:
        raise ApiError(
            409,
            "submission_storage_limit_reached",
            "이 이벤트에서 팀의 최대 제출 횟수에 도달했습니다.",
        )

    secret = settings.secret_key.get_secret_value()
    if challenge.flag_type == "exact":
        correct = compare_exact_flag(secret, challenge.flag_hash, payload.flag)
    else:
        regex_result = compare_regex_flag(challenge.flag_regex, payload.flag, settings.regex_timeout_seconds)
        correct = regex_result.matched
        if regex_result.timed_out:
            logger.warning("flag_regex_timeout", extra={"challenge_id": str(challenge.id)})

    if correct:
        # Incorrect attempts never take the globally contended challenge-row
        # lock. A candidate that appears correct is locked, refreshed, and
        # matched again before the solve and score transaction is committed.
        locked_challenge = db.scalar(
            select(Challenge)
            .where(Challenge.id == challenge.id, Challenge.event_id == event.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked_challenge is None:
            raise ApiError(404, "challenge_not_found", "문제를 찾을 수 없습니다.")
        challenge = locked_challenge
        if challenge.flag_type == "exact":
            correct = compare_exact_flag(secret, challenge.flag_hash, payload.flag)
        else:
            regex_result = compare_regex_flag(
                challenge.flag_regex,
                payload.flag,
                settings.regex_timeout_seconds,
            )
            correct = regex_result.matched
            if regex_result.timed_out:
                logger.warning("flag_regex_timeout", extra={"challenge_id": str(challenge.id)})

    submission = Submission(
        team_id=team_id,
        challenge_id=challenge.id,
        user_id=user.id,
        submitted_hash=hash_submission(secret, payload.flag),
        correct=correct,
        idempotency_key=payload.idempotency_key,
        ip_hash=hash_ip(secret, client_ip),
    )
    db.add(submission)
    try:
        db.flush()
        solved_at = None
        if correct:
            solve = Solve(
                team_id=team_id,
                challenge_id=challenge.id,
                user_id=user.id,
                submission_id=submission.id,
            )
            db.add(solve)
            db.flush()
            score_event = ScoreEvent(
                event_id=event.id,
                team_id=team_id,
                challenge_id=challenge.id,
                solve_id=solve.id,
                kind="solve",
                points=0,
            )
            db.add(score_event)
            db.flush()
            solve_count = (
                db.scalar(select(func.count()).select_from(Solve).where(Solve.challenge_id == challenge.id))
                or 0
            )
            points = challenge_points(challenge, solve_count)
            score_event.points = points
            submission.awarded_points = points
            solved_at = solve.solved_at
            add_audit(
                db,
                user.id,
                "challenge.solved",
                "challenge",
                challenge.id,
                {"team_id": str(team_id)},
            )
            add_outbox(
                db,
                "challenge.solved",
                "challenge",
                challenge.id,
                {
                    "event_id": str(event.id),
                    "challenge_id": str(challenge.id),
                    "team_id": str(team_id),
                    "points": points,
                },
            )
        # Re-evaluate wall-clock start/end gates immediately before commit. The
        # locked event row prevents an admin state transition from overtaking us.
        if not state_allows_submission(event):
            db.rollback()
            raise ApiError(409, "submissions_closed", "현재 플래그를 제출할 수 없습니다.")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raced = db.scalar(select(Solve).where(Solve.team_id == team_id, Solve.challenge_id == challenge_id))
        if raced is not None:
            return {
                "correct": True,
                "message": "이미 해결한 문제입니다.",
                "awarded_points": 0,
                "solved_at": iso(raced.solved_at),
            }
        repeated = db.scalar(
            select(Submission).where(
                Submission.team_id == team_id,
                Submission.challenge_id == challenge_id,
                Submission.idempotency_key == payload.idempotency_key,
            )
        )
        if repeated is not None:
            return _submission_result(db, repeated)
        raise ApiError(409, "submission_conflict", "제출 상태가 변경되었습니다. 다시 확인하세요.") from exc

    if correct:
        try:
            request.app.state.store.increment(f"scoreboard:{event.id}:generation")
        except Exception:
            logger.exception("scoreboard_generation_increment_failed")
        for phase in ("live", "frozen", "final"):
            try:
                request.app.state.store.delete(f"scoreboard:{event.id}:public:{phase}")
            except Exception:
                logger.exception("scoreboard_cache_invalidation_failed")
    return {
        "correct": correct,
        "message": "정답입니다." if correct else "정답이 아닙니다.",
        "awarded_points": submission.awarded_points,
        "solved_at": iso(solved_at),
    }


@router.get("/scoreboard")
def scoreboard(request: Request, db: Session = Depends(get_db)) -> dict:
    event = current_event(db)
    if event is None:
        raise ApiError(404, "event_not_found", "현재 이벤트가 없습니다.")
    now = utcnow()
    phase = public_scoreboard_cache_phase(event, now)
    key = f"scoreboard:{event.id}:public:{phase}"
    generation_key = f"scoreboard:{event.id}:generation"
    lease_key = f"scoreboard:{event.id}:build:{phase}"

    def cached_payload(cached: dict | None, generation: int) -> dict | None:
        if cached is None or cached.get("generation") != generation:
            return None
        payload = cached.get("payload")
        return payload if isinstance(payload, dict) else None

    try:
        generation = request.app.state.store.get_counter(generation_key)
        cached = request.app.state.store.get_json(key)
        payload = cached_payload(cached, generation)
        if payload is not None:
            return payload
        lease_token = request.app.state.store.acquire_lease(lease_key, SCOREBOARD_BUILD_LEASE_SECONDS)
    except Exception as exc:
        logger.exception("scoreboard_cache_read_failed")
        raise ApiError(503, "scoreboard_unavailable", "점수판 캐시를 사용할 수 없습니다.") from exc

    if lease_token is None:
        try:
            generation = request.app.state.store.get_counter(generation_key)
            payload = cached_payload(request.app.state.store.get_json(key), generation)
        except Exception as exc:
            logger.exception("scoreboard_cache_reread_failed")
            raise ApiError(503, "scoreboard_unavailable", "점수판 캐시를 사용할 수 없습니다.") from exc
        if payload is not None:
            return payload
        raise ApiError(
            503,
            "scoreboard_busy",
            "점수판을 집계하고 있습니다. 잠시 후 다시 시도하세요.",
            {"Retry-After": "1"},
        )

    try:
        # Another request may have published while this request acquired the
        # lease. Re-read before doing any expensive database aggregation.
        generation = request.app.state.store.get_counter(generation_key)
        payload = cached_payload(request.app.state.store.get_json(key), generation)
        if payload is not None:
            return payload
        result = public_scoreboard(db, event, now)
        if request.app.state.store.get_counter(generation_key) != generation:
            raise ApiError(
                503,
                "scoreboard_changed",
                "점수판이 변경되었습니다. 다시 시도하세요.",
                {"Retry-After": "1"},
            )
        request.app.state.store.set_json(
            key,
            {"generation": generation, "payload": result},
            settings_from(request).scoreboard_cache_seconds,
        )
        return result
    except ApiError:
        raise
    except Exception as exc:
        logger.exception("scoreboard_cache_or_build_failed")
        raise ApiError(503, "scoreboard_unavailable", "점수판을 집계할 수 없습니다.") from exc
    finally:
        try:
            request.app.state.store.release_lease(lease_key, lease_token)
        except Exception:
            logger.exception("scoreboard_lease_release_failed")
