#!/bin/sh
set -eu

umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
report_dir=$app_root/security-reports
trivy_cache_dir=$report_dir/.trivy-cache

# Official immutable release assets:
# https://github.com/aquasecurity/trivy/releases/tag/v0.74.0
trivy_version=0.74.0
trivy_archive=trivy_${trivy_version}_Linux-64bit.tar.gz
trivy_checksums=trivy_${trivy_version}_checksums.txt
trivy_archive_sha256=2ae6fe3ee734b7fdf11335663e18c75ea12dccc76062f09f164a3b0f8be4371a
trivy_checksums_sha256=bc701c3c3ee8b9acbea2c23257e41381e3854888f51281616a6ba5dc96963821
trivy_release_url=https://github.com/aquasecurity/trivy/releases/download/v${trivy_version}

fail() {
  printf 'security gate 오류: %s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "필수 명령을 찾을 수 없습니다: $1"
}

read_env_value() {
  value_file=$1
  value_key=$2
  value_count=$(grep -c "^${value_key}=" "$value_file" || true)
  [ "$value_count" -eq 1 ] || fail "$value_file 에 ${value_key}가 정확히 한 번 있어야 합니다."
  sed -n "s/^${value_key}=//p" "$value_file"
}

validate_image_ref() {
  checked_ref=$1
  case "$checked_ref" in
    *@sha256:*) ;;
    *) fail "image ref가 sha256 digest로 고정되지 않았습니다: $checked_ref" ;;
  esac
  checked_digest=${checked_ref##*@sha256:}
  [ "${#checked_digest}" -eq 64 ] || fail "image digest 길이가 올바르지 않습니다: $checked_ref"
  case "$checked_digest" in
    *[!0-9a-f]*) fail "image digest는 소문자 16진수여야 합니다: $checked_ref" ;;
  esac
}

require_command curl
require_command flock
require_command npm
require_command python3
require_command sha256sum
require_command tar

python3 "$script_dir/validate-compose-security.py" --self-test
python3 "$script_dir/deployment-manifest.py" --self-test

case ${COMPOSE_FILE:-}${COMPOSE_ENV_FILES:-} in
  '') ;;
  *) fail 'COMPOSE_FILE/COMPOSE_ENV_FILES override는 canonical 보안 렌더링에서 허용하지 않습니다.' ;;
esac

# Trivy uses Viper, so environment variables and cwd config/ignore files can
# silently override CLI defaults. Only the explicit private empty config files
# created below and the exact VEX allowlist may influence this gate.
if env | grep -qi '^TRIVY_'; then
  fail 'TRIVY_* environment override는 all-severity gate에서 허용하지 않습니다.'
fi

# VEX is an exact, single finding disposition rather than a general waiver.
# Reject any added statement or widened product/status before Trivy consumes it.
python3 - "$app_root/security/ctfnight.openvex.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
try:
    document = json.loads(path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"security gate 오류: VEX 문서를 읽지 못했습니다: {exc}")

statements = document.get("statements")
if not isinstance(statements, list) or len(statements) != 1:
    raise SystemExit("security gate 오류: VEX는 정확히 한 statement만 허용합니다.")
statement = statements[0]
expected = {
    "vulnerability": {"name": "GO-2026-5932"},
    "products": [{"@id": "pkg:golang/golang.org/x/crypto@v0.54.0"}],
    "status": "not_affected",
    "justification": "vulnerable_code_not_in_execute_path",
}
for key, value in expected.items():
    if statement.get(key) != value:
        raise SystemExit(f"security gate 오류: VEX {key}가 exact allowlist와 다릅니다.")
allowed_statement_keys = {*expected, "impact_statement"}
if set(statement) != allowed_statement_keys:
    raise SystemExit("security gate 오류: VEX statement에 허용되지 않은 필드가 있습니다.")
impact = statement.get("impact_statement")
if not isinstance(impact, str) or "golang.org/x/crypto/openpgp" not in impact:
    raise SystemExit("security gate 오류: VEX impact_statement 근거가 불완전합니다.")
PY

case $(uname -s):$(uname -m) in
  Linux:x86_64) ;;
  *) fail '고정 검증된 Trivy Linux-64bit(x86_64) 자산만 지원합니다.' ;;
esac

mkdir -p "$report_dir" "$trivy_cache_dir"

