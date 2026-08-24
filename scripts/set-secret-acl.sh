#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
secret_dir=${1:-"$app_root/.secrets"}

fail() {
  printf '오류: %s\n' "$1" >&2
  exit 1
}

command -v setfacl >/dev/null 2>&1 || fail 'setfacl(acl package)이 필요합니다.'
command -v getfacl >/dev/null 2>&1 || fail 'getfacl(acl package)이 필요합니다.'
[ -d "$secret_dir" ] || fail "secret 디렉터리가 없습니다: $secret_dir"
[ ! -L "$secret_dir" ] || fail "secret 디렉터리는 심볼릭 링크일 수 없습니다: $secret_dir"

case "$(basename -- "$secret_dir")" in
  .secrets|.secrets.tmp.*) ;;
  *) fail '대상 basename은 .secrets 또는 .secrets.tmp.* 이어야 합니다.' ;;
esac

setfacl -b -- "$secret_dir"
chmod 700 -- "$secret_dir"

for name in \
  alpha_secret_key \
  postgres_owner_password \
  postgres_migrator_password \
  postgres_runtime_password \
  redis_password \
  admin_password
do
  path=$secret_dir/$name
  [ -f "$path" ] || fail "일반 secret 파일이 없습니다: $path"
  [ ! -L "$path" ] || fail "secret 파일은 심볼릭 링크일 수 없습니다: $path"
  if find "$path" -prune -links +1 -print | grep -q .; then
    fail "secret 파일은 hard link일 수 없습니다: $path"
  fi
  setfacl -b -- "$path"
  chmod 600 -- "$path"
  setfacl -m u:65532:r-- -- "$path"
done

printf '%s\n' "container UID 65532 전용 read ACL을 적용했습니다: $secret_dir"
