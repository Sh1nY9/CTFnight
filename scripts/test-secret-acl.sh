#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
test_root=$(mktemp -d /tmp/ctfnight-secret-acl.XXXXXX)
cleanup() {
  rm -rf -- "$test_root"
}
trap cleanup EXIT HUP INT TERM

"$script_dir/generate-env.sh" "$test_root/.env" >/dev/null
secret_path=$test_root/.secrets/alpha_secret_key

setfacl -b -- "$secret_path"
chmod 600 -- "$secret_path"
if "$script_dir/validate-env.sh" "$test_root/.env" >/dev/null 2>&1; then
  printf '%s\n' '오류: container UID ACL이 없는 secret을 validator가 허용했습니다.' >&2
  exit 1
fi

"$script_dir/set-secret-acl.sh" "$test_root/.secrets" >/dev/null
"$script_dir/validate-env.sh" "$test_root/.env" >/dev/null

ln -- "$secret_path" "$test_root/secret-hardlink"
if "$script_dir/set-secret-acl.sh" "$test_root/.secrets" >/dev/null 2>&1; then
  printf '%s\n' '오류: hard-linked secret을 ACL helper가 허용했습니다.' >&2
  exit 1
fi
rm -f -- "$test_root/secret-hardlink"
"$script_dir/set-secret-acl.sh" "$test_root/.secrets" >/dev/null
"$script_dir/validate-env.sh" "$test_root/.env" >/dev/null

same_port_env=$test_root/.env.same-port
cp -- "$test_root/.env" "$same_port_env"
sed -i 's/^ALPHA_HTTPS_PORT=.*/ALPHA_HTTPS_PORT=80/' "$same_port_env"
chmod 600 "$same_port_env"
if "$script_dir/validate-env.sh" "$same_port_env" >/dev/null 2>&1; then
  printf '%s\n' '오류: 동일 HTTP/HTTPS host port를 validator가 허용했습니다.' >&2
  exit 1
fi

printf '%s\n' 'secret ACL 회귀 테스트 통과'
