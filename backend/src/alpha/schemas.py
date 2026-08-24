from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from .limits import MAX_CHALLENGE_ATTEMPTS


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=128)
    access_code: str | None = Field(default=None, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)

    @model_validator(mode="after")
    def password_must_change(self) -> ChangePasswordRequest:
        if self.current_password == self.new_password:
            raise ValueError("new password must differ")
        return self


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value):
            raise ValueError("team name contains forbidden control or formatting characters")
        return " ".join(value.split())


class TeamJoinRequest(BaseModel):
    invite_code: str = Field(min_length=16, max_length=128)


class TeamMemberRequest(BaseModel):
    user_id: uuid.UUID


class SubmitRequest(BaseModel):
    flag: str = Field(min_length=1, max_length=4096)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


class EventUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[a-z0-9-]+$")
    description_md: str | None = Field(default=None, max_length=100_000)
    state: Literal["draft", "registration", "live", "frozen", "ended", "archived"] | None = None
    registration_at: datetime | None = None
    start_at: datetime | None = None
    freeze_at: datetime | None = None
    end_at: datetime | None = None
    team_mode: Literal["team", "individual"] | None = None
    registration_access_mode: Literal["open", "code"] | None = None

    @field_validator("registration_at", "start_at", "freeze_at", "end_at")
    @classmethod
    def event_times_need_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timezone offset is required")
        return value


class RegistrationCodeCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    max_uses: int | None = Field(default=None, ge=1, le=10_000)
    expires_at: datetime | None = None

    @field_validator("label", mode="before")
    @classmethod
    def clean_label(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value):
            raise ValueError("registration-code label contains forbidden characters")
        return " ".join(value.split())

    @field_validator("expires_at")
    @classmethod
    def expiration_needs_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timezone offset is required")
        return value


class UserStatusUpdateRequest(BaseModel):
    active: bool
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def clean_reason(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value):
            raise ValueError("moderation reason contains forbidden characters")
        return " ".join(value.split())

    @model_validator(mode="after")
    def suspension_requires_reason(self) -> UserStatusUpdateRequest:
        if not self.active and not self.reason:
            raise ValueError("a reason is required when suspending a participant")
        return self


class FlagInput(BaseModel):
    type: Literal["exact", "regex"]
    value: str = Field(min_length=1, max_length=4096)


class ChallengeCreateRequest(BaseModel):
    slug: str = Field(min_length=2, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    title: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    description_md: str = Field(min_length=1, max_length=100_000)
    connection_info: str | None = Field(default=None, max_length=2000)
    scoring_type: Literal["fixed", "dynamic"] = "fixed"
    initial_points: int = Field(default=100, gt=0, le=1_000_000)
    minimum_points: int = Field(default=100, gt=0, le=1_000_000)
    decay: int = Field(default=20, gt=0, le=1_000_000)
    visible: bool = False
    visible_at: datetime | None = None
    max_attempts: int = Field(default=0, ge=0, le=MAX_CHALLENGE_ATTEMPTS)
    prerequisite_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    flag: FlagInput

    @model_validator(mode="after")
    def validate_scoring(self) -> ChallengeCreateRequest:
        if self.minimum_points > self.initial_points:
            raise ValueError("minimum_points cannot exceed initial_points")
        return self

    @field_validator("visible_at")
    @classmethod
    def visible_time_needs_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timezone offset is required")
        return value


class ChallengeUpdateRequest(BaseModel):
    slug: str | None = Field(default=None, min_length=2, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    title: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    description_md: str | None = Field(default=None, min_length=1, max_length=100_000)
    connection_info: str | None = Field(default=None, max_length=2000)
    scoring_type: Literal["fixed", "dynamic"] | None = None
    initial_points: int | None = Field(default=None, gt=0, le=1_000_000)
    minimum_points: int | None = Field(default=None, gt=0, le=1_000_000)
    decay: int | None = Field(default=None, gt=0, le=1_000_000)
    visible: bool | None = None
    visible_at: datetime | None = None
    max_attempts: int | None = Field(default=None, ge=0, le=MAX_CHALLENGE_ATTEMPTS)
    prerequisite_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
    flag: FlagInput | None = None

    @field_validator("visible_at")
    @classmethod
    def visible_time_needs_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timezone offset is required")
        return value


class VisibilityRequest(BaseModel):
    visible: bool


class AnnouncementCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body_md: str = Field(min_length=1, max_length=100_000)
    publish_at: datetime | None = None

    @field_validator("publish_at")
    @classmethod
    def publish_time_needs_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timezone offset is required")
        return value


class AnnouncementUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body_md: str | None = Field(default=None, min_length=1, max_length=100_000)
    publish_at: datetime | None = None

    @field_validator("publish_at")
    @classmethod
    def publish_time_needs_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timezone offset is required")
        return value
