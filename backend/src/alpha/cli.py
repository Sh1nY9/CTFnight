from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, ValidationInfo, field_validator, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError

from .config import Settings
from .db import Database
from .errors import ApiError
from .limits import MAX_CHALLENGE_ATTEMPTS
from .models import Announcement, Challenge, Event, SessionToken, User, utcnow
from .schemas import FlagInput
from .security import hash_password
from .services import (
    add_audit,
    add_outbox,
    apply_flag,
    assert_event_mutable,
    assert_scoring_mutable,
    load_prerequisites,
)

CHALLENGE_SOURCE_MAX_CHARS = 1_000_000


class ImportScoring(BaseModel):
    type: Literal["fixed", "dynamic"]
    initial: int = Field(gt=0, le=1_000_000)
    minimum: int | None = Field(default=None, gt=0, le=1_000_000)
    decay: int | None = Field(default=None, gt=0, le=1_000_000)

    @model_validator(mode="after")
    def dynamic_fields_are_explicit(self) -> ImportScoring:
        if self.type == "dynamic" and (self.minimum is None or self.decay is None):
            raise ValueError("dynamic scoring requires minimum and decay")
        return self


class ChallengeFile(BaseModel):
    event_slug: str = Field(min_length=2, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    slug: str = Field(min_length=2, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    title: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    description_md: str = Field(min_length=1, max_length=100_000)
    connection_info: str | None = Field(default=None, max_length=2000)
    scoring: ImportScoring
    max_attempts: int = Field(default=0, ge=0, le=MAX_CHALLENGE_ATTEMPTS)
    visible: bool = False
    visible_at: datetime | None = None
    flag: FlagInput
    prerequisites: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("visible_at")
    @classmethod
    def visible_time_needs_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timezone offset is required")
        return value

    @field_validator("prerequisites")
    @classmethod
    def prerequisites_match_schema(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("prerequisites must be unique")
        if any(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", item) is None for item in value):
            raise ValueError("prerequisite slug is invalid")
        return value

    @model_validator(mode="after")
    def flag_respects_runtime_limit(self, info: ValidationInfo) -> ChallengeFile:
        max_length = (info.context or {}).get("max_flag_length", 4096)
        if len(self.flag.value) > max_length:
            raise ValueError(f"flag exceeds configured maximum length of {max_length}")
        return self


def _bootstrap(settings: Settings, database: Database) -> None:
    with database.session_factory() as db:
        event = db.scalar(select(Event).order_by(Event.created_at.desc()).limit(1))
        if event is None:
            event = Event(
                name="CTFnight",
                slug="ctfnight",
                description_md="CTFnight에 오신 것을 환영합니다.",
                state="draft",
                team_mode="team",
            )
            db.add(event)
            db.flush()
            add_audit(db, None, "event.bootstrapped", "event", event.id)

        existing_admin = db.scalar(select(User.id).where(User.role == "admin").limit(1))
        admin_password = (
            settings.admin_password.get_secret_value() if settings.admin_password is not None else None
        )
        if admin_password is not None and len(admin_password) < 12:
            raise RuntimeError("ALPHA_ADMIN_PASSWORD must contain at least 12 characters")
        if admin_password and not settings.admin_email:
            raise RuntimeError("ALPHA_ADMIN_PASSWORD requires ALPHA_ADMIN_EMAIL")
        if (
            settings.environment == "production"
            and existing_admin is None
            and not (settings.admin_email and admin_password)
        ):
            raise RuntimeError(
                "initial production bootstrap requires ALPHA_ADMIN_EMAIL and ALPHA_ADMIN_PASSWORD"
            )
        if settings.admin_email:
            email = settings.admin_email.strip().lower()
            admin = db.scalar(select(User).where(func.lower(User.email) == email))
            if admin is not None and admin.role != "admin":
                raise RuntimeError("ALPHA_ADMIN_EMAIL belongs to a non-admin account")
            if admin is None:
                if not admin_password:
                    raise RuntimeError(
                        "ALPHA_ADMIN_PASSWORD is required when the configured admin does not exist"
                    )
                username = settings.admin_username.strip()
                if db.scalar(select(User.id).where(func.lower(User.username) == username.lower())):
                    raise RuntimeError("ALPHA_ADMIN_USERNAME is already in use")
                admin = User(
                    email=email,
                    username=username,
                    password_hash=hash_password(admin_password),
                    role="admin",
                    password_change_required=True,
                )
                db.add(admin)
                db.flush()
                add_audit(db, admin.id, "admin.bootstrapped", "user", admin.id)
                add_outbox(db, "admin.bootstrapped", "user", admin.id, {"user_id": str(admin.id)})

        if settings.seed_demo and event.state != "archived":
            _seed_demo(db, event, settings)
        db.commit()


def _seed_demo(db, event: Event, settings: Settings) -> None:
    challenges = [
        {
            "slug": "welcome",
            "title": "Welcome",
            "category": "Misc",
            "description_md": "플래그 제출 흐름을 확인하는 첫 문제입니다.",
            "scoring_type": "fixed",
            "initial_points": 100,
            "minimum_points": 100,
            "decay": 20,
            "flag": FlagInput(type="exact", value="FLAG{welcome-to-ctfnight}"),
        },
        {
            "slug": "regex-demo",
            "title": "Regex Demo",
            "category": "Misc",
            "description_md": "정규식 플래그와 동적 점수를 확인하는 데모 문제입니다.",
            "scoring_type": "dynamic",
            "initial_points": 500,
            "minimum_points": 100,
            "decay": 20,
            "flag": FlagInput(type="regex", value=r"^FLAG\{regex-[0-9]{4}\}$"),
        },
    ]
    for values in challenges:
        challenge = db.scalar(
            select(Challenge).where(Challenge.event_id == event.id, Challenge.slug == values["slug"])
        )
        if challenge is not None:
            continue
        flag = values.pop("flag")
        challenge = Challenge(event_id=event.id, visible=True, max_attempts=0, **values)
        apply_flag(
            challenge,
            flag,
            settings.secret_key.get_secret_value(),
            settings.max_flag_length,
        )
        db.add(challenge)
    if not db.scalar(select(Announcement.id).where(Announcement.event_id == event.id)):
        db.add(
            Announcement(
                event_id=event.id,
                title="CTFnight 준비 완료",
                body_md="관리자 계정으로 로그인해 이벤트와 문제를 설정하세요.",
                publish_at=utcnow(),
            )
        )


def _set_password(settings: Settings, database: Database, email: str) -> None:
    if settings.admin_password is None:
        raise RuntimeError("set-password reads the new password from ALPHA_ADMIN_PASSWORD")
    if len(settings.admin_password.get_secret_value()) < 12:
        raise RuntimeError("recovery password must contain at least 12 characters")
    with database.session_factory() as db:
        user = db.scalar(
            select(User)
            .where(func.lower(User.email) == email.strip().lower())
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None:
            raise RuntimeError("user not found")
        user.password_hash = hash_password(settings.admin_password.get_secret_value())
        user.password_change_required = True
        user.credential_version += 1
        db.execute(
            delete(SessionToken)
            .where(SessionToken.user_id == user.id)
            .execution_options(synchronize_session=False)
        )
        add_audit(db, user.id, "admin.password_reset_cli", "user", user.id)
        db.commit()


def _import_challenge(settings: Settings, database: Database, path: Path) -> None:
    try:
        if path == Path("-"):
            source_text = sys.stdin.read(CHALLENGE_SOURCE_MAX_CHARS + 1)
        else:
            with path.open(encoding="utf-8") as source_file:
                source_text = source_file.read(CHALLENGE_SOURCE_MAX_CHARS + 1)
    except UnicodeError as exc:
        raise RuntimeError("challenge YAML must be valid UTF-8") from exc
    if len(source_text) > CHALLENGE_SOURCE_MAX_CHARS:
        raise RuntimeError("challenge YAML exceeds the 1000000 character limit")
    raw = yaml.safe_load(source_text)
    spec = ChallengeFile.model_validate(raw, context={"max_flag_length": settings.max_flag_length})
    with database.session_factory() as db:
        event = db.scalar(select(Event).where(Event.slug == spec.event_slug).with_for_update())
        if event is None:
            raise RuntimeError(f"event not found: {spec.event_slug}")
        assert_event_mutable(event)
        prereq_rows = (
            list(
                db.scalars(
                    select(Challenge).where(
                        Challenge.event_id == event.id,
                        Challenge.slug.in_(spec.prerequisites),
                    )
                )
            )
            if spec.prerequisites
            else []
        )
        if len(prereq_rows) != len(set(spec.prerequisites)):
            raise RuntimeError("one or more prerequisite slugs were not found in the event")
        challenge = db.scalar(
            select(Challenge).where(Challenge.event_id == event.id, Challenge.slug == spec.slug)
        )
        created = challenge is None
        scoring_values = {
            "scoring_type": spec.scoring.type,
            "initial_points": spec.scoring.initial,
            "minimum_points": spec.scoring.minimum or spec.scoring.initial,
            "decay": spec.scoring.decay or 20,
        }
        if challenge is None:
            challenge = Challenge(event_id=event.id, slug=spec.slug)
            db.add(challenge)
        else:
            assert_scoring_mutable(event, challenge, scoring_values)
        challenge.title = spec.title
        challenge.category = spec.category
        challenge.description_md = spec.description_md
        challenge.connection_info = spec.connection_info
        challenge.scoring_type = scoring_values["scoring_type"]
        challenge.initial_points = scoring_values["initial_points"]
        challenge.minimum_points = scoring_values["minimum_points"]
        challenge.decay = scoring_values["decay"]
        challenge.max_attempts = spec.max_attempts
        challenge.visible = spec.visible
        challenge.visible_at = spec.visible_at
        if challenge.minimum_points > challenge.initial_points:
            raise RuntimeError("scoring.minimum cannot exceed scoring.initial")
        apply_flag(
            challenge,
            spec.flag,
            settings.secret_key.get_secret_value(),
            settings.max_flag_length,
        )
        challenge.prerequisites = load_prerequisites(
            db,
            event.id,
            [row.id for row in prereq_rows],
            None if created else challenge.id,
        )
        db.flush()
        action = "challenge.imported" if created else "challenge.reimported"
        add_audit(db, None, action, "challenge", challenge.id, {"source": path.name})
        add_outbox(db, action, "challenge", challenge.id, {"event_id": str(event.id)})
        db.commit()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m alpha.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("bootstrap", help="create the initial event and optional admin/demo data")
    password = subparsers.add_parser("set-password", help="reset a user's password from ALPHA_ADMIN_PASSWORD")
    password.add_argument("--email", required=True)
    importer = subparsers.add_parser(
        "import-challenge", help="upsert a challenge YAML file, or read YAML from stdin with '-'"
    )
    importer.add_argument("path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database: Database | None = None
    try:
        settings = Settings()
        database = Database(settings.database_url)
        if args.command == "bootstrap":
            _bootstrap(settings, database)
        elif args.command == "set-password":
            _set_password(settings, database, args.email)
        elif args.command == "import-challenge":
            _import_challenge(settings, database, args.path)
    except ValidationError as exc:
        safe_errors = [
            {
                "path": ".".join(str(part) for part in item["loc"]),
                "message": item["msg"],
                "type": item["type"],
            }
            for item in exc.errors(include_input=False, include_context=False)
        ]
        print(f"alpha-cli: validation failed: {json.dumps(safe_errors)}", file=sys.stderr)
        return 1
    except ApiError as exc:
        print(f"alpha-cli: {exc.message}", file=sys.stderr)
        return 1
    except yaml.YAMLError as exc:
        # PyYAML's exception string includes the offending source line. A
        # malformed private challenge can contain a plaintext flag there, so
        # report only the sanitized basename and numeric parser position.
        source = json.dumps(getattr(args, "path", Path("challenge.yaml")).name)
        mark = getattr(exc, "problem_mark", None)
        location = ""
        if mark is not None and isinstance(mark.line, int) and isinstance(mark.column, int):
            location = f" at line {mark.line + 1}, column {mark.column + 1}"
        print(f"alpha-cli: invalid YAML in {source}{location}", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        # SQLAlchemy exception strings may embed statement parameters, including
        # a private regex flag. Keep CLI stderr generic and parameter-free.
        print("alpha-cli: database operation failed", file=sys.stderr)
        return 1
    except (RuntimeError, OSError) as exc:
        print(f"alpha-cli: {exc}", file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
