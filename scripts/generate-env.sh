#!/bin/sh
set -eu

umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
target=${1:-"$app_root/.env"}

if [ -e "$target" ] || [ -L "$target" ]; then
  printf '%s\n' "오류: $target 파일이 이미 있습니다. 기존 secret을 보호하기 위해 덮어쓰지 않습니다." >&2
  exit 1
fi

target_dir=$(dirname -- "$target")
if [ ! -d "$target_dir" ]; then
  printf '%s\n' "오류: 대상 디렉터리가 없습니다: $target_dir" >&2
  exit 1
fi
secret_dir=$target_dir/.secrets
if [ -e "$secret_dir" ] || [ -L "$secret_dir" ]; then
  printf '%s\n' "오류: $secret_dir 경로가 이미 있습니다. 기존 secret을 보호하기 위해 덮어쓰지 않습니다." >&2
  exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
  printf '%s\n' '오류: 안전한 secret 생성을 위해 openssl이 필요합니다.' >&2
  exit 1
fi
if ! command -v setfacl >/dev/null 2>&1 || ! command -v getfacl >/dev/null 2>&1; then
  printf '%s\n' '오류: non-root container secret ACL을 위해 setfacl/getfacl(acl package)이 필요합니다.' >&2
  exit 1
fi

site_address=${ALPHA_SITE_ADDRESS:-http://localhost}
compose_project_name=${ALPHA_COMPOSE_PROJECT_NAME:-alpha}
admin_email=${ALPHA_ADMIN_EMAIL:-admin@example.com}
admin_username=${ALPHA_ADMIN_USERNAME:-admin}
http_port=${ALPHA_HTTP_PORT:-80}
https_port=${ALPHA_HTTPS_PORT:-443}

case "$compose_project_name" in
  ''|*[!a-z0-9-]*|-*|*-|*--*)
    printf '%s\n' '오류: Compose project 이름은 소문자·숫자 단어를 단일 -로 구분해야 합니다.' >&2
    exit 1
    ;;
esac
if [ "${#compose_project_name}" -gt 63 ]; then
  printf '%s\n' '오류: Compose project 이름은 63자 이하여야 합니다.' >&2
  exit 1
fi

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

for port in "$http_port" "$https_port"; do
  case "$port" in *[!0-9]*|'') printf '%s\n' '오류: 공개 포트는 1~65535 정수여야 합니다.' >&2; exit 1 ;; esac
  if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    printf '%s\n' '오류: 공개 포트는 1~65535 범위여야 합니다.' >&2
    exit 1
  fi
done
[ "$http_port" -ne "$https_port" ] || {
  printf '%s\n' '오류: HTTP와 HTTPS host port는 서로 달라야 합니다.' >&2
  exit 1
}

