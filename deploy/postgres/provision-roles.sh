#!/bin/sh
set -eu

umask 077

fail() {
  printf 'DB role provisioning 오류: %s\n' "$1" >&2
  exit 1
}

require_identifier() {
  identifier_name=$1
  identifier_value=$2
  case "$identifier_value" in
    ''|*[!A-Za-z0-9_]*) fail "$identifier_name 값은 영문, 숫자, _만 사용할 수 있습니다." ;;
  esac
  [ "${#identifier_value}" -le 63 ] || fail "$identifier_name 값은 63자 이하여야 합니다."
}

read_secret() {
  secret_name=$1
  secret_path=/run/secrets/$secret_name
  [ -f "$secret_path" ] && [ ! -L "$secret_path" ] || \
    fail "일반 secret 파일이 없습니다: $secret_path"
  secret_lines=$(wc -l < "$secret_path" | tr -d ' ')
  [ "$secret_lines" -eq 1 ] || fail "$secret_path 는 정확히 한 줄이어야 합니다."
  secret_value=$(sed -n '1p' "$secret_path")
  [ "${#secret_value}" -ge 24 ] && [ "${#secret_value}" -le 256 ] || \
    fail "$secret_path 길이는 24~256자여야 합니다."
  case "$secret_value" in
    *[!A-Za-z0-9_-]*) fail "$secret_path 는 URL-safe 문자만 사용할 수 있습니다." ;;
  esac
  printf '%s' "$secret_value"
}

command -v psql >/dev/null 2>&1 || fail 'psql client를 찾을 수 없습니다.'

database_name=${ALPHA_POSTGRES_DB:-}
owner_role=${ALPHA_POSTGRES_OWNER_USER:-}
migrator_role=${ALPHA_POSTGRES_MIGRATOR_USER:-}
runtime_role=${ALPHA_POSTGRES_RUNTIME_USER:-}

require_identifier ALPHA_POSTGRES_DB "$database_name"
require_identifier ALPHA_POSTGRES_OWNER_USER "$owner_role"
require_identifier ALPHA_POSTGRES_MIGRATOR_USER "$migrator_role"
require_identifier ALPHA_POSTGRES_RUNTIME_USER "$runtime_role"
[ "$migrator_role" = alpha_migrator ] || fail 'migrator role은 alpha_migrator로 고정됩니다.'
[ "$runtime_role" = alpha_app ] || fail 'runtime role은 alpha_app으로 고정됩니다.'
[ "$owner_role" != "$migrator_role" ] && [ "$owner_role" != "$runtime_role" ] || \
  fail 'owner, migrator, runtime role은 서로 달라야 합니다.'

owner_password=$(read_secret postgres_owner_password)
# Validate every credential before the first database mutation. The two values
# below are deliberately not exported or placed in a process argument.
migrator_password=$(read_secret postgres_migrator_password)
runtime_password=$(read_secret postgres_runtime_password)
[ "$owner_password" != "$migrator_password" ] && \
  [ "$owner_password" != "$runtime_password" ] && \
  [ "$migrator_password" != "$runtime_password" ] || \
  fail 'owner, migrator, runtime 비밀번호는 서로 달라야 합니다.'
unset migrator_password runtime_password

pgpass_file=/tmp/alpha-role-provision.pgpass
cleanup() {
  rm -f -- "$pgpass_file"
}
trap cleanup EXIT HUP INT TERM
printf 'postgres:5432:%s:%s:%s\n' \
  "$database_name" "$owner_role" "$owner_password" > "$pgpass_file"
unset owner_password
chmod 600 "$pgpass_file"
export PGPASSFILE=$pgpass_file

psql_base() {
  psql \
    --no-password \
    --no-psqlrc \
    --host=postgres \
    --port=5432 \
    --username="$owner_role" \
    --dbname="$database_name" \
    --set=ON_ERROR_STOP=1 \
    "$@"
}

owner_state=$(psql_base --tuples-only --no-align --quiet \
  --command="SELECT current_user || chr(58) || CASE WHEN rolsuper THEN '1' ELSE '0' END FROM pg_roles WHERE rolname = current_user")
[ "$owner_state" = "$owner_role:1" ] || \
  fail 'bootstrap owner 연결이 superuser로 확인되지 않아 변경을 중단합니다.'

psql_base \
  --set=database_name="$database_name" \
  --set=owner_role="$owner_role" \
  --set=migrator_role="$migrator_role" \
  --set=runtime_role="$runtime_role" \
  --file=- <<'SQL'
SELECT format(
  'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
  :'migrator_role'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'migrator_role')
\gexec

SELECT format(
  'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
  :'runtime_role'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'runtime_role')
\gexec

ALTER ROLE :"migrator_role"
  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 10;
ALTER ROLE :"runtime_role"
  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 100;

-- psql reads the secrets itself. They never appear in argv, environment, SQL
-- files, or logs, and :'var' performs literal-safe SQL quoting.
\set migrator_password `cat /run/secrets/postgres_migrator_password`
\set runtime_password `cat /run/secrets/postgres_runtime_password`
ALTER ROLE :"migrator_role" PASSWORD :'migrator_password';
ALTER ROLE :"runtime_role" PASSWORD :'runtime_password';
\unset migrator_password
\unset runtime_password

-- Remove every pre-existing membership so neither application credential can
-- SET ROLE into an older privileged role on an upgraded volume.
SELECT format('REVOKE %I FROM %I', parent.rolname, member.rolname)
FROM pg_auth_members membership
JOIN pg_roles parent ON parent.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
WHERE member.rolname IN (:'migrator_role', :'runtime_role')
\gexec

