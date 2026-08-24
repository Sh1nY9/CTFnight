from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

import redis


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int = 0


class EphemeralStore(Protocol):
    def ping(self) -> bool: ...

    def check_rate(self, key: str, limit: int, window_seconds: int) -> RateLimitResult: ...

    def get_json(self, key: str) -> dict[str, Any] | None: ...

    def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None: ...

    def delete(self, key: str) -> None: ...

    def get_counter(self, key: str) -> int: ...

    def increment(self, key: str) -> int: ...

    def acquire_lease(self, key: str, ttl_seconds: int) -> str | None: ...

    def release_lease(self, key: str, token: str) -> bool: ...

    def close(self) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self._values: dict[str, tuple[float, Any]] = {}
        self._counters: dict[str, int] = {}
        self._leases: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def ping(self) -> bool:
        return True

    def check_rate(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = time.monotonic()
        with self._lock:
            expires, count = self._values.get(key, (now + window_seconds, 0))
            if expires <= now:
                expires, count = now + window_seconds, 0
            count += 1
            self._values[key] = (expires, count)
            if count > limit:
                return RateLimitResult(False, max(1, int(expires - now) + 1))
            return RateLimitResult(True)

    def get_json(self, key: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            item = self._values.get(key)
            if not item:
                return None
            expires, value = item
            if expires <= now:
                self._values.pop(key, None)
                return None
            return value if isinstance(value, dict) else None

    def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        with self._lock:
            self._values[key] = (time.monotonic() + ttl_seconds, value)

    def delete(self, key: str) -> None:
        with self._lock:
            self._values.pop(key, None)

    def get_counter(self, key: str) -> int:
        with self._lock:
            return self._counters.get(key, 0)

    def increment(self, key: str) -> int:
        with self._lock:
            value = self._counters.get(key, 0) + 1
            self._counters[key] = value
            return value

    def acquire_lease(self, key: str, ttl_seconds: int) -> str | None:
        if ttl_seconds <= 0:
            raise ValueError("lease ttl must be positive")
        now = time.monotonic()
        token = secrets.token_urlsafe(24)
        with self._lock:
            current = self._leases.get(key)
            if current is not None and current[0] > now:
                return None
            self._leases[key] = (now + ttl_seconds, token)
            return token

    def release_lease(self, key: str, token: str) -> bool:
        with self._lock:
            current = self._leases.get(key)
            if current is None or current[1] != token:
                return False
            self._leases.pop(key, None)
            return True

    def close(self) -> None:
        with self._lock:
            self._values.clear()
            self._counters.clear()
            self._leases.clear()


class RedisStore:
    _rate_script = """
    local count = redis.call('INCR', KEYS[1])
    if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[2]) end
    local ttl = redis.call('TTL', KEYS[1])
    if count > tonumber(ARGV[1]) then return {0, ttl} end
    return {1, ttl}
    """
    _release_lease_script = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('DEL', KEYS[1])
    end
    return 0
    """

    def __init__(self, url: str) -> None:
        self.client = redis.Redis.from_url(
            url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
        )

    def ping(self) -> bool:
        return bool(self.client.ping())

    def check_rate(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        allowed, ttl = self.client.eval(self._rate_script, 1, key, limit, window_seconds)
        return RateLimitResult(bool(allowed), max(0, int(ttl)))

    def get_json(self, key: str) -> dict[str, Any] | None:
        raw = self.client.get(key)
        if raw is None:
            return None
        value = json.loads(raw)
        return value if isinstance(value, dict) else None

    def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        if ttl_seconds > 0:
            self.client.setex(key, ttl_seconds, json.dumps(value, separators=(",", ":")))

    def delete(self, key: str) -> None:
        self.client.delete(key)

    def get_counter(self, key: str) -> int:
        raw = self.client.get(key)
        if raw is None:
            return 0
        value = int(raw)
        if value < 0:
            raise ValueError("counter cannot be negative")
        return value

    def increment(self, key: str) -> int:
        return int(self.client.incr(key))

    def acquire_lease(self, key: str, ttl_seconds: int) -> str | None:
        if ttl_seconds <= 0:
            raise ValueError("lease ttl must be positive")
        token = secrets.token_urlsafe(24)
        acquired = self.client.set(key, token, nx=True, ex=ttl_seconds)
        return token if acquired else None

    def release_lease(self, key: str, token: str) -> bool:
        return bool(self.client.eval(self._release_lease_script, 1, key, token))

    def close(self) -> None:
        self.client.close()


def create_store(url: str) -> EphemeralStore:
    if url == "memory://":
        return MemoryStore()
    return RedisStore(url)
