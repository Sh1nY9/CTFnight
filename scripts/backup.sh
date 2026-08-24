#!/bin/sh
set -eu

umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
env_file=$app_root/.env
secret_dir=$app_root/.secrets
destination_root=${1:-"$app_root/backups"}
staging_root=${ALPHA_BACKUP_TMPDIR:-/dev/shm}

fail() {
  printf '오류: %s\n' "$1" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail 'docker가 필요합니다.'
command -v age >/dev/null 2>&1 || fail '평문 백업 게시를 막기 위해 age가 필요합니다.'
command -v tar >/dev/null 2>&1 || fail 'tar가 필요합니다.'
"$script_dir/validate-env.sh" "$env_file"

recipient=${ALPHA_BACKUP_AGE_RECIPIENT:-}
if [ -z "$recipient" ]; then
  recipient=$(sed -n 's/^ALPHA_BACKUP_AGE_RECIPIENT=//p' "$env_file")
fi
[ -n "$recipient" ] || fail 'ALPHA_BACKUP_AGE_RECIPIENT에 age 공개 recipient를 설정하세요.'
case "$recipient" in
  age1*|age-plugin-*) ;;
  *) fail 'ALPHA_BACKUP_AGE_RECIPIENT 형식이 올바르지 않습니다.' ;;
esac

if [ -L "$destination_root" ]; then
  fail '백업 대상 루트는 심볼릭 링크일 수 없습니다.'
fi
if [ -e "$destination_root" ]; then
  [ -d "$destination_root" ] || fail '백업 대상이 디렉터리가 아닙니다.'
else
  mkdir -p -m 700 -- "$destination_root"
fi
destination_root=$(CDPATH= cd -P -- "$destination_root" && pwd)
case "$destination_root" in
  /|"$app_root") fail '백업 대상은 파일시스템 루트나 애플리케이션 루트일 수 없습니다.' ;;
esac
if find "$destination_root" -prune -perm /077 -print | grep -q .; then
  fail "백업 대상 권한이 너무 넓습니다. 먼저 chmod 700 '$destination_root'을 실행하세요."
fi

[ -d "$staging_root" ] && [ -w "$staging_root" ] || fail "암호화 전 staging 경로를 사용할 수 없습니다: $staging_root"
[ ! -L "$staging_root" ] || fail '암호화 전 staging 경로는 심볼릭 링크일 수 없습니다.'
staging_type=$(df -PT "$staging_root" | awk 'NR == 2 {print $2}')
[ "$staging_type" = tmpfs ] || fail "평문 staging은 tmpfs여야 합니다: $staging_root ($staging_type)"

tmp_dir=
encrypted_tmp=
checksum_tmp=
plaintext_archive=
publish_lock=
cleanup() {
  if [ -n "${tmp_dir:-}" ] && [ -d "$tmp_dir" ]; then
    rm -rf -- "$tmp_dir"
  fi
  if [ -n "${encrypted_tmp:-}" ] && [ -f "$encrypted_tmp" ]; then
    rm -f -- "$encrypted_tmp"
  fi
  if [ -n "${checksum_tmp:-}" ] && [ -f "$checksum_tmp" ]; then
    rm -f -- "$checksum_tmp"
  fi
  if [ -n "${plaintext_archive:-}" ] && [ -f "$plaintext_archive" ]; then
    rm -f -- "$plaintext_archive"
  fi
  if [ -n "${publish_lock:-}" ] && [ -d "$publish_lock" ]; then
    rmdir -- "$publish_lock"
  fi
}
trap cleanup EXIT HUP INT TERM

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
archive_name=ctfnight-$timestamp.tar.gz.age
final_archive=$destination_root/$archive_name
final_checksum=$final_archive.sha256
publish_lock_candidate=$destination_root/.ctfnight-publish-$timestamp.lock
if ! mkdir -m 700 -- "$publish_lock_candidate" 2>/dev/null; then
  fail "같은 시각의 백업이 이미 진행 중입니다: $final_archive"
fi
publish_lock=$publish_lock_candidate
[ ! -e "$final_archive" ] && [ ! -e "$final_checksum" ] || fail "백업 경로가 이미 있습니다: $final_archive"

