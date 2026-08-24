#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

"$script_dir/compose.sh" exec -T backend python - <<'PY'
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import psycopg
from psycopg import errors
from sqlalchemy.engine import make_url

from alpha.config import Settings


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


settings = Settings()
url = make_url(settings.database_url)
expected_runtime_role = os.environ.get("ALPHA_DATABASE_USER")
require(expected_runtime_role == "alpha_app", "backend database role is not alpha_app")

connection = psycopg.connect(
    host=url.host,
    port=url.port,
    dbname=url.database,
    user=url.username,
    password=url.password,
    sslmode=dict(url.query).get("sslmode", "disable"),
)
try:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT rolname, rolsuper, rolcreatedb, rolcreaterole,
                   rolreplication, rolbypassrls, rolinherit
            FROM pg_roles
            WHERE rolname = current_user
            """
        )
        require(
            cursor.fetchone() == ("alpha_app", False, False, False, False, False, False),
            "runtime role attributes exceed the canonical boundary",
        )
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_auth_members membership
                JOIN pg_roles member ON member.oid = membership.member
                WHERE member.rolname = current_user
            )
            """
        )
        require(cursor.fetchone() == (False,), "runtime role retains a role membership")
        cursor.execute(
            """
            SELECT database_owner.rolname, schema_owner.rolname
            FROM pg_database database
            JOIN pg_roles database_owner ON database_owner.oid = database.datdba
            JOIN pg_namespace namespace ON namespace.nspname = 'public'
            JOIN pg_roles schema_owner ON schema_owner.oid = namespace.nspowner
            WHERE database.datname = current_database()
            """
        )
        require(
            cursor.fetchone() == ("alpha_migrator", "alpha_migrator"),
            "database and public schema must be owned by alpha_migrator",
        )
        cursor.execute(
            """
            SELECT count(*)
            FROM pg_class relation
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            JOIN pg_roles owner ON owner.oid = relation.relowner
            WHERE namespace.nspname = 'public'
              AND relation.relkind IN ('r', 'p', 'S', 'v', 'm', 'f', 'c')
              AND owner.rolname <> 'alpha_migrator'
            """
        )
        require(cursor.fetchone() == (0,), "a public application object has an unexpected owner")
        cursor.execute(
            """
            SELECT
              has_database_privilege(current_user, current_database(), 'CONNECT'),
              has_database_privilege(current_user, current_database(), 'TEMP'),
              has_schema_privilege(current_user, 'public', 'USAGE'),
              has_schema_privilege(current_user, 'public', 'CREATE')
            """
        )
        require(cursor.fetchone() == (True, False, True, False), "runtime database/schema grants differ")
        cursor.execute(
            """
            SELECT
              has_table_privilege(current_user, 'public.announcements', 'SELECT'),
              has_table_privilege(current_user, 'public.announcements', 'INSERT'),
              has_table_privilege(current_user, 'public.announcements', 'UPDATE'),
              has_table_privilege(current_user, 'public.announcements', 'DELETE'),
              has_table_privilege(current_user, 'public.announcements', 'TRUNCATE'),
              has_table_privilege(current_user, 'public.announcements', 'TRIGGER')
            """
        )
        require(cursor.fetchone() == (True, True, True, True, False, False), "runtime table grants differ")

        cursor.execute("SELECT id FROM public.events ORDER BY created_at LIMIT 1")
        event_row = cursor.fetchone()
        require(event_row is not None, "bootstrap event is missing")
        probe_id = uuid.uuid4()
        now = datetime.now(UTC)
        cursor.execute(
            """
            INSERT INTO public.announcements
              (id, event_id, title, body_md, publish_at, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (probe_id, event_row[0], "privilege-probe", "probe", now, now, now),
        )
        cursor.execute("SELECT title FROM public.announcements WHERE id = %s", (probe_id,))
        require(cursor.fetchone() == ("privilege-probe",), "runtime SELECT/INSERT probe failed")
        cursor.execute(
            "UPDATE public.announcements SET body_md = %s WHERE id = %s",
            ("updated", probe_id),
        )
        require(cursor.rowcount == 1, "runtime UPDATE probe failed")
        cursor.execute("DELETE FROM public.announcements WHERE id = %s", (probe_id,))
        require(cursor.rowcount == 1, "runtime DELETE probe failed")
    connection.rollback()

    denied_statements = (
        "CREATE TABLE public.alpha_runtime_privilege_probe (id integer)",
        "CREATE SCHEMA alpha_runtime_privilege_probe",
        "CREATE TEMP TABLE alpha_runtime_privilege_probe (id integer)",
        "ALTER TABLE public.announcements ADD COLUMN alpha_runtime_privilege_probe integer",
        "TRUNCATE TABLE public.announcements",
        "DROP TABLE public.announcements",
        "SET ROLE alpha_migrator",
    )
    for statement in denied_statements:
        try:
            with connection.cursor() as cursor:
                cursor.execute(statement)
        except errors.InsufficientPrivilege:
            connection.rollback()
        except psycopg.Error as exc:
            connection.rollback()
            raise RuntimeError(
                f"unexpected SQLSTATE for denied privilege probe: {exc.sqlstate}"
            ) from exc
        else:
            connection.rollback()
            raise RuntimeError("runtime role unexpectedly executed a privileged statement")
finally:
    connection.close()

print("PostgreSQL runtime CRUD/minimum-privilege verification passed")
PY
