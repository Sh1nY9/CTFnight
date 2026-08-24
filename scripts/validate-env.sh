#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
env_file=${1:-"$app_root/.env"}
secret_dir=$(dirname -- "$env_file")/.secrets

fail() {
  printf '오류: %s\n' "$1" >&2
  exit 1
}

is_common_dns_email() {
  candidate=$1
  case "$candidate" in
    ''|*[[:space:][:cntrl:]]*|*@*@*) return 1 ;;
  esac
  email_local=${candidate%@*}
  email_domain=${candidate#*@}
  [ "$email_local" != "$candidate" ] || return 1
  case "$email_local" in
    ''|.*|*.|*..*|*[!A-Za-z0-9._%+-]*) return 1 ;;
  esac
  case "$email_domain" in
    ''|.*|*.|*..*|*[!A-Za-z0-9.-]*) return 1 ;;
  esac
  case "$email_domain" in
    *.*) ;;
    *) return 1 ;;
  esac
  old_ifs=$IFS
  IFS=.
  set -- $email_domain
  IFS=$old_ifs
  [ "$#" -ge 2 ] || return 1
  for label in "$@"; do
    case "$label" in
      ''|-*|*-|*[!A-Za-z0-9-]*) return 1 ;;
    esac
  done
  return 0
}

[ -f "$env_file" ] || fail "환경 파일이 없습니다: $env_file"
[ ! -L "$env_file" ] || fail "환경 파일은 심볼릭 링크일 수 없습니다: $env_file"
[ -d "$secret_dir" ] || fail "Compose secret 디렉터리가 없습니다: $secret_dir"
[ ! -L "$secret_dir" ] || fail "Compose secret 디렉터리는 심볼릭 링크일 수 없습니다: $secret_dir"
command -v getfacl >/dev/null 2>&1 || fail 'secret ACL 검증을 위해 getfacl(acl package)이 필요합니다.'

if LC_ALL=C grep -q "$(printf '\r')" "$env_file"; then
  fail '환경 파일에 CR 문자가 있습니다. LF 줄바꿈으로 변환하세요.'
fi
if grep -q '\$' "$env_file"; then
  fail '환경 파일 값에는 Compose 재보간을 유발하는 $ 문자를 사용할 수 없습니다.'
fi

while IFS= read -r env_line || [ -n "$env_line" ]; do
  case "$env_line" in
    ''|'#'*) continue ;;
    *=*) env_key=${env_line%%=*} ;;
    *) fail '환경 파일은 KEY=value 또는 주석 형식이어야 합니다.' ;;
  esac
  case "$env_key" in
    ALPHA_COMPOSE_PROJECT_NAME|ALPHA_SITE_ADDRESS|ALPHA_HTTP_PORT|ALPHA_HTTPS_PORT|ALPHA_BIND_ADDRESS|\
    ALPHA_ENVIRONMENT|ALPHA_COOKIE_SECURE|ALPHA_ALLOWED_ORIGINS|ALPHA_TRUSTED_HOSTS|\
    ALPHA_FORWARDED_ALLOW_IPS|ALPHA_SESSION_TTL_HOURS|ALPHA_SESSION_CLEANUP_BATCH_SIZE|\
    ALPHA_CSRF_TTL_SECONDS|ALPHA_MAX_FLAG_LENGTH|ALPHA_POSTGRES_DB|\
    ALPHA_POSTGRES_OWNER_USER|ALPHA_POSTGRES_MIGRATOR_USER|ALPHA_POSTGRES_RUNTIME_USER|\
    ALPHA_ADMIN_EMAIL|ALPHA_ADMIN_USERNAME|ALPHA_ADMIN_BOOTSTRAPPED|ALPHA_SEED_DEMO|\
    ALPHA_BACKUP_AGE_RECIPIENT|POSTGRES_IMAGE|REDIS_IMAGE) ;;
    *) fail "허용되지 않은 환경 키입니다: $env_key" ;;
  esac
done < "$env_file"

if find "$env_file" -prune -perm /077 -print | grep -q .; then
  fail "$env_file 권한이 너무 넓습니다. chmod 600 '$env_file'을 실행하세요."
fi
if find "$secret_dir" -prune -perm /077 -print | grep -q .; then
  fail "$secret_dir 권한이 너무 넓습니다. chmod 700 '$secret_dir'을 실행하세요."
