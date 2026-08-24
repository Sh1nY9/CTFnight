#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
test_root=$(mktemp -d "${TMPDIR:-/tmp}/alpha-backup-race.XXXXXX")

cleanup() {
  rm -rf -- "$test_root"
}
trap cleanup EXIT HUP INT TERM

fixture=$test_root/app
fake_bin=$test_root/bin
destination=$test_root/backups
mkdir -p "$fixture/scripts" "$fixture/deploy" "$fake_bin"
mkdir -m 700 "$destination"

cp -- "$app_root/scripts/backup.sh" "$fixture/scripts/backup.sh"
cp -- "$app_root/scripts/compose.sh" "$fixture/scripts/compose.sh"
cp -- "$app_root/scripts/validate-env.sh" "$fixture/scripts/validate-env.sh"
cp -- "$app_root/compose.yaml" "$fixture/compose.yaml"
cp -- "$app_root/deploy/Caddyfile" "$fixture/deploy/Caddyfile"
ALPHA_COMPOSE_PROJECT_NAME=alpha-backup-race \
  "$app_root/scripts/generate-env.sh" "$fixture/.env" >/dev/null

{
  printf '%s\n' '#!/bin/sh' 'set -eu'
  printf '%s\n' 'case " $* " in'
  printf '%s\n' "  *' exec -T postgres '*)"
  printf '%s\n' '    sleep 1'
  printf '%s\n' "    printf '%s\\n' 'synthetic pg_dump'"
  printf '%s\n' '    ;;'
  printf '%s\n' "  *' config --images '*)"
  printf '%s\n' "    printf '%s\\n' 'alpha-backup-race-backend' 'cgr.dev/chainguard/postgres@sha256:synthetic'"
  printf '%s\n' '    ;;'
  printf '%s\n' '  *) exit 64 ;;'
  printf '%s\n' 'esac'
} > "$fake_bin/docker"

{
  printf '%s\n' '#!/bin/sh'
  printf '%s\n' "printf '%s\\n' '20260824T000000Z'"
} > "$fake_bin/date"

{
  printf '%s\n' '#!/bin/sh' 'set -eu' 'output='
  printf '%s\n' 'while [ "$#" -gt 0 ]; do'
  printf '%s\n' '  case "$1" in'
  printf '%s\n' '    --output) output=$2; shift 2 ;;'
  printf '%s\n' '    --recipient) shift 2 ;;'
  printf '%s\n' '    --encrypt) shift ;;'
  printf '%s\n' '    *) exit 64 ;;'
  printf '%s\n' '  esac'
  printf '%s\n' 'done'
  printf '%s\n' '[ -n "$output" ]'
  printf '%s\n' 'cat > "$output"'
} > "$fake_bin/age"

{
  printf '%s\n' '#!/bin/sh' 'set -eu'
  printf '%s\n' 'case " $* " in'
  printf '%s\n' "  *'.ctfnight-publish-'*)"
  printf '%s\n' '    if [ "${BACKUP_RACE_ROLE:-}" = delayed ]; then'
  printf '%s\n' '      : > "$BACKUP_RACE_ROOT/delayed-at-lock"'
  printf '%s\n' '      while [ ! -e "$BACKUP_RACE_ROOT/release-delayed" ]; do sleep 0.02; done'
  printf '%s\n' '    fi'
  printf '%s\n' '    ;;'
  printf '%s\n' 'esac'
  printf '%s\n' 'exec "$BACKUP_REAL_MKDIR" "$@"'
} > "$fake_bin/mkdir"
chmod 700 "$fake_bin/docker" "$fake_bin/date" "$fake_bin/age" "$fake_bin/mkdir"

real_mkdir=$(command -v mkdir)
BACKUP_RACE_ROLE=delayed BACKUP_RACE_ROOT="$test_root" BACKUP_REAL_MKDIR="$real_mkdir" \
  ALPHA_BACKUP_AGE_RECIPIENT=age1synthetic \
  PATH="$fake_bin:$PATH" sh "$fixture/scripts/backup.sh" "$destination" \
  >"$test_root/delayed.out" 2>"$test_root/delayed.err" &
delayed_pid=$!

attempt=0
while [ ! -e "$test_root/delayed-at-lock" ] && [ "$attempt" -lt 250 ]; do
  sleep 0.02
  attempt=$((attempt + 1))
done
if [ ! -e "$test_root/delayed-at-lock" ]; then
  : > "$test_root/release-delayed"
  set +e
  wait "$delayed_pid"
  set -e
  printf '%s\n' '오류: 지연 백업이 publish lock 경계에 도달하지 못했습니다.' >&2
  exit 1
fi

BACKUP_RACE_ROLE=publisher BACKUP_RACE_ROOT="$test_root" BACKUP_REAL_MKDIR="$real_mkdir" \
  ALPHA_BACKUP_AGE_RECIPIENT=age1synthetic \
  PATH="$fake_bin:$PATH" sh "$fixture/scripts/backup.sh" "$destination" \
  >"$test_root/publisher.out" 2>"$test_root/publisher.err" &
publisher_pid=$!

set +e
wait "$publisher_pid"
publisher_status=$?
: > "$test_root/release-delayed"
wait "$delayed_pid"
delayed_status=$?
set -e

if [ "$publisher_status" -ne 0 ] || [ "$delayed_status" -eq 0 ]; then
  sed -n '1,120p' "$test_root/publisher.err" >&2
  sed -n '1,120p' "$test_root/delayed.err" >&2
  printf '오류: 먼저 publish한 백업만 성공해야 합니다. publisher=%s, delayed=%s\n' \
    "$publisher_status" "$delayed_status" >&2
  exit 1
fi

final_count=$(find "$destination" -mindepth 1 -maxdepth 1 -type f -name 'ctfnight-*.tar.gz.age' | wc -l)
[ "$final_count" -eq 1 ] || { printf '%s\n' '오류: 완성된 백업은 하나여야 합니다.' >&2; exit 1; }
if find "$destination" -mindepth 1 -maxdepth 1 -name '.ctfnight-*' -print | grep -q .; then
  printf '%s\n' '오류: 임시 백업 또는 publish lock이 남았습니다.' >&2
  exit 1
fi

final_archive=$(find "$destination" -mindepth 1 -maxdepth 1 -type f -name 'ctfnight-*.tar.gz.age' -print)
(cd "$destination" && sha256sum -c "$(basename "$final_archive").sha256" >/dev/null)
restore_dir=$test_root/restore
mkdir -m 700 "$restore_dir"
tar -xzf "$final_archive" -C "$restore_dir"
(cd "$restore_dir" && sha256sum -c SHA256SUMS >/dev/null)
[ -f "$restore_dir/secrets/alpha_secret_key" ] || {
  printf '%s\n' '오류: 암호화 archive에 복구 secret이 없습니다.' >&2
  exit 1
}
for restored_secret in \
  postgres_owner_password \
  postgres_migrator_password \
  postgres_runtime_password
do
  [ -f "$restore_dir/secrets/$restored_secret" ] || {
    printf '오류: 암호화 archive에 DB role secret이 없습니다: %s\n' "$restored_secret" >&2
    exit 1
  }
done

printf '%s\n' '병렬 백업 publish 경쟁 조건 검증 통과'
