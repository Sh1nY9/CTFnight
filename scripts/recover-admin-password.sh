#!/bin/sh
set -eu

umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
env_file=$app_root/.env
admin_secret=$app_root/.secrets/admin_password
deployment_manifest=$app_root/security-reports/deployment-manifest.json
target_email=${1:-}
editor=${EDITOR:-vi}
recovery_active=0

fail() {
  printf '관리자 복구 오류: %s\n' "$1" >&2
  exit 1
}

set_bootstrap_marker() {
  marker=$1
  marker_count=$(grep -c '^ALPHA_ADMIN_BOOTSTRAPPED=' "$env_file" || true)
  [ "$marker_count" -eq 1 ] || fail 'ALPHA_ADMIN_BOOTSTRAPPED가 정확히 한 번 있어야 합니다.'
  marker_tmp=$(mktemp "$app_root/.env.admin-recovery.XXXXXX")
  awk -v marker="$marker" '
    /^ALPHA_ADMIN_BOOTSTRAPPED=/ { print "ALPHA_ADMIN_BOOTSTRAPPED=" marker; next }
    { print }
  ' "$env_file" > "$marker_tmp"
  chmod 600 "$marker_tmp"
  mv -- "$marker_tmp" "$env_file"
}

empty_admin_secret() {
  if [ -L "$admin_secret" ] || \
    [ ! -f "$admin_secret" ] || \
    find "$admin_secret" -prune -links +1 -print | grep -q .
  then
    rm -f -- "$admin_secret"
    : > "$admin_secret"
  else
    : > "$admin_secret"
  fi
}

restore_canonical_state() {
  recovery_status=$?
  trap - EXIT HUP INT TERM
  if [ "$recovery_active" -eq 1 ]; then
    set +e
    empty_admin_secret
    set_bootstrap_marker true
    "$script_dir/set-secret-acl.sh" >/dev/null
    "$script_dir/validate-env.sh" "$env_file" >/dev/null
    cleanup_status=$?
    set -e
    if [ "$cleanup_status" -ne 0 ]; then
      printf '%s\n' '관리자 복구 cleanup 실패: 공개 서비스를 재개하지 말고 env/secret을 점검하세요.' >&2
      recovery_status=1
    fi
  fi
  exit "$recovery_status"
}
trap restore_canonical_state EXIT
trap 'exit 130' HUP INT TERM

case "$target_email" in
  ''|*[[:space:][:cntrl:]]*|*@*@*|*@) fail '복구할 관리자 email 하나를 argument로 지정하세요.' ;;
esac
case "$editor" in
  ''|*[[:space:][:cntrl:]]*) fail 'EDITOR는 argument 없는 단일 executable 경로여야 합니다.' ;;
esac
command -v "$editor" >/dev/null 2>&1 || fail "EDITOR executable을 찾을 수 없습니다: $editor"
command -v flock >/dev/null 2>&1 || fail 'flock executable이 필요합니다.'
command -v python3 >/dev/null 2>&1 || fail 'python3 executable이 필요합니다.'

"$script_dir/validate-env.sh" "$env_file" >/dev/null
[ -d "$app_root/security-reports" ] && [ ! -L "$app_root/security-reports" ] || \
  fail 'security-reports는 실제 private directory여야 합니다.'
exec 9<"$app_root/security-reports"
flock -n 9 || fail '다른 security gate 또는 deployment 작업이 진행 중입니다.'
python3 "$script_dir/deployment-manifest.py" verify-prestart \
  --manifest "$deployment_manifest" --app-root "$app_root" --env-file "$env_file"
[ "$(sed -n 's/^ALPHA_ADMIN_BOOTSTRAPPED=//p' "$env_file")" = true ] || \
  fail '복구 시작 전 ALPHA_ADMIN_BOOTSTRAPPED=true여야 합니다.'
[ ! -s "$admin_secret" ] || fail '복구 시작 전 admin_password 파일은 비어 있어야 합니다.'

"$script_dir/compose.sh" stop caddy backend
recovery_active=1
set_bootstrap_marker false
"$editor" "$admin_secret"
"$script_dir/set-secret-acl.sh" >/dev/null
"$script_dir/validate-env.sh" "$env_file" >/dev/null
python3 "$script_dir/deployment-manifest.py" verify-prestart \
  --manifest "$deployment_manifest" --app-root "$app_root" --env-file "$env_file"

"$script_dir/compose.sh" run --rm --no-deps --pull never -T --entrypoint python migrate \
  -m alpha.cli set-password --email "$target_email"

empty_admin_secret
set_bootstrap_marker true
"$script_dir/set-secret-acl.sh" >/dev/null
"$script_dir/validate-env.sh" "$env_file" >/dev/null
flock -u 9 || fail 'deployment lock을 해제하지 못했습니다.'
exec 9<&-
recovery_active=0
trap - EXIT HUP INT TERM

make -C "$app_root" up
printf '%s\n' '관리자 비밀번호 복구와 기존 session 폐기를 완료했습니다. 새 비밀번호로 로그인해 다시 변경하세요.'
