#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
test_root=$(mktemp -d "${TMPDIR:-/tmp}/alpha-db-role-upgrade.XXXXXX")

cleanup() {
  rm -rf -- "$test_root"
}
trap cleanup EXIT HUP INT TERM

fixture=$test_root/app
mkdir -p "$fixture/scripts"
cp -- "$app_root/scripts/generate-env.sh" "$fixture/scripts/generate-env.sh"
cp -- "$app_root/scripts/set-secret-acl.sh" "$fixture/scripts/set-secret-acl.sh"
cp -- "$app_root/scripts/upgrade-database-roles.sh" "$fixture/scripts/upgrade-database-roles.sh"
cp -- "$app_root/scripts/validate-env.sh" "$fixture/scripts/validate-env.sh"
chmod 0555 "$fixture/scripts"/*.sh

ALPHA_COMPOSE_PROJECT_NAME=alpha-role-upgrade \
  "$fixture/scripts/generate-env.sh" "$fixture/.env" >/dev/null

mv -- "$fixture/.secrets/postgres_owner_password" "$fixture/.secrets/postgres_password"
legacy_env=$(mktemp "$fixture/.env.legacy.XXXXXX")
awk '
  /^ALPHA_POSTGRES_OWNER_USER=/ {
    sub(/^ALPHA_POSTGRES_OWNER_USER=/, "ALPHA_POSTGRES_USER=")
    print
    next
  }
  /^ALPHA_POSTGRES_(MIGRATOR|RUNTIME)_USER=/ { next }
  { print }
' "$fixture/.env" > "$legacy_env"
chmod 600 "$legacy_env"
mv -- "$legacy_env" "$fixture/.env"

owner_before=$(sha256sum "$fixture/.secrets/postgres_password" | awk '{print $1}')
"$fixture/scripts/upgrade-database-roles.sh" >/dev/null
owner_after=$(sha256sum "$fixture/.secrets/postgres_owner_password" | awk '{print $1}')
[ "$owner_before" = "$owner_after" ] || {
  printf '%s\n' '오류: legacy owner credential이 upgrade 중 변경되었습니다.' >&2
  exit 1
}
[ ! -e "$fixture/.secrets/postgres_password" ] || {
  printf '%s\n' '오류: legacy secret 경로가 남았습니다.' >&2
  exit 1
}
grep -Fx 'ALPHA_POSTGRES_OWNER_USER=alpha' "$fixture/.env" >/dev/null
grep -Fx 'ALPHA_POSTGRES_MIGRATOR_USER=alpha_migrator' "$fixture/.env" >/dev/null
grep -Fx 'ALPHA_POSTGRES_RUNTIME_USER=alpha_app' "$fixture/.env" >/dev/null
! grep -q '^ALPHA_POSTGRES_USER=' "$fixture/.env"
! cmp -s "$fixture/.secrets/postgres_owner_password" "$fixture/.secrets/postgres_migrator_password"
! cmp -s "$fixture/.secrets/postgres_owner_password" "$fixture/.secrets/postgres_runtime_password"
! cmp -s "$fixture/.secrets/postgres_migrator_password" "$fixture/.secrets/postgres_runtime_password"

canonical_before=$(sha256sum \
  "$fixture/.env" \
  "$fixture/.secrets/postgres_owner_password" \
  "$fixture/.secrets/postgres_migrator_password" \
  "$fixture/.secrets/postgres_runtime_password")
"$fixture/scripts/upgrade-database-roles.sh" >/dev/null
canonical_after=$(sha256sum \
  "$fixture/.env" \
  "$fixture/.secrets/postgres_owner_password" \
  "$fixture/.secrets/postgres_migrator_password" \
  "$fixture/.secrets/postgres_runtime_password")
[ "$canonical_before" = "$canonical_after" ] || {
  printf '%s\n' '오류: canonical 재실행이 env 또는 role credential을 변경했습니다.' >&2
  exit 1
}

printf '%s\n' 'legacy PostgreSQL role/secret idempotent upgrade 검증 통과'