# Reports and canonical Compose tags are shared process state. Serialize the
# complete gate so concurrent operators cannot cross-read another run's report
# or retag an image between this run's build, scan, and manifest publication.
exec 9<"$report_dir"
flock -n 9 || fail '다른 security gate가 같은 project에서 실행 중입니다.'

image_env_file=$app_root/.env.example
if [ -e "$app_root/.env" ] || [ -L "$app_root/.env" ]; then
  [ -f "$app_root/.env" ] && [ ! -L "$app_root/.env" ] || fail '운영 .env는 일반 파일이어야 합니다.'
  "$app_root/scripts/validate-env.sh" "$app_root/.env"
  image_env_file=$app_root/.env
fi

# Snapshot every build, scan-policy, Compose and canonical env input before any
# audit starts. The manifest publisher checks the same digest after all scans,
# closing source/env drift during a long-running gate.
source_snapshot=$(python3 "$script_dir/deployment-manifest.py" snapshot \
  --app-root "$app_root" \
  --env-file "$image_env_file")
case $source_snapshot in
  *[!0-9a-f]*|'') fail 'deployment source snapshot 형식이 올바르지 않습니다.' ;;
esac
[ "${#source_snapshot}" -eq 64 ] || fail 'deployment source snapshot 길이가 올바르지 않습니다.'

# Starting a newer gate revokes the previous approval. Otherwise a newly
# discovered advisory could make this run fail while a still-fresh older
# manifest remained reusable for the same image IDs.
deployment_manifest=$report_dir/deployment-manifest.json
python3 "$script_dir/deployment-manifest.py" invalidate \
  --manifest "$deployment_manifest" \
  --app-root "$app_root"

tmp_base=${TMPDIR:-/tmp}
work_dir=$(mktemp -d "$tmp_base/ctfnight-security.XXXXXX")
trivy_config=$work_dir/trivy.yaml
trivy_ignorefile=$work_dir/.trivyignore
trivy_secret_config=$work_dir/trivy-secret.yaml
printf '{}\n' > "$trivy_config"
: > "$trivy_ignorefile"
printf '{}\n' > "$trivy_secret_config"
built_images=
gate_failed=0

cleanup() {
  cleanup_status=$?
  trap - 0 1 2 3 15
  for cleanup_image in $built_images; do
    docker image rm "$cleanup_image" >/dev/null 2>&1 || :
  done
  case $work_dir in
    "$tmp_base"/ctfnight-security.*) rm -rf -- "$work_dir" ;;
    *) printf 'security gate 경고: 예상하지 못한 임시 경로를 보존합니다: %s\n' "$work_dir" >&2 ;;
  esac
  exit "$cleanup_status"
}
trap cleanup 0
trap 'exit 130' 1 2 3 15

printf '1/4 Python hash-lock 감사를 준비합니다.\n'
audit_venv=$work_dir/audit-venv
python3 -m venv "$audit_venv"
"$audit_venv/bin/python" -m pip install \
  --disable-pip-version-check \
  --no-cache-dir \
  --quiet \
  --require-hashes \
  -r "$app_root/backend/requirements-bootstrap.lock"
"$audit_venv/bin/python" -m pip install \
  --disable-pip-version-check \
  --no-cache-dir \
  --quiet \
  --require-hashes \
  -r "$app_root/security/requirements.lock"
pip_audit=$audit_venv/bin/pip-audit
"$audit_venv/bin/python" "$app_root/scripts/check-backend-locks.py"

run_pip_audit() {
  audit_label=$1
  audit_lock=$2
  audit_report=$report_dir/pip-audit-$audit_label.json
  printf '  pip-audit: %s\n' "$audit_lock"
  if "$pip_audit" \
    --strict \
    --require-hashes \
    --disable-pip \
    --progress-spinner off \
    --format json \
    --output "$audit_report" \
    -r "$audit_lock"; then
    [ -s "$audit_report" ] || fail "pip-audit 보고서가 비어 있습니다: $audit_report"
  else
    audit_status=$?
    [ ! -f "$audit_report" ] || cat "$audit_report" >&2
    printf 'security gate 실패: pip-audit가 실패했습니다(%s): %s\n' "$audit_status" "$audit_lock" >&2
    gate_failed=1
  fi
}

