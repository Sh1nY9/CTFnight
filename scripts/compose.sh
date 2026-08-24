#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
env_file=$app_root/.env

fail() {
  printf '오류: %s\n' "$1" >&2
  exit 1
}

[ -f "$env_file" ] && [ ! -L "$env_file" ] || fail 'canonical .env 일반 파일이 필요합니다.'
[ "$#" -gt 0 ] || fail 'Compose subcommand가 필요합니다.'
case "$1" in
  config|build|up|exec|stop|run|logs|ps|down) ;;
  *) fail "허용되지 않은 Compose subcommand입니다: $1" ;;
esac
for argument in "$@"; do
  case "$argument" in
    -f|-f=*|--file|--file=*|--env-file|--env-file=*|-p|-p=*|--project-name|--project-name=*|\
    --project-directory|--project-directory=*|--profile|--profile=*)
      fail "canonical graph를 바꾸는 command option은 허용하지 않습니다: $argument"
      ;;
  esac
done

# The deployment graph is rendered only from the validated canonical file.
# Exported interpolation or Compose graph overrides could otherwise make the
# command operate on services/images different from the security gate target.
for override_name in \
  COMPOSE_FILE COMPOSE_ENV_FILES COMPOSE_PROJECT_NAME COMPOSE_PROFILES COMPOSE_PATH_SEPARATOR \
  ALPHA_COMPOSE_PROJECT_NAME ALPHA_SITE_ADDRESS ALPHA_HTTP_PORT ALPHA_HTTPS_PORT \
  ALPHA_BIND_ADDRESS ALPHA_ENVIRONMENT ALPHA_COOKIE_SECURE ALPHA_ALLOWED_ORIGINS \
  ALPHA_TRUSTED_HOSTS ALPHA_FORWARDED_ALLOW_IPS ALPHA_SESSION_TTL_HOURS \
  ALPHA_SESSION_CLEANUP_BATCH_SIZE ALPHA_CSRF_TTL_SECONDS ALPHA_MAX_FLAG_LENGTH \
  ALPHA_POSTGRES_DB ALPHA_POSTGRES_OWNER_USER ALPHA_POSTGRES_MIGRATOR_USER \
  ALPHA_POSTGRES_RUNTIME_USER ALPHA_ADMIN_EMAIL ALPHA_ADMIN_USERNAME \
  ALPHA_SEED_DEMO POSTGRES_IMAGE REDIS_IMAGE
do
  if printenv "$override_name" >/dev/null 2>&1; then
    fail "runtime Compose override는 허용하지 않습니다: $override_name"
  fi
done

"$script_dir/validate-env.sh" "$env_file" >/dev/null
exec docker compose \
  --project-directory "$app_root" \
  --env-file "$env_file" \
  -f "$app_root/compose.yaml" \
  "$@"
