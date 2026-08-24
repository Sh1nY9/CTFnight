#!/bin/sh
set -eu

umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
env_file=$app_root/.env
secret_dir=$app_root/.secrets
env_tmp=
secret_tmp=

fail() {
  printf 'DB role upgrade 오류: %s\n' "$1" >&2
  exit 1
}

cleanup() {
  [ -z "${env_tmp:-}" ] || [ ! -f "$env_tmp" ] || rm -f -- "$env_tmp"
  [ -z "${secret_tmp:-}" ] || [ ! -f "$secret_tmp" ] || rm -f -- "$secret_tmp"
}
trap cleanup EXIT HUP INT TERM

[ -f "$env_file" ] && [ ! -L "$env_file" ] || fail 'canonical .env 일반 파일이 필요합니다.'
[ -d "$secret_dir" ] && [ ! -L "$secret_dir" ] || fail 'canonical .secrets 실제 디렉터리가 필요합니다.'
command -v openssl >/dev/null 2>&1 || fail '새 role credential 생성에 openssl이 필요합니다.'

new_key_count=$(grep -Ec '^ALPHA_POSTGRES_(OWNER|MIGRATOR|RUNTIME)_USER=' "$env_file" || true)
legacy_key_count=$(grep -c '^ALPHA_POSTGRES_USER=' "$env_file" || true)

if [ "$new_key_count" -eq 3 ] && [ "$legacy_key_count" -eq 0 ]; then
  [ ! -e "$secret_dir/postgres_password" ] && [ ! -L "$secret_dir/postgres_password" ] || \
    fail 'canonical env와 legacy postgres_password가 함께 있습니다. 대상을 수동 확인하세요.'
  "$script_dir/validate-env.sh" "$env_file"
  printf '%s\n' 'PostgreSQL role/secret 구성은 이미 upgrade되어 있습니다.'
  exit 0
fi

[ "$legacy_key_count" -eq 1 ] && [ "$new_key_count" -eq 0 ] || \
  fail 'legacy ALPHA_POSTGRES_USER 하나 또는 canonical role key 세 개만 허용합니다.'

legacy_owner=$(sed -n 's/^ALPHA_POSTGRES_USER=//p' "$env_file")
case "$legacy_owner" in
  ''|*[!A-Za-z0-9_]*) fail 'legacy PostgreSQL owner identifier가 올바르지 않습니다.' ;;
esac
[ "${#legacy_owner}" -le 63 ] || fail 'legacy PostgreSQL owner identifier는 63자 이하여야 합니다.'
[ "$legacy_owner" != alpha_migrator ] && [ "$legacy_owner" != alpha_app ] || \
  fail 'legacy owner가 canonical application role과 충돌합니다.'

legacy_secret=$secret_dir/postgres_password
owner_secret=$secret_dir/postgres_owner_password
if [ -e "$legacy_secret" ] || [ -L "$legacy_secret" ]; then
  [ -f "$legacy_secret" ] && [ ! -L "$legacy_secret" ] || \
    fail 'legacy postgres_password는 symlink가 아닌 일반 파일이어야 합니다.'
  [ ! -e "$owner_secret" ] && [ ! -L "$owner_secret" ] || \
    fail 'legacy와 canonical owner secret이 함께 있어 자동 선택할 수 없습니다.'
  mv -- "$legacy_secret" "$owner_secret"
else
  # Recovery after interruption between the atomic secret rename and env
  # publication: continue only from the one unambiguous canonical owner file.
  [ -f "$owner_secret" ] && [ ! -L "$owner_secret" ] || \
    fail 'legacy 또는 canonical PostgreSQL owner secret을 찾을 수 없습니다.'
fi

owner_value=$(sed -n '1p' "$owner_secret")
owner_lines=$(wc -l < "$owner_secret" | tr -d ' ')
[ "$owner_lines" -eq 1 ] && [ "${#owner_value}" -ge 24 ] || \
  fail 'owner secret은 최소 24자의 한 줄이어야 합니다.'
case "$owner_value" in
  *[!A-Za-z0-9_-]*) fail 'owner secret은 URL-safe 문자만 사용할 수 있습니다.' ;;
esac

create_role_secret() {
  role_secret_name=$1
  role_secret_path=$secret_dir/$role_secret_name
  if [ -e "$role_secret_path" ] || [ -L "$role_secret_path" ]; then
    [ -f "$role_secret_path" ] && [ ! -L "$role_secret_path" ] || \
      fail "$role_secret_name 은 symlink가 아닌 일반 파일이어야 합니다."
    return
  fi
  while :; do
    candidate=$(openssl rand -hex 24)
    [ "$candidate" != "$owner_value" ] || continue
    other_name=postgres_runtime_password
    [ "$role_secret_name" = postgres_migrator_password ] || other_name=postgres_migrator_password
    other_path=$secret_dir/$other_name
    if [ -f "$other_path" ] && [ "$(sed -n '1p' "$other_path")" = "$candidate" ]; then
      continue
    fi
    break
  done
  secret_tmp=$(mktemp "$secret_dir/.db-role-secret.XXXXXX")
  printf '%s\n' "$candidate" > "$secret_tmp"
  chmod 600 "$secret_tmp"
  mv -- "$secret_tmp" "$role_secret_path"
  secret_tmp=
}

create_role_secret postgres_migrator_password
create_role_secret postgres_runtime_password

validate_role_secret() {
  checked_name=$1
  checked_path=$secret_dir/$checked_name
  checked_lines=$(wc -l < "$checked_path" | tr -d ' ')
  checked_value=$(sed -n '1p' "$checked_path")
  [ "$checked_lines" -eq 1 ] && \
    [ "${#checked_value}" -ge 24 ] && \
    [ "${#checked_value}" -le 256 ] || \
    fail "$checked_name 은 24~256자의 한 줄이어야 합니다."
  case "$checked_value" in
    *[!A-Za-z0-9_-]*) fail "$checked_name 은 URL-safe 문자만 사용할 수 있습니다." ;;
  esac
  printf '%s' "$checked_value"
}

migrator_value=$(validate_role_secret postgres_migrator_password)
runtime_value=$(validate_role_secret postgres_runtime_password)
[ "$owner_value" != "$migrator_value" ] && \
  [ "$owner_value" != "$runtime_value" ] && \
  [ "$migrator_value" != "$runtime_value" ] || \
  fail 'owner, migrator, runtime credential은 서로 달라야 합니다.'
unset owner_value migrator_value runtime_value

env_tmp=$(mktemp "$app_root/.env.db-role-upgrade.XXXXXX")
awk -v owner="$legacy_owner" '
  /^ALPHA_POSTGRES_USER=/ {
    print "ALPHA_POSTGRES_OWNER_USER=" owner
    print "ALPHA_POSTGRES_MIGRATOR_USER=alpha_migrator"
    print "ALPHA_POSTGRES_RUNTIME_USER=alpha_app"
    next
  }
  { print }
' "$env_file" > "$env_tmp"
chmod 600 "$env_tmp"

# Apply the exact container ACL before publishing the env that references the
# new paths. A signal can leave a retryable legacy env, never a canonical env
# pointing at unreadable or missing role secrets.
"$script_dir/set-secret-acl.sh" "$secret_dir" >/dev/null
mv -- "$env_tmp" "$env_file"
env_tmp=
"$script_dir/validate-env.sh" "$env_file"

printf '%s\n' 'legacy PostgreSQL owner credential을 보존하며 role-separated 구성으로 upgrade했습니다.'
printf '%s\n' '다음 make up에서 db-roles가 전용 database 객체 소유권과 최소권한을 수렴시킵니다.'