fi
expected_secret_dir_acl=$(printf '%s\n' 'user::rwx' 'group::---' 'other::---')
actual_secret_dir_acl=$(getfacl -cpn -- "$secret_dir")
[ "$actual_secret_dir_acl" = "$expected_secret_dir_acl" ] || \
  fail "$secret_dir ACL은 owner-only 0700이어야 합니다."

for forbidden_key in \
  ALPHA_SECRET_KEY \
  ALPHA_POSTGRES_PASSWORD \
  ALPHA_POSTGRES_OWNER_PASSWORD \
  ALPHA_POSTGRES_MIGRATOR_PASSWORD \
  ALPHA_POSTGRES_RUNTIME_PASSWORD \
  ALPHA_REDIS_PASSWORD \
  ALPHA_ADMIN_PASSWORD
do
  if grep -q "^${forbidden_key}=" "$env_file"; then
    fail "$forbidden_key 값을 .env에 저장하지 말고 .secrets 파일을 사용하세요."
  fi
done

get_value() {
  key=$1
  count=$(grep -c "^${key}=" "$env_file" || true)
  [ "$count" -eq 1 ] || fail "$key 항목은 정확히 한 번 있어야 합니다."
  sed -n "s/^${key}=//p" "$env_file"
}

reject_placeholder() {
  key=$1
  value=$2
  case "$value" in
    ''|*CHANGE_ME*|*change-me*|*example-secret*)
      fail "$key 값을 안전한 실제 값으로 바꾸세요."
      ;;
  esac
}

get_secret() {
  name=$1
  allow_empty=${2:-false}
  path=$secret_dir/$name
  [ -f "$path" ] || fail "secret 파일이 없습니다: $path"
  [ ! -L "$path" ] || fail "secret 파일은 심볼릭 링크일 수 없습니다: $path"
  expected_acl=$(printf '%s\n' \
    'user::rw-' 'user:65532:r--' 'group::---' 'mask::r--' 'other::---')
  actual_acl=$(getfacl -cpn -- "$path")
  [ "$actual_acl" = "$expected_acl" ] || \
    fail "secret 파일은 owner rw와 container UID 65532 read만 허용해야 합니다: $path"
  if LC_ALL=C grep -q "$(printf '\r')" "$path"; then
    fail "secret 파일에 CR 문자가 있습니다: $path"
  fi
  line_count=$(wc -l < "$path" | tr -d ' ')
  case "$allow_empty:$line_count" in
    true:0|true:1|false:1) ;;
    *) fail "secret 파일은 정확히 한 줄이어야 합니다: $path" ;;
  esac
  sed -n '1p' "$path"
}

site_address=$(get_value ALPHA_SITE_ADDRESS)
compose_project_name=$(get_value ALPHA_COMPOSE_PROJECT_NAME)
secret_key=$(get_secret alpha_secret_key)
postgres_owner_password=$(get_secret postgres_owner_password)
postgres_migrator_password=$(get_secret postgres_migrator_password)
postgres_runtime_password=$(get_secret postgres_runtime_password)
redis_password=$(get_secret redis_password)
admin_email=$(get_value ALPHA_ADMIN_EMAIL)
admin_username=$(get_value ALPHA_ADMIN_USERNAME)
admin_password=$(get_secret admin_password true)
admin_bootstrapped=$(get_value ALPHA_ADMIN_BOOTSTRAPPED)
cookie_secure=$(get_value ALPHA_COOKIE_SECURE)
environment=$(get_value ALPHA_ENVIRONMENT)
seed_demo=$(get_value ALPHA_SEED_DEMO)
postgres_image=$(get_value POSTGRES_IMAGE)
redis_image=$(get_value REDIS_IMAGE)
trusted_hosts=$(get_value ALPHA_TRUSTED_HOSTS)
allowed_origins=$(get_value ALPHA_ALLOWED_ORIGINS)
postgres_db=$(get_value ALPHA_POSTGRES_DB)
postgres_owner_user=$(get_value ALPHA_POSTGRES_OWNER_USER)
postgres_migrator_user=$(get_value ALPHA_POSTGRES_MIGRATOR_USER)
postgres_runtime_user=$(get_value ALPHA_POSTGRES_RUNTIME_USER)
http_port=$(get_value ALPHA_HTTP_PORT)
https_port=$(get_value ALPHA_HTTPS_PORT)
bind_address=$(get_value ALPHA_BIND_ADDRESS)
max_flag_length=$(get_value ALPHA_MAX_FLAG_LENGTH)
session_cleanup_batch_size=$(get_value ALPHA_SESSION_CLEANUP_BATCH_SIZE)
session_ttl_hours=$(get_value ALPHA_SESSION_TTL_HOURS)
csrf_ttl_seconds=$(get_value ALPHA_CSRF_TTL_SECONDS)
forwarded_allow_ips=$(get_value ALPHA_FORWARDED_ALLOW_IPS)
backup_recipient=$(get_value ALPHA_BACKUP_AGE_RECIPIENT)