run_pip_audit runtime "$app_root/backend/requirements.lock"
run_pip_audit test "$app_root/backend/requirements-test.lock"
run_pip_audit build "$app_root/backend/requirements-build.lock"
run_pip_audit bootstrap "$app_root/backend/requirements-bootstrap.lock"
run_pip_audit tooling "$app_root/security/requirements.lock"

printf '2/4 npm lock 감사를 실행합니다.\n'
npm_report=$report_dir/npm-audit.json
if npm --prefix "$app_root/frontend" audit \
  --audit-level=info \
  --registry=https://registry.npmjs.org/ \
  --offline=false \
  --prefer-online \
  --include=prod \
  --include=dev \
  --include=optional \
  --include=peer \
  --json >"$npm_report"; then
  [ -s "$npm_report" ] || fail "npm audit 보고서가 비어 있습니다: $npm_report"
else
  npm_status=$?
  [ ! -f "$npm_report" ] || cat "$npm_report" >&2
  printf 'security gate 실패: npm audit가 실패했습니다(%s). 알려진 취약점 또는 감사 오류를 확인하세요.\n' "$npm_status" >&2
  gate_failed=1
fi

printf '3/4 Trivy %s 공식 릴리스 자산을 검증합니다.\n' "$trivy_version"
checksums_path=$work_dir/$trivy_checksums
archive_path=$work_dir/$trivy_archive
curl \
  --fail \
  --location \
  --proto '=https' \
  --retry 3 \
  --show-error \
  --silent \
  --tlsv1.2 \
  --output "$checksums_path" \
  "$trivy_release_url/$trivy_checksums"
curl \
  --fail \
  --location \
  --proto '=https' \
  --retry 3 \
  --show-error \
  --silent \
  --tlsv1.2 \
  --output "$archive_path" \
  "$trivy_release_url/$trivy_archive"

printf '%s  %s\n' "$trivy_checksums_sha256" "$checksums_path" | sha256sum --check --strict
manifest_count=$(awk -v name="$trivy_archive" '$2 == name { count += 1 } END { print count + 0 }' "$checksums_path")
[ "$manifest_count" -eq 1 ] || fail "공식 checksum manifest의 Trivy 자산 항목 수가 1이 아닙니다: $manifest_count"
manifest_sha256=$(awk -v name="$trivy_archive" '$2 == name { print $1 }' "$checksums_path")
[ "$manifest_sha256" = "$trivy_archive_sha256" ] || fail '공식 checksum manifest가 고정된 Trivy archive digest와 다릅니다.'
printf '%s  %s\n' "$trivy_archive_sha256" "$archive_path" | sha256sum --check --strict

trivy_dir=$work_dir/trivy-bin
mkdir -p "$trivy_dir"
tar -xzf "$archive_path" -C "$trivy_dir" trivy
chmod 0555 "$trivy_dir/trivy"
trivy=$trivy_dir/trivy
installed_trivy_version=$("$trivy" --config "$trivy_config" --cache-dir "$trivy_cache_dir" --version | sed -n 's/^Version: //p' | sed -n '1p')
[ "$installed_trivy_version" = "$trivy_version" ] || fail "Trivy 실행 파일 버전이 다릅니다: $installed_trivy_version"

fs_report=$report_dir/trivy-fs.json
"$trivy" --config "$trivy_config" fs \
  --cache-dir "$trivy_cache_dir" \
  --timeout 15m \
  --no-progress \
  --include-dev-deps \
  --scanners vuln,misconfig,secret \
  --ignorefile "$trivy_ignorefile" \
  --secret-config "$trivy_secret_config" \
  --skip-dirs "$report_dir" \
  --skip-dirs "$app_root/.secrets" \
  --skip-dirs "$app_root/frontend/node_modules" \
  --skip-dirs "$app_root/.venv" \
  --skip-dirs "$app_root/.lock-venv" \
  --format json \
  --output "$fs_report" \
  "$app_root"
[ -s "$fs_report" ] || fail "Trivy 파일시스템 보고서가 비어 있습니다: $fs_report"
if "$trivy" --config "$trivy_config" convert \
  --cache-dir "$trivy_cache_dir" \
  --scanners vuln,misconfig,secret \
  --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL \
  --exit-code 1 \
  --format table \
  "$fs_report"; then
  :
else
  trivy_fs_status=$?
  printf 'security gate 실패: Trivy 파일시스템 정책 검사가 실패했습니다(%s).\n' "$trivy_fs_status" >&2
  gate_failed=1