tmp_dir=$(mktemp -d "$staging_root/ctfnight-backup.XXXXXX")
mkdir -m 700 -- "$tmp_dir/secrets"

cd "$app_root"
"$script_dir/compose.sh" exec -T postgres sh -ec \
  'pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format=custom --no-owner' \
  > "$tmp_dir/database.dump"

cp -- "$env_file" "$tmp_dir/.env"
cp -- "$secret_dir/alpha_secret_key" "$tmp_dir/secrets/alpha_secret_key"
cp -- "$secret_dir/postgres_owner_password" "$tmp_dir/secrets/postgres_owner_password"
cp -- "$secret_dir/postgres_migrator_password" "$tmp_dir/secrets/postgres_migrator_password"
cp -- "$secret_dir/postgres_runtime_password" "$tmp_dir/secrets/postgres_runtime_password"
cp -- "$secret_dir/redis_password" "$tmp_dir/secrets/redis_password"
cp -- "$secret_dir/admin_password" "$tmp_dir/secrets/admin_password"
cp -- compose.yaml "$tmp_dir/compose.yaml"
cp -- deploy/Caddyfile "$tmp_dir/Caddyfile"
"$script_dir/compose.sh" config --images > "$tmp_dir/images.txt"

source_revision=unversioned
source_dirty=unknown
if git -C "$app_root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  source_revision=$(git -C "$app_root" rev-parse HEAD)
  if [ -n "$(git -C "$app_root" status --porcelain)" ]; then
    source_dirty=true
  else
    source_dirty=false
  fi
fi

{
  printf 'created_utc=%s\n' "$timestamp"
  printf '%s\n' 'format=age-encrypted tar.gz' 'database=PostgreSQL custom-format dump'
  printf 'source_revision=%s\n' "$source_revision"
  printf 'source_dirty=%s\n' "$source_dirty"
  printf '%s\n' 'redis=excluded (cache and rate-limit state is disposable)'
  printf '%s\n' 'caddy_data=excluded (certificates are automatically reissued)'
} > "$tmp_dir/manifest.txt"

(
  cd "$tmp_dir"
  sha256sum \
    database.dump .env compose.yaml Caddyfile images.txt manifest.txt \
    secrets/alpha_secret_key secrets/postgres_owner_password \
    secrets/postgres_migrator_password secrets/postgres_runtime_password \
    secrets/redis_password secrets/admin_password > SHA256SUMS
)
chmod 600 \
  "$tmp_dir/database.dump" "$tmp_dir/.env" "$tmp_dir/compose.yaml" \
  "$tmp_dir/Caddyfile" "$tmp_dir/images.txt" "$tmp_dir/manifest.txt" \
  "$tmp_dir/SHA256SUMS" "$tmp_dir/secrets"/*
chmod 700 "$tmp_dir/secrets"

encrypted_tmp=$(mktemp "$destination_root/.ctfnight-encrypted.XXXXXX")
plaintext_archive=$(mktemp "$staging_root/ctfnight-archive.XXXXXX.tar.gz")
tar -C "$tmp_dir" -czf "$plaintext_archive" .
age --encrypt --recipient "$recipient" --output "$encrypted_tmp" < "$plaintext_archive"
rm -f -- "$plaintext_archive"
plaintext_archive=
chmod 600 "$encrypted_tmp"
mv -- "$encrypted_tmp" "$final_archive"
encrypted_tmp=

checksum_tmp=$(mktemp "$destination_root/.ctfnight-checksum.XXXXXX")
(
  cd "$destination_root"
  sha256sum "$archive_name"
) > "$checksum_tmp"
chmod 600 "$checksum_tmp"
mv -- "$checksum_tmp" "$final_checksum"
checksum_tmp=

rmdir -- "$publish_lock"
publish_lock=

printf '%s\n' "암호화 백업 완료: $final_archive"
printf '%s\n' "무결성 checksum: $final_checksum"
printf '%s\n' '복구 전 age로 복호화한 뒤 내부 SHA256SUMS도 반드시 검증하세요.'
