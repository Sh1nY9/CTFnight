from __future__ import annotations

import json
import os
import stat
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import EmailStr, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL

_MAX_SECRET_FILE_BYTES = 16_384


def _read_secret_file(path_value: str | Path, *, allow_empty: bool = False) -> str:
    """Read one UTF-8 secret line without following a final-path symlink."""

    path = Path(path_value)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open secret file: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"secret path is not a regular file: {path}")
        if metadata.st_size > _MAX_SECRET_FILE_BYTES:
            raise ValueError(f"secret file exceeds {_MAX_SECRET_FILE_BYTES} bytes: {path}")
        raw = os.read(descriptor, _MAX_SECRET_FILE_BYTES + 1)
        if len(raw) > _MAX_SECRET_FILE_BYTES or os.read(descriptor, 1):
            raise ValueError(f"secret file exceeds {_MAX_SECRET_FILE_BYTES} bytes: {path}")
    finally:
        os.close(descriptor)

    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"secret file must contain UTF-8 text: {path}") from exc
    if value.endswith("\n"):
        value = value[:-1]
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError(f"secret file must contain exactly one line: {path}")
    if not value and not allow_empty:
        raise ValueError(f"secret file cannot be empty: {path}")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ALPHA_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = Field(default="sqlite:///./alpha.db", repr=False)
    database_host: str | None = None
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str | None = None
    database_user: str | None = None
    database_password_file: Path | None = None
    database_tls: bool = False
    redis_url: str = Field(default="memory://", repr=False)
    redis_host: str | None = None
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_password_file: Path | None = None
    redis_tls: bool = False
    secret_key: SecretStr = SecretStr("development-only-change-this-secret")
    secret_key_file: Path | None = None
    cookie_secure: bool | None = None
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost"])
    trusted_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )
    forwarded_allow_ips: str = "127.0.0.1"

    session_ttl_hours: int = Field(default=24 * 7, ge=1, le=24 * 90)
    session_cleanup_batch_size: int = Field(default=100, ge=1, le=1000)
    csrf_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    max_request_body_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    submission_rate_limit: int = Field(default=10, ge=1, le=1000)
    submission_ip_rate_limit: int = Field(default=60, ge=1, le=10_000)
    submission_challenge_rate_limit: int = Field(default=1000, ge=1, le=100_000)
    submission_rate_window_seconds: int = Field(default=10, ge=1, le=3600)
    team_mutation_rate_limit: int = Field(default=20, ge=1, le=1000)
    team_mutation_ip_rate_limit: int = Field(default=100, ge=1, le=10_000)
    team_mutation_rate_window_seconds: int = Field(default=3600, ge=60, le=86_400)
    auth_rate_limit: int = Field(default=20, ge=1, le=1000)
    auth_ip_rate_limit: int = Field(default=200, ge=1, le=10_000)
    auth_rate_window_seconds: int = Field(default=60, ge=1, le=3600)
    registration_global_rate_limit: int = Field(default=1000, ge=1, le=100_000)
    registration_global_rate_window_seconds: int = Field(default=60, ge=1, le=3600)
    scoreboard_cache_seconds: int = Field(default=2, ge=1, le=60)
    max_flag_length: int = Field(default=512, ge=16, le=4096)
    regex_timeout_seconds: float = Field(default=0.05, ge=0.005, le=1.0)

    admin_email: EmailStr | None = None
    admin_username: str = Field(default="admin", min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    admin_password: SecretStr | None = None
    admin_password_file: Path | None = None
    seed_demo: bool = False

    @model_validator(mode="before")
    @classmethod
    def resolve_file_backed_secrets(cls, raw_values: Any) -> Any:
        if not isinstance(raw_values, dict):
            return raw_values
        values = dict(raw_values)

        secret_key_file = values.get("secret_key_file")
        if secret_key_file:
            values["secret_key"] = _read_secret_file(secret_key_file)

        admin_password_file = values.get("admin_password_file")
        if admin_password_file:
            admin_password = _read_secret_file(admin_password_file, allow_empty=True)
            values["admin_password"] = admin_password or None

        database_components = (
            "database_host",
            "database_name",
            "database_user",
            "database_password_file",
        )
        if any(values.get(key) not in (None, "") for key in database_components):
            missing = [key for key in database_components if values.get(key) in (None, "")]
            if missing:
                raise ValueError(f"incomplete database component configuration: {', '.join(missing)}")
            database_password = _read_secret_file(values["database_password_file"])
            query = {"sslmode": "verify-full" if values.get("database_tls", False) else "disable"}
            values["database_url"] = URL.create(
                "postgresql+psycopg",
                username=str(values["database_user"]),
                password=database_password,
                host=str(values["database_host"]),
                port=int(values.get("database_port", 5432)),
                database=str(values["database_name"]),
                query=query,
            ).render_as_string(hide_password=False)

        redis_components = ("redis_host", "redis_password_file")
        if any(values.get(key) not in (None, "") for key in redis_components):
            missing = [key for key in redis_components if values.get(key) in (None, "")]
            if missing:
                raise ValueError(f"incomplete Redis component configuration: {', '.join(missing)}")
            redis_password = _read_secret_file(values["redis_password_file"])
            redis_scheme = "rediss" if values.get("redis_tls", False) else "redis"
            values["redis_url"] = URL.create(
                redis_scheme,
                username="",
                password=redis_password,
                host=str(values["redis_host"]),
                port=int(values.get("redis_port", 6379)),
                database="0",
            ).render_as_string(hide_password=False)
        return values

    @field_validator("allowed_origins", "trusted_hosts", mode="before")
    @classmethod
    def split_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            if value.lstrip().startswith("["):
                return json.loads(value)
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        for value in values:
            parsed = urlsplit(value)
            try:
                _ = parsed.port
            except ValueError as exc:
                raise ValueError("ALPHA_ALLOWED_ORIGINS contains an invalid port") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("ALPHA_ALLOWED_ORIGINS entries must be HTTP(S) origins without paths")
        return values

    @field_validator("environment", mode="before")
    @classmethod
    def normalize_environment(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("admin_password", mode="before")
    @classmethod
    def normalize_empty_admin_password(cls, value: Any) -> Any:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(raw, str) and not raw.strip():
            return None
        return value

    @field_validator("admin_password")
    @classmethod
    def validate_admin_password_length(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 12:
            raise ValueError("ALPHA_ADMIN_PASSWORD must contain at least 12 characters")
        return value

    @model_validator(mode="after")
    def validate_production_secret(self) -> Settings:
        secret = self.secret_key.get_secret_value()
        if self.environment == "production" and (
            len(secret) < 32 or secret == "development-only-change-this-secret"  # noqa: S105 - rejected in production
        ):
            raise ValueError(
                "ALPHA_SECRET_KEY must be a non-default secret with at least 32 characters in production"
            )
        if self.environment == "production" and not self.database_url.startswith(
            ("postgresql://", "postgresql+psycopg://")
        ):
            raise ValueError("ALPHA_DATABASE_URL must use PostgreSQL in production")
        if self.environment == "production" and self.redis_url == "memory://":
            raise ValueError("ALPHA_REDIS_URL must use Redis in production")
        if self.environment == "production" and not self.redis_url.startswith(("redis://", "rediss://")):
            raise ValueError("ALPHA_REDIS_URL must use the redis or rediss scheme in production")
        if self.environment == "production":
            required_files = {
                "ALPHA_SECRET_KEY_FILE": self.secret_key_file,
                "ALPHA_DATABASE_PASSWORD_FILE": self.database_password_file,
                "ALPHA_REDIS_PASSWORD_FILE": self.redis_password_file,
            }
            missing_files = [name for name, value in required_files.items() if value is None]
            if missing_files:
                raise ValueError("production secrets must be file-backed: " + ", ".join(missing_files))
            if not self.database_tls and self.database_host != "postgres":
                raise ValueError(
                    "external PostgreSQL connections require ALPHA_DATABASE_TLS=true; "
                    "plaintext is allowed only for the isolated Compose service named postgres"
                )
            if not self.redis_tls and self.redis_host != "redis":
                raise ValueError(
                    "external Redis connections require ALPHA_REDIS_TLS=true; "
                    "plaintext is allowed only for the isolated Compose service named redis"
                )
        if "*" in self.allowed_origins:
            raise ValueError(
                "ALPHA_ALLOWED_ORIGINS cannot contain '*' when credentialed requests are enabled"
            )
        if self.environment == "production" and any("*" in host for host in self.trusted_hosts):
            raise ValueError("ALPHA_TRUSTED_HOSTS cannot contain wildcards in production")
        if self.environment == "production" and any(
            not origin.startswith("https://") for origin in self.allowed_origins
        ):
            raise ValueError("ALPHA_ALLOWED_ORIGINS must use HTTPS in production")
        if self.environment == "production" and self.cookie_secure is False:
            raise ValueError("ALPHA_COOKIE_SECURE cannot be false in production")
        return self

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.environment == "production"

    @property
    def session_cookie_name(self) -> str:
        return "__Host-alpha_session" if self.environment == "production" else "alpha_session"

    @property
    def csrf_cookie_name(self) -> str:
        return "__Host-alpha_csrf" if self.environment == "production" else "alpha_csrf"

    @property
    def browser_cookie_name(self) -> str:
        return "__Host-alpha_browser" if self.environment == "production" else "alpha_browser"


@lru_cache
def get_settings() -> Settings:
    return Settings()