fi

configured_postgres_image=$(read_env_value "$image_env_file" POSTGRES_IMAGE)
configured_redis_image=$(read_env_value "$image_env_file" REDIS_IMAGE)
# Exported values have the same precedence as Docker Compose and therefore must
# also be the exact references scanned before build/up.
postgres_image=${POSTGRES_IMAGE:-$configured_postgres_image}
redis_image=${REDIS_IMAGE:-$configured_redis_image}
validate_image_ref "$postgres_image"
validate_image_ref "$redis_image"

printf '4/4 Docker 최종 이미지 검사를 판정합니다.\n'
require_docker=${SECURITY_REQUIRE_DOCKER:-0}
case $require_docker in
  0|1) ;;
  *) fail 'SECURITY_REQUIRE_DOCKER는 0 또는 1이어야 합니다.' ;;
esac

docker_ready=0
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker_ready=1
fi

if [ "$docker_ready" -eq 0 ]; then
  [ "$require_docker" -eq 0 ] || fail 'Docker 이미지 검사가 필수지만 Docker daemon을 사용할 수 없습니다.'
  printf 'Docker daemon을 사용할 수 없어 최종 이미지 검사만 건너뜁니다. CI에서는 필수입니다.\n'
  current_source_snapshot=$(python3 "$script_dir/deployment-manifest.py" snapshot \
    --app-root "$app_root" \
    --env-file "$image_env_file")
  [ "$current_source_snapshot" = "$source_snapshot" ] || fail '비-Docker 보안 검사 중 deployment source 또는 env가 변경되었습니다.'
  [ "$gate_failed" -eq 0 ] || fail '하나 이상의 비-Docker 보안 검사가 실패했습니다.'
  printf '비-Docker security gate 통과. 보고서: %s\n' "$report_dir"
  exit 0
fi

docker compose version >/dev/null 2>&1 || fail 'Docker Compose plugin을 사용할 수 없습니다.'
rendered_config=$work_dir/compose-rendered.json
docker compose \
  --project-directory "$app_root" \
  --env-file "$image_env_file" \
  -f "$app_root/compose.yaml" \
  config --format json > "$rendered_config"

rendered_images=$work_dir/rendered-images
python3 "$script_dir/validate-compose-security.py" \
  "$rendered_config" "$app_root" "$image_env_file" > "$rendered_images"

rendered_project=$(sed -n 's/^project=//p' "$rendered_images")
rendered_postgres_image=$(sed -n 's/^postgres=//p' "$rendered_images")
rendered_redis_image=$(sed -n 's/^redis=//p' "$rendered_images")
[ -n "$rendered_project" ] || fail '렌더링된 canonical Compose project 이름이 없습니다.'
[ "$rendered_postgres_image" = "$postgres_image" ] || fail '렌더링된 PostgreSQL image가 검사 대상과 다릅니다.'
[ "$rendered_redis_image" = "$redis_image" ] || fail '렌더링된 Redis image가 검사 대상과 다릅니다.'
postgres_image=$rendered_postgres_image
redis_image=$rendered_redis_image

backend_image=$rendered_project-backend
caddy_image=$rendered_project-caddy
frontend_image=$rendered_project-frontend

inspect_image_id() {
  inspect_ref=$1
  inspect_id=$(docker image inspect --format '{{.Id}}' "$inspect_ref")
  case $inspect_id in
    sha256:*) ;;
    *) fail "local image ID 형식이 올바르지 않습니다: $inspect_ref" ;;
  esac
  inspect_digest=${inspect_id#sha256:}
  [ "${#inspect_digest}" -eq 64 ] || fail "local image ID 길이가 올바르지 않습니다: $inspect_ref"
  case $inspect_digest in
    *[!0-9a-f]*) fail "local image ID는 소문자 sha256이어야 합니다: $inspect_ref" ;;
  esac
  inspect_platform=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$inspect_id")
  [ "$inspect_platform" = linux/amd64 ] || fail "image platform이 linux/amd64가 아닙니다: $inspect_ref ($inspect_platform)"
  printf '%s\n' "$inspect_id"
}