case "$site_address" in
  http://*)
    origin=${site_address%/}
    if [ "$http_port" -ne 80 ]; then
      origin=$origin:$http_port
    fi
    authority=${site_address#http://}
    cookie_secure=false
    environment=development
    bind_address=127.0.0.1
    ;;
  https://*)
    origin=${site_address%/}
    authority=${site_address#https://}
    cookie_secure=true
    environment=production
    bind_address=0.0.0.0
    ;;
  *)
    origin=https://${site_address%/}
    authority=$site_address
    cookie_secure=true
    environment=production
    bind_address=0.0.0.0
    ;;
esac

case "$authority" in
  */*)
    printf '%s\n' '오류: 사이트 주소에 path를 사용할 수 없습니다.' >&2
    exit 1
    ;;
esac
case "$authority" in
  *:*)
    printf '%s\n' '오류: ALPHA_SITE_ADDRESS에는 port를 넣지 말고 ALPHA_HTTP_PORT/ALPHA_HTTPS_PORT를 사용하세요.' >&2
    exit 1
    ;;
esac
authority=${authority%%/*}
trusted_host=${authority%%:*}

case "$site_address" in
  http://localhost|http://127.0.0.1) ;;
  http://*)
    printf '%s\n' '오류: 공개 배포 주소에는 평문 HTTP를 사용할 수 없습니다.' >&2
    exit 1
    ;;
  https://*|*) ;;
esac

case "$site_address" in
  http://localhost|http://127.0.0.1) ;;
  *)
    if [ "$http_port" -ne 80 ] || [ "$https_port" -ne 443 ]; then
      printf '%s\n' '오류: 공개 자동 TLS 배포는 host port 80과 443을 사용해야 합니다.' >&2
      exit 1
    fi
    ;;
esac

case "$trusted_host" in
  ''|*[!A-Za-z0-9.-]*)
    printf '%s\n' '오류: 사이트 hostname 형식이 올바르지 않습니다.' >&2
    exit 1
    ;;
esac

if ! is_common_dns_email "$admin_email"; then
  printf '%s\n' '오류: 관리자 email은 user@example.com 형태의 일반 DNS email이어야 합니다.' >&2
  exit 1
fi
case "$admin_username" in
  ''|*[!A-Za-z0-9_.-]*)
    printf '%s\n' '오류: 관리자 username은 영문, 숫자, _, ., -만 사용할 수 있습니다.' >&2
    exit 1
    ;;
esac
if [ "${#admin_username}" -lt 3 ] || [ "${#admin_username}" -gt 40 ]; then
  printf '%s\n' '오류: 관리자 username은 3~40자여야 합니다.' >&2
  exit 1
fi

case "$trusted_host" in
  localhost|127.0.0.1)
    trusted_hosts='["localhost","127.0.0.1","backend"]'
    ;;
  *)
    trusted_hosts="[\"$trusted_host\",\"localhost\",\"127.0.0.1\",\"backend\"]"
    ;;
esac

case "$site_address$admin_email$admin_username" in
  *[[:space:]]*)
    printf '%s\n' '오류: 사이트 주소와 관리자 식별자에는 공백을 사용할 수 없습니다.' >&2
    exit 1
    ;;
esac

secret_key=$(openssl rand -hex 32)
postgres_owner_password=$(openssl rand -hex 24)
postgres_migrator_password=$(openssl rand -hex 24)
postgres_runtime_password=$(openssl rand -hex 24)
redis_password=$(openssl rand -hex 24)
admin_password=$(openssl rand -hex 20)

tmp=$(mktemp "${target}.tmp.XXXXXX")
secret_tmp=$(mktemp -d "${secret_dir}.tmp.XXXXXX")
cleanup() {
  if [ -n "${tmp:-}" ] && [ -f "$tmp" ]; then
    rm -f -- "$tmp"
  fi
  if [ -n "${secret_tmp:-}" ] && [ -d "$secret_tmp" ]; then
    rm -rf -- "$secret_tmp"
  fi
}
trap cleanup EXIT HUP INT TERM

{
  printf '%s\n' '# Generated by scripts/generate-env.sh. Keep this file mode 0600.'
  printf 'ALPHA_COMPOSE_PROJECT_NAME=%s\n' "$compose_project_name"
  printf 'ALPHA_SITE_ADDRESS=%s\n' "$site_address"
  printf 'ALPHA_HTTP_PORT=%s\n' "$http_port"
  printf 'ALPHA_HTTPS_PORT=%s\n' "$https_port"
  printf 'ALPHA_BIND_ADDRESS=%s\n' "$bind_address"
  printf 'ALPHA_ENVIRONMENT=%s\n' "$environment"
  printf 'ALPHA_COOKIE_SECURE=%s\n' "$cookie_secure"
  printf 'ALPHA_ALLOWED_ORIGINS=["%s"]\n' "$origin"
  printf 'ALPHA_TRUSTED_HOSTS=%s\n' "$trusted_hosts"
  printf '%s\n' 'ALPHA_FORWARDED_ALLOW_IPS=172.31.250.2'
  printf '%s\n' 'ALPHA_SESSION_TTL_HOURS=24' 'ALPHA_SESSION_CLEANUP_BATCH_SIZE=100'
  printf '%s\n' 'ALPHA_CSRF_TTL_SECONDS=3600' 'ALPHA_MAX_FLAG_LENGTH=512'
  printf '%s\n' \
    'ALPHA_POSTGRES_DB=alpha' \
    'ALPHA_POSTGRES_OWNER_USER=alpha' \
    'ALPHA_POSTGRES_MIGRATOR_USER=alpha_migrator' \
    'ALPHA_POSTGRES_RUNTIME_USER=alpha_app'
  printf 'ALPHA_ADMIN_EMAIL=%s\n' "$admin_email"
  printf 'ALPHA_ADMIN_USERNAME=%s\n' "$admin_username"
  printf '%s\n' 'ALPHA_ADMIN_BOOTSTRAPPED=false' 'ALPHA_SEED_DEMO=false'
  printf '%s\n' 'ALPHA_BACKUP_AGE_RECIPIENT='
  printf '%s\n' 'POSTGRES_IMAGE=cgr.dev/chainguard/postgres:latest@sha256:41a02d9c35a8dc6cac36188a0a201528ea8d686bb238af595867252821f609b9'
  printf '%s\n' 'REDIS_IMAGE=cgr.dev/chainguard/redis:latest@sha256:f639a439f5ab4f14486d6c6404f388b9dc076a25f85c6939630f2f805dfad969'
} > "$tmp"

printf '%s\n' "$secret_key" > "$secret_tmp/alpha_secret_key"
printf '%s\n' "$postgres_owner_password" > "$secret_tmp/postgres_owner_password"
printf '%s\n' "$postgres_migrator_password" > "$secret_tmp/postgres_migrator_password"
printf '%s\n' "$postgres_runtime_password" > "$secret_tmp/postgres_runtime_password"
printf '%s\n' "$redis_password" > "$secret_tmp/redis_password"
printf '%s\n' "$admin_password" > "$secret_tmp/admin_password"
chmod 700 "$secret_tmp"
chmod 600 "$secret_tmp"/*
# Docker Compose file-source secrets are bind mounts and preserve host access
# controls. Grant read access only to the pinned container UID while keeping
# the owning host account and all other users isolated.
"$script_dir/set-secret-acl.sh" "$secret_tmp" >/dev/null
chmod 600 "$tmp"
mv -- "$secret_tmp" "$secret_dir"
secret_tmp=
mv -- "$tmp" "$target"
tmp=

"$script_dir/validate-env.sh" "$target"

printf '%s\n' "환경 파일을 생성했습니다: $target"
printf '%s\n' "Compose secret 디렉터리를 생성했습니다: $secret_dir"
printf '%s\n' "초기 관리자 계정: $admin_email"
printf '%s\n' '초기 비밀번호는 .secrets/admin_password에만 저장했습니다. 출력하거나 채팅·이슈에 복사하지 마세요.'