-- This database is dedicated to CTFnight. Re-home every application object in
-- public without REASSIGN OWNED, which could also alter unrelated shared
-- databases or tablespaces owned by a legacy bootstrap role.
ALTER DATABASE :"database_name" OWNER TO :"migrator_role";
ALTER SCHEMA public OWNER TO :"migrator_role";

SELECT format(
  'ALTER %s %I.%I OWNER TO %I',
  CASE relation.relkind
    WHEN 'S' THEN 'SEQUENCE'
    WHEN 'v' THEN 'VIEW'
    WHEN 'm' THEN 'MATERIALIZED VIEW'
    WHEN 'f' THEN 'FOREIGN TABLE'
    WHEN 'c' THEN 'TYPE'
    ELSE 'TABLE'
  END,
  namespace.nspname,
  relation.relname,
  :'migrator_role'
)
FROM pg_class relation
JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
JOIN pg_roles owner ON owner.oid = relation.relowner
WHERE namespace.nspname = 'public'
  AND relation.relkind IN ('r', 'p', 'S', 'v', 'm', 'f', 'c')
  AND owner.rolname <> :'migrator_role'
ORDER BY relation.oid
\gexec

SELECT format(
  'ALTER %s %I.%I(%s) OWNER TO %I',
  CASE procedure.prokind WHEN 'p' THEN 'PROCEDURE' WHEN 'a' THEN 'AGGREGATE' ELSE 'FUNCTION' END,
  namespace.nspname,
  procedure.proname,
  pg_get_function_identity_arguments(procedure.oid),
  :'migrator_role'
)
FROM pg_proc procedure
JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
JOIN pg_roles owner ON owner.oid = procedure.proowner
WHERE namespace.nspname = 'public'
  AND owner.rolname <> :'migrator_role'
ORDER BY procedure.oid
\gexec

SELECT format('ALTER TYPE %I.%I OWNER TO %I', namespace.nspname, type.typname, :'migrator_role')
FROM pg_type type
JOIN pg_namespace namespace ON namespace.oid = type.typnamespace
JOIN pg_roles owner ON owner.oid = type.typowner
WHERE namespace.nspname = 'public'
  AND type.typrelid = 0
  AND type.typtype IN ('c', 'd', 'e', 'm', 'r')
  AND owner.rolname <> :'migrator_role'
ORDER BY type.oid
\gexec

REVOKE ALL PRIVILEGES ON DATABASE :"database_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database_name" TO :"owner_role", :"migrator_role", :"runtime_role";
REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC, :"runtime_role";
GRANT ALL PRIVILEGES ON SCHEMA public TO :"migrator_role";
GRANT USAGE ON SCHEMA public TO :"runtime_role";

REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC, :"runtime_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"runtime_role";
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, :"runtime_role";
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO :"runtime_role";
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, :"runtime_role";

ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_role" IN SCHEMA public
  REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC, :"runtime_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_role" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"runtime_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_role" IN SCHEMA public
  REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC, :"runtime_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_role" IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO :"runtime_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_role"
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- pg_catalog remains implicitly first for name resolution when it is omitted;
-- public is then the first explicit schema and therefore the safe DDL target.
ALTER ROLE :"migrator_role" SET search_path = public;
ALTER ROLE :"runtime_role" SET search_path = public;
ALTER ROLE :"runtime_role" SET statement_timeout = '30s';
ALTER ROLE :"runtime_role" SET lock_timeout = '5s';
ALTER ROLE :"runtime_role" SET idle_in_transaction_session_timeout = '15s';

-- Fail closed if role attributes, ownership, or the core runtime boundary did
-- not converge to the canonical state.
SELECT 1 / CASE WHEN (
  SELECT count(*) = 2
  FROM pg_roles
  WHERE rolname IN (:'migrator_role', :'runtime_role')
    AND rolcanlogin
    AND NOT rolsuper
    AND NOT rolcreatedb
    AND NOT rolcreaterole
    AND NOT rolreplication
    AND NOT rolbypassrls
    AND NOT rolinherit
) THEN 1 ELSE 0 END;

SELECT 1 / CASE WHEN NOT EXISTS (
  SELECT 1
  FROM pg_auth_members membership
  JOIN pg_roles member ON member.oid = membership.member
  WHERE member.rolname IN (:'migrator_role', :'runtime_role')
) THEN 1 ELSE 0 END;

SELECT 1 / CASE WHEN (
  SELECT owner.rolname = :'migrator_role'
  FROM pg_database database
  JOIN pg_roles owner ON owner.oid = database.datdba
  WHERE database.datname = :'database_name'
) THEN 1 ELSE 0 END;

SELECT 1 / CASE WHEN (
  SELECT owner.rolname = :'migrator_role'
  FROM pg_namespace namespace
  JOIN pg_roles owner ON owner.oid = namespace.nspowner
  WHERE namespace.nspname = 'public'
) THEN 1 ELSE 0 END;

SELECT 1 / CASE WHEN NOT has_database_privilege(:'runtime_role', :'database_name', 'TEMP')
  AND has_database_privilege(:'runtime_role', :'database_name', 'CONNECT')
  AND has_schema_privilege(:'runtime_role', 'public', 'USAGE')
  AND NOT has_schema_privilege(:'runtime_role', 'public', 'CREATE')
THEN 1 ELSE 0 END;
SQL

printf '%s\n' 'PostgreSQL owner/migrator/runtime 역할과 최소권한을 적용했습니다.'