scan_existing_image() {
  image_component=$1
  image_ref=$2
  image_report=$report_dir/trivy-image-$image_component.json
  image_sbom=$report_dir/trivy-image-$image_component.cdx.json

  case $image_component in
    caddy*)
    set -- --skip-vex-repo-update --vex "$app_root/security/ctfnight.openvex.json"
    ;;
    *)
    set --
    ;;
  esac
  "$trivy" --config "$trivy_config" image \
    --cache-dir "$trivy_cache_dir" \
    --timeout 15m \
    --no-progress \
    --image-src docker \
    --scanners vuln \
    --ignorefile "$trivy_ignorefile" \
    --secret-config "$trivy_secret_config" \
    "$@" \
    --format json \
    --output "$image_report" \
    "$image_ref"
  [ -s "$image_report" ] || fail "Trivy 이미지 보고서가 비어 있습니다: $image_report"

  if "$trivy" --config "$trivy_config" convert \
    --cache-dir "$trivy_cache_dir" \
    --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL \
    --exit-code 1 \
    --format table \
    "$image_report"; then
    :
  else
    trivy_image_status=$?
    printf 'security gate 실패: %s 이미지 CVE 검사가 실패했습니다(%s).\n' "$image_component" "$trivy_image_status" >&2
    gate_failed=1
  fi
  "$trivy" --config "$trivy_config" convert \
    --cache-dir "$trivy_cache_dir" \
    --format cyclonedx \
    --output "$image_sbom" \
    "$image_report"
  [ -s "$image_sbom" ] || fail "CycloneDX SBOM이 비어 있습니다: $image_sbom"
}

build_and_scan_stage() {
  image_component=$1
  image_context=$2
  image_target=$3
  stage_ref=ctfnight-security-$image_component:scan-$$
  printf '  builder stage 임시 빌드/검사: %s\n' "$image_component"
  docker build --pull --platform linux/amd64 --target "$image_target" --tag "$stage_ref" "$image_context"
  built_images="$built_images $stage_ref"
  stage_id=$(inspect_image_id "$stage_ref")
  scan_existing_image "$image_component" "$stage_id"
}

build_and_scan_stage backend-builder "$app_root/backend" builder
build_and_scan_stage frontend-builder "$app_root/frontend" build
build_and_scan_stage caddy-builder "$app_root/deploy/caddy" builder

# This is the one and only final application-image build in the deployment
# path. The exact local image IDs produced here are scanned and later deployed;
# Make must not invoke another build after this point.
printf '  canonical Compose 최종 이미지 단일 빌드: backend, frontend, caddy\n'
docker compose \
  --project-directory "$app_root" \
  --env-file "$image_env_file" \
  -f "$app_root/compose.yaml" \
  build --pull backend frontend caddy

backend_image_id=$(inspect_image_id "$backend_image")
caddy_image_id=$(inspect_image_id "$caddy_image")
frontend_image_id=$(inspect_image_id "$frontend_image")

printf '  고정 인프라 이미지 pull: postgres, redis\n'
docker pull --platform linux/amd64 "$postgres_image"
postgres_image_id=$(inspect_image_id "$postgres_image")
docker pull --platform linux/amd64 "$redis_image"
redis_image_id=$(inspect_image_id "$redis_image")

scan_existing_image backend "$backend_image_id"
scan_existing_image frontend "$frontend_image_id"
scan_existing_image caddy "$caddy_image_id"
scan_existing_image postgres "$postgres_image_id"
scan_existing_image redis "$redis_image_id"

[ "$gate_failed" -eq 0 ] || fail '하나 이상의 보안 검사가 실패했습니다.'

python3 "$script_dir/deployment-manifest.py" create \
  --manifest "$deployment_manifest" \
  --app-root "$app_root" \
  --env-file "$image_env_file" \
  --rendered-config "$rendered_config" \
  --expected-source-sha256 "$source_snapshot" \
  --trivy-version "$trivy_version" \
  --image backend "$backend_image" "$backend_image_id" \
  --image caddy "$caddy_image" "$caddy_image_id" \
  --image frontend "$frontend_image" "$frontend_image_id" \
  --image postgres "$postgres_image" "$postgres_image_id" \
  --image redis "$redis_image" "$redis_image_id"
python3 "$script_dir/deployment-manifest.py" verify-prestart \
  --manifest "$deployment_manifest" \
  --app-root "$app_root" \
  --env-file "$image_env_file"
printf '전체 security gate 통과. 보고서와 SBOM: %s\n' "$report_dir"
