#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
env_file=$app_root/.env
base_url=
insecure=false
timeout_seconds=120

usage() {
  cat <<'EOF'
사용법: ./scripts/smoke-test.sh [--url URL] [--timeout SECONDS] [--insecure]

기본 동작은 .env의 ALPHA_SITE_ADDRESS를 사용하며 TLS 인증서를 검증합니다.
--insecure는 명시적인 임시·사설 인증서 시험에서만 사용하세요.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --url)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      base_url=$2
      shift 2
      ;;
    --timeout)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      timeout_seconds=$2
      shift 2
      ;;
    --insecure)
      insecure=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

case "$timeout_seconds" in *[!0-9]*|'') printf '%s\n' 'timeout은 양의 정수여야 합니다.' >&2; exit 2 ;; esac
[ "$timeout_seconds" -gt 0 ] || { printf '%s\n' 'timeout은 0보다 커야 합니다.' >&2; exit 2; }

if [ -z "$base_url" ]; then
  [ -f "$env_file" ] || { printf '%s\n' '.env가 없으므로 --url을 지정하세요.' >&2; exit 2; }
  base_url=$(sed -n 's/^ALPHA_SITE_ADDRESS=//p' "$env_file")
  [ -n "$base_url" ] || { printf '%s\n' 'ALPHA_SITE_ADDRESS가 비어 있습니다.' >&2; exit 2; }
  case "$base_url" in
    http://localhost|http://127.0.0.1)
      local_port=$(sed -n 's/^ALPHA_HTTP_PORT=//p' "$env_file")
      if [ -n "$local_port" ] && [ "$local_port" != 80 ]; then
        base_url=$base_url:$local_port
      fi
      ;;
  esac
fi

case "$base_url" in
  http://*|https://*) ;;
  *) base_url=https://$base_url ;;
esac
base_url=${base_url%/}

command -v curl >/dev/null 2>&1 || { printf '%s\n' 'curl이 필요합니다.' >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { printf '%s\n' 'JSON 검증을 위해 python3가 필요합니다.' >&2; exit 2; }

tmp_dir=$(mktemp -d)
cleanup() {
  rm -rf -- "$tmp_dir"
}
trap cleanup EXIT HUP INT TERM

fetch() {
  output=$1
  url=$2
  if [ "$insecure" = true ]; then
    curl --insecure --fail --silent --show-error --location \
      --connect-timeout 5 --max-time 15 --output "$output" "$url"
  else
    curl --fail --silent --show-error --location \
      --connect-timeout 5 --max-time 15 --output "$output" "$url"
  fi
}

fetch_status() {
  url=$1
  if [ "$insecure" = true ]; then
    curl --insecure --silent --show-error --output /dev/null --write-out '%{http_code}' \
      --connect-timeout 5 --max-time 15 "$url"
  else
    curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
      --connect-timeout 5 --max-time 15 "$url"
  fi
}

deadline=$(( $(date +%s) + timeout_seconds ))
until fetch "$tmp_dir/live.json" "$base_url/api/v1/health/live" 2>/dev/null; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    printf '%s\n' "준비 상태 대기 시간이 초과됐습니다: $base_url" >&2
    exit 1
  fi
  sleep 2
done

fetch "$tmp_dir/meta.json" "$base_url/api/v1/meta"
fetch "$tmp_dir/index.html" "$base_url/"
readiness_status=$(fetch_status "$base_url/api/v1/health/ready")
[ "$readiness_status" = 404 ] || {
  printf '%s\n' "공개 readiness 경로가 차단되지 않았습니다(HTTP $readiness_status)." >&2
  exit 1
}

python3 - "$tmp_dir/live.json" "$tmp_dir/meta.json" <<'PY'
import json
import pathlib
import sys

responses = []
for raw in sys.argv[1:]:
    path = pathlib.Path(raw)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"유효한 JSON 응답이 아닙니다 ({path.name}): {exc}")
    if not isinstance(value, dict):
        raise SystemExit(f"JSON 객체 응답이 아닙니다: {path.name}")
    responses.append(value)

live, meta = responses
if live.get("status") != "ok":
    raise SystemExit("liveness status가 ok가 아닙니다")
for field in ("name", "version", "api_version"):
    value = meta.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"meta 응답의 {field}가 비어 있거나 문자열이 아닙니다")
PY

[ -s "$tmp_dir/index.html" ] || { printf '%s\n' '프런트엔드 응답이 비어 있습니다.' >&2; exit 1; }

printf '%s\n' "스모크 테스트 통과: $base_url"
printf '%s\n' '확인 범위: liveness, 공개 readiness 차단, meta JSON, 프런트엔드 문서'
