from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from dataclasses import dataclass

import regex
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_argon2_slots = threading.BoundedSemaphore(value=2)


class PasswordWorkBusy(RuntimeError):
    """Raised immediately when the bounded Argon2 workers are saturated."""


def _acquire_argon2_slot() -> None:
    # Never queue inside an AnyIO worker thread: enough blocked authentication
    # calls could otherwise consume the entire shared thread pool and starve
    # unrelated synchronous endpoints. The API maps this to a retryable 503.
    if not _argon2_slots.acquire(blocking=False):
        raise PasswordWorkBusy


def hash_password(password: str) -> str:
    # Bound process-wide Argon2 memory to roughly 2 x 64 MiB while failing fast
    # when saturated instead of occupying more request-worker threads.
    _acquire_argon2_slot()
    try:
        return _password_hasher.hash(password)
    finally:
        _argon2_slots.release()


def verify_password(password_hash: str, password: str) -> bool:
    _acquire_argon2_slot()
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False
    finally:
        _argon2_slots.release()


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def keyed_hash(secret: str, domain: str, value: str) -> str:
    message = f"{domain}\0{value}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def hash_session(secret: str, token: str) -> str:
    return keyed_hash(secret, "session", token)


def hash_invite(secret: str, invite: str) -> str:
    return keyed_hash(secret, "invite", invite)


def hash_registration_access(secret: str, access_code: str) -> str:
    return keyed_hash(secret, "registration-access", access_code)


def hash_flag(secret: str, flag: str) -> str:
    return keyed_hash(secret, "flag", flag)


def hash_submission(secret: str, flag: str) -> str:
    return keyed_hash(secret, "submission", flag)


def hash_ip(secret: str, ip: str) -> str:
    return keyed_hash(secret, "ip", ip)


def compare_exact_flag(secret: str, stored_hash: str | None, candidate: str) -> bool:
    if not stored_hash:
        return False
    return hmac.compare_digest(stored_hash, hash_flag(secret, candidate))


@dataclass(frozen=True)
class RegexResult:
    matched: bool
    timed_out: bool = False


def validate_regex(pattern: str) -> None:
    regex.compile(pattern)


def compare_regex_flag(pattern: str | None, candidate: str, timeout: float) -> RegexResult:
    if not pattern:
        return RegexResult(False)
    try:
        return RegexResult(regex.fullmatch(pattern, candidate, timeout=timeout) is not None)
    except TimeoutError:
        return RegexResult(False, timed_out=True)
    except regex.error:
        return RegexResult(False)


def issue_csrf(secret: str, context: str = "") -> str:
    serializer = URLSafeTimedSerializer(secret, salt="alpha-csrf-v1")
    return serializer.dumps({"nonce": random_token(24), "context": context})


def verify_csrf(secret: str, token: str, max_age: int, context: str = "") -> bool:
    serializer = URLSafeTimedSerializer(secret, salt="alpha-csrf-v1")
    try:
        value = serializer.loads(token, max_age=max_age)
        return (
            isinstance(value, dict)
            and isinstance(value.get("nonce"), str)
            and len(value["nonce"]) >= 24
            and isinstance(value.get("context"), str)
            and hmac.compare_digest(value["context"], context)
        )
    except (BadSignature, SignatureExpired):
        return False


def csrf_context(secret: str, *, session_token: str = "", browser_token: str = "") -> str:
    if session_token:
        return keyed_hash(secret, "csrf-session", session_token)
    if browser_token:
        return keyed_hash(secret, "csrf-browser", browser_token)
    return ""