case "$compose_project_name" in
  ''|*[!a-z0-9-]*|-*|*-|*--*)
    fail 'ALPHA_COMPOSE_PROJECT_NAME은 소문자·숫자 단어를 단일 -로 구분해야 합니다.'
    ;;
esac
[ "${#compose_project_name}" -le 63 ] || fail 'ALPHA_COMPOSE_PROJECT_NAME은 63자 이하여야 합니다.'

case "$site_address" in
  ''|*[[:space:]]*) fail 'ALPHA_SITE_ADDRESS가 비어 있거나 공백을 포함합니다.' ;;
esac

for port in "$http_port" "$https_port"; do
  case "$port" in *[!0-9]*|'') fail '공개 포트는 1~65535 정수여야 합니다.' ;; esac
  [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || fail '공개 포트는 1~65535 범위여야 합니다.'
done
[ "$http_port" -ne "$https_port" ] || fail 'HTTP와 HTTPS host port는 서로 달라야 합니다.'

case "$max_flag_length" in
  *[!0-9]*|'') fail 'ALPHA_MAX_FLAG_LENGTH는 16~4096 정수여야 합니다.' ;;
esac
[ "$max_flag_length" -ge 16 ] && [ "$max_flag_length" -le 4096 ] || fail 'ALPHA_MAX_FLAG_LENGTH는 16~4096 범위여야 합니다.'

case "$session_cleanup_batch_size" in
  *[!0-9]*|'') fail 'ALPHA_SESSION_CLEANUP_BATCH_SIZE는 1~1000 정수여야 합니다.' ;;
esac
[ "$session_cleanup_batch_size" -ge 1 ] && [ "$session_cleanup_batch_size" -le 1000 ] || \
  fail 'ALPHA_SESSION_CLEANUP_BATCH_SIZE는 1~1000 범위여야 합니다.'

case "$session_ttl_hours" in
  *[!0-9]*|'') fail 'ALPHA_SESSION_TTL_HOURS는 1~2160 정수여야 합니다.' ;;
esac
[ "$session_ttl_hours" -ge 1 ] && [ "$session_ttl_hours" -le 2160 ] || fail 'ALPHA_SESSION_TTL_HOURS는 1~2160 범위여야 합니다.'

case "$csrf_ttl_seconds" in
  *[!0-9]*|'') fail 'ALPHA_CSRF_TTL_SECONDS는 60~86400 정수여야 합니다.' ;;
esac
[ "$csrf_ttl_seconds" -ge 60 ] && [ "$csrf_ttl_seconds" -le 86400 ] || fail 'ALPHA_CSRF_TTL_SECONDS는 60~86400 범위여야 합니다.'
[ "$forwarded_allow_ips" = '172.31.250.2' ] || fail 'ALPHA_FORWARDED_ALLOW_IPS는 고정된 Caddy API 주소 172.31.250.2여야 합니다.'

case "$backup_recipient" in
  ''|age1*|age-plugin-*) ;;
  *) fail 'ALPHA_BACKUP_AGE_RECIPIENT 형식이 올바르지 않습니다.' ;;
esac

case "$site_address" in
  http://localhost|http://127.0.0.1) ;;
  http://*) fail '공개 배포 주소에는 평문 HTTP를 사용할 수 없습니다.' ;;
esac

case "$site_address" in
  http://localhost|http://127.0.0.1)
    [ "$bind_address" = 127.0.0.1 ] || fail 'loopback 개발 배포는 ALPHA_BIND_ADDRESS=127.0.0.1이어야 합니다.'
    ;;
  *)
    [ "$bind_address" = 0.0.0.0 ] || fail '공개 배포는 ALPHA_BIND_ADDRESS=0.0.0.0이어야 합니다.'
    [ "$http_port" -eq 80 ] && [ "$https_port" -eq 443 ] || fail '공개 자동 TLS 배포는 host port 80과 443을 사용해야 합니다.'
    ;;
esac

case "$site_address" in
  http://localhost) public_host=localhost ;;
  http://127.0.0.1) public_host=127.0.0.1 ;;
  https://*) public_host=${site_address#https://} ;;
  *://*) fail 'ALPHA_SITE_ADDRESS는 HTTP(S) 주소 또는 DNS 이름이어야 합니다.' ;;
  *) public_host=$site_address ;;
esac
case "$public_host" in
  *:*) fail 'ALPHA_SITE_ADDRESS에는 port를 넣지 말고 ALPHA_HTTP_PORT/ALPHA_HTTPS_PORT를 사용하세요.' ;;
esac
public_host=${public_host%%:*}
case "$public_host" in
  ''|*/*|*[!A-Za-z0-9.-]*) fail 'ALPHA_SITE_ADDRESS의 hostname 형식이 올바르지 않습니다.' ;;
esac

case "$site_address" in
  http://*|https://*) expected_origin=${site_address%/} ;;
  *) expected_origin=https://${site_address%/} ;;
esac
case "$site_address" in
  http://localhost|http://127.0.0.1)
    if [ "$http_port" -ne 80 ]; then
      expected_origin=$expected_origin:$http_port
    fi
    ;;
esac

command -v python3 >/dev/null 2>&1 || fail 'origin과 trusted host 검증을 위해 python3가 필요합니다.'
python3 - "$allowed_origins" "$trusted_hosts" "$expected_origin" "$public_host" <<'PY' || exit 1
import json
import sys

try:
    origins = json.loads(sys.argv[1])
    hosts = json.loads(sys.argv[2])
except (TypeError, ValueError) as exc:
    print(f"오류: origin/trusted host 값은 JSON 배열이어야 합니다: {exc}", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(origins, list) or not all(isinstance(item, str) for item in origins):
    print("오류: ALPHA_ALLOWED_ORIGINS는 문자열 JSON 배열이어야 합니다.", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(hosts, list) or not all(isinstance(item, str) for item in hosts):
    print("오류: ALPHA_TRUSTED_HOSTS는 문자열 JSON 배열이어야 합니다.", file=sys.stderr)
    raise SystemExit(1)
if sys.argv[3] not in origins:
    print(f"오류: 공개 origin이 ALPHA_ALLOWED_ORIGINS에 없습니다: {sys.argv[3]}", file=sys.stderr)
    raise SystemExit(1)
for required_host in ("localhost", "127.0.0.1", "backend", sys.argv[4]):
    if required_host not in hosts:
        print(
            f"오류: ALPHA_TRUSTED_HOSTS에 필수 hostname이 없습니다: {required_host}",
            file=sys.stderr,
        )
        raise SystemExit(1)
PY

reject_placeholder ALPHA_SECRET_KEY "$secret_key"
[ "${#secret_key}" -ge 32 ] || fail 'ALPHA_SECRET_KEY는 최소 32자여야 합니다.'

for credential in \
  "$postgres_owner_password" \
  "$postgres_migrator_password" \
  "$postgres_runtime_password" \
  "$redis_password"
do
  reject_placeholder '데이터 저장소 비밀번호' "$credential"
  [ "${#credential}" -ge 24 ] || fail 'PostgreSQL/Redis 비밀번호는 최소 24자여야 합니다.'
  case "$credential" in
    *[!A-Za-z0-9_-]*) fail 'PostgreSQL/Redis 비밀번호는 URL-safe 문자(A-Z, a-z, 0-9, _, -)만 사용하세요.' ;;
  esac
done

[ "$postgres_owner_password" != "$postgres_migrator_password" ] && \
  [ "$postgres_owner_password" != "$postgres_runtime_password" ] && \
  [ "$postgres_migrator_password" != "$postgres_runtime_password" ] || \
  fail 'PostgreSQL owner, migrator, runtime 비밀번호는 서로 달라야 합니다.'

for db_identifier in \
  "$postgres_db" \
  "$postgres_owner_user" \
  "$postgres_migrator_user" \
  "$postgres_runtime_user"
do
  case "$db_identifier" in
    ''|*[!A-Za-z0-9_]*) fail 'PostgreSQL DB와 사용자는 영문, 숫자, _만 사용할 수 있습니다.' ;;
  esac
  [ "${#db_identifier}" -le 63 ] || fail 'PostgreSQL DB와 사용자는 63자 이하여야 합니다.'
done
[ "$postgres_migrator_user" = alpha_migrator ] || fail 'ALPHA_POSTGRES_MIGRATOR_USER는 alpha_migrator여야 합니다.'
[ "$postgres_runtime_user" = alpha_app ] || fail 'ALPHA_POSTGRES_RUNTIME_USER는 alpha_app이어야 합니다.'
[ "$postgres_owner_user" != "$postgres_migrator_user" ] && \
  [ "$postgres_owner_user" != "$postgres_runtime_user" ] || \
  fail 'PostgreSQL owner, migrator, runtime 사용자는 서로 달라야 합니다.'
case "$admin_bootstrapped" in
  true)
    [ -n "$admin_email" ] || fail 'ALPHA_ADMIN_EMAIL은 bootstrap 관리자 식별자로 유지해야 합니다.'
    [ -z "$admin_password" ] || fail 'bootstrap 완료 후 .secrets/admin_password를 비워야 합니다.'
    ;;
  false)
    [ -n "$admin_email" ] || fail '최초 bootstrap에는 ALPHA_ADMIN_EMAIL이 필요합니다.'
    reject_placeholder ALPHA_ADMIN_PASSWORD "$admin_password"
    [ "${#admin_password}" -ge 16 ] || fail '초기 관리자 비밀번호는 최소 16자여야 합니다.'
    ;;
  *) fail 'ALPHA_ADMIN_BOOTSTRAPPED는 true 또는 false여야 합니다.' ;;
esac

if [ -n "$admin_email" ]; then
  is_common_dns_email "$admin_email" || fail 'ALPHA_ADMIN_EMAIL은 user@example.com 형태의 일반 DNS email이어야 합니다.'
fi
case "$admin_username" in
  ''|*[!A-Za-z0-9_.-]*) fail 'ALPHA_ADMIN_USERNAME은 영문, 숫자, _, ., -만 사용할 수 있습니다.' ;;
esac
[ "${#admin_username}" -ge 3 ] && [ "${#admin_username}" -le 40 ] || fail 'ALPHA_ADMIN_USERNAME은 3~40자여야 합니다.'

case "$cookie_secure" in true|false) ;; *) fail 'ALPHA_COOKIE_SECURE는 true 또는 false여야 합니다.' ;; esac
case "$environment" in development|production) ;; *) fail 'ALPHA_ENVIRONMENT는 development 또는 production이어야 합니다.' ;; esac
case "$seed_demo" in true|false) ;; *) fail 'ALPHA_SEED_DEMO는 true 또는 false여야 합니다.' ;; esac

case "$site_address:$cookie_secure" in
  http://localhost*:false|http://127.0.0.1*:false) ;;
  *:true) ;;
  *) fail '공개 HTTPS 배포에서는 ALPHA_COOKIE_SECURE=true여야 합니다.' ;;
esac

case "$site_address:$environment" in
  http://localhost*:development|http://127.0.0.1*:development) ;;
  http://localhost*:production|http://127.0.0.1*:production)
    fail 'loopback HTTP는 ALPHA_ENVIRONMENT=development여야 합니다.'
    ;;
  *:production) ;;
  *) fail '공개 배포는 ALPHA_ENVIRONMENT=production이어야 합니다.' ;;
esac

for image in "$postgres_image" "$redis_image"; do
  [ -n "$image" ] || fail '인프라 이미지 값이 비어 있습니다.'
  case "$image" in *@sha256:*) ;; *) fail "인프라 이미지는 sha256 digest로 고정하세요: $image" ;; esac
  digest=${image##*@sha256:}
  [ "${#digest}" -eq 64 ] || fail "이미지 digest 길이가 올바르지 않습니다: $image"
  case "$digest" in *[!0-9a-f]*) fail "이미지 digest는 소문자 16진수여야 합니다: $image" ;; esac
done

printf '%s\n' "환경 검증 통과: $env_file"
