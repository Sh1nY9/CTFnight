#!/usr/bin/env python3
"""Create and verify an atomic manifest for the exact images CTFnight deploys."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from typing import Any


class ManifestError(RuntimeError):
    pass


SCHEMA = "ctfnight-deployment-manifest-v1"
MAX_MANIFEST_AGE = timedelta(hours=2)
MAX_CLOCK_SKEW = timedelta(minutes=5)
IMAGE_SERVICES = ("backend", "caddy", "frontend", "postgres", "redis")
LOCAL_IMAGE_SERVICES = ("backend", "caddy", "frontend")
COMPOSE_SERVICES = (
    "backend",
    "caddy",
    "db-roles",
    "frontend",
    "migrate",
    "postgres",
    "redis",
)
PERSISTENT_SERVICES = {"backend", "caddy", "frontend", "postgres", "redis"}
ONE_SHOT_SERVICES = {"db-roles", "migrate"}
SERVICE_IMAGE = {
    "backend": "backend",
    "caddy": "caddy",
    "db-roles": "postgres",
    "frontend": "frontend",
    "migrate": "backend",
    "postgres": "postgres",
    "redis": "redis",
}
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
PROJECT_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".lock-venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tooling",
    ".venv",
    "__pycache__",
    "coverage",
    "dist",
    "htmlcov",
    "node_modules",
}
EXCLUDED_FILE_NAMES = {".coverage", ".DS_Store"}
EXCLUDED_SUFFIXES = {".db", ".log", ".pyc", ".sqlite", ".tsbuildinfo"}
POLICY_FILES = (
    "scripts/check-backend-locks.py",
    "scripts/compose.sh",
    "scripts/deployment-manifest.py",
    "scripts/recover-admin-password.sh",
    "scripts/security-scan.sh",
    "scripts/set-secret-acl.sh",
    "scripts/validate-compose-security.py",
    "scripts/validate-env.sh",
    "security/ctfnight.openvex.json",
    "security/requirements.in",
    "security/requirements.lock",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestError(message)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def absolute_without_following_leaf(path: pathlib.Path) -> pathlib.Path:
    """Make a path absolute without resolving a potentially hostile leaf symlink."""

    return pathlib.Path(os.path.abspath(os.fspath(path)))


def read_json_file(path: pathlib.Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ManifestError(f"{label}을 읽지 못했습니다: {exc}") from exc
    require(len(raw) <= 4 * 1024 * 1024, f"{label}이 비정상적으로 큽니다.")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{label}이 유효한 JSON이 아닙니다: {exc}") from exc
    require(isinstance(value, dict), f"{label} 최상위 값은 object여야 합니다.")
    return value


def ensure_private_directory(path: pathlib.Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ManifestError(f"manifest 디렉터리를 검사하지 못했습니다: {exc}") from exc
    require(stat.S_ISDIR(metadata.st_mode), "manifest 디렉터리는 symlink가 아닌 실제 디렉터리여야 합니다.")
    require(metadata.st_uid == os.getuid(), "manifest 디렉터리는 현재 운영 계정 소유여야 합니다.")
    require(stat.S_IMODE(metadata.st_mode) & 0o077 == 0, "manifest 디렉터리는 group/other 접근을 허용할 수 없습니다.")


def should_exclude_file(name: str) -> bool:
    return name in EXCLUDED_FILE_NAMES or pathlib.Path(name).suffix in EXCLUDED_SUFFIXES


def iter_tree_files(app_root: pathlib.Path, relative_root: str) -> list[pathlib.Path]:
    root = app_root / relative_root
    require(root.is_dir() and not root.is_symlink(), f"deployment input 디렉터리가 올바르지 않습니다: {relative_root}")
    found: list[pathlib.Path] = []
    for directory, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        directory_names[:] = sorted(
            name for name in directory_names if name not in EXCLUDED_DIRECTORY_NAMES
        )
        base = pathlib.Path(directory)
        for name in sorted(file_names):
            if should_exclude_file(name):
                continue
            found.append(base / name)
        for name in directory_names:
            candidate = base / name
            if candidate.is_symlink():
                found.append(candidate)
    return found


def deployment_input_paths(app_root: pathlib.Path) -> list[pathlib.Path]:
    required_files = [app_root / "Makefile", app_root / "compose.yaml"]
    for path in required_files:
        require(path.is_file() and not path.is_symlink(), f"deployment input 일반 파일이 없습니다: {path}")
    paths = list(required_files)
    for relative_root in ("backend", "frontend", "deploy"):
        paths.extend(iter_tree_files(app_root, relative_root))
    for relative in POLICY_FILES:
        path = app_root / relative
        require(path.is_file() and not path.is_symlink(), f"deployment policy 일반 파일이 없습니다: {relative}")
        paths.append(path)
    unique = {path.relative_to(app_root).as_posix(): path for path in paths}
    return [unique[name] for name in sorted(unique)]


def deployment_env_bytes(env_file: pathlib.Path) -> bytes:
    """Hash deployment inputs while ignoring the non-Compose recovery marker."""

    try:
        text = env_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ManifestError(f"canonical env를 읽지 못했습니다: {exc}") from exc
    key = "ALPHA_ADMIN_BOOTSTRAPPED="
    marker_count = 0
    normalized: list[str] = []
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content) :]
        if content.startswith(key):
            marker_count += 1
            require(content in {key + "true", key + "false"}, "admin bootstrap marker 값이 올바르지 않습니다.")
            content = key + "<operational-marker>"
        normalized.append(content + ending)
    require(marker_count == 1, "canonical env에 admin bootstrap marker가 정확히 한 번 있어야 합니다.")
    return "".join(normalized).encode("utf-8")


def update_path_digest(digest: Any, app_root: pathlib.Path, path: pathlib.Path) -> None:
    relative = path.relative_to(app_root).as_posix().encode("utf-8")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ManifestError(f"deployment input을 검사하지 못했습니다: {path}: {exc}") from exc
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISREG(metadata.st_mode):
        require(metadata.st_nlink == 1, f"deployment input은 hard link일 수 없습니다: {path}")
        digest.update(b"F\0" + relative + b"\0" + f"{mode:o}".encode() + b"\0")
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        except OSError as exc:
            raise ManifestError(f"deployment input을 읽지 못했습니다: {path}: {exc}") from exc
        digest.update(b"\0")
        return
    if stat.S_ISLNK(metadata.st_mode):
        try:
            target = os.readlink(path)
        except OSError as exc:
            raise ManifestError(f"deployment symlink를 읽지 못했습니다: {path}: {exc}") from exc
        digest.update(b"L\0" + relative + b"\0" + target.encode("utf-8") + b"\0")
        return
    raise ManifestError(f"deployment input에는 일반 파일과 symlink만 허용합니다: {path}")


def source_digest(app_root: pathlib.Path, env_file: pathlib.Path) -> str:
    app_root = app_root.resolve()
    try:
        env_metadata = env_file.lstat()
    except OSError as exc:
        raise ManifestError(f"canonical env를 검사하지 못했습니다: {exc}") from exc
    require(stat.S_ISREG(env_metadata.st_mode), "canonical env는 symlink가 아닌 일반 파일이어야 합니다.")
    require(env_metadata.st_nlink == 1, "canonical env는 hard link일 수 없습니다.")
    digest = hashlib.sha256()
    digest.update(b"ctfnight-deployment-input-v1\0")
    for path in deployment_input_paths(app_root):
        update_path_digest(digest, app_root, path)
    digest.update(b"ENV\0" + env_file.name.encode("utf-8") + b"\0")
    digest.update(deployment_env_bytes(env_file))
    digest.update(b"\0")
    return digest.hexdigest()


def compose_base(app_root: pathlib.Path, env_file: pathlib.Path) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-directory",
        str(app_root),
        "--env-file",
        str(env_file),
        "-f",
        str(app_root / "compose.yaml"),
    ]


def run_checked(command: list[str], label: str) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManifestError(f"{label}에 실패했습니다: {exc}") from exc
    return result.stdout


def render_compose(app_root: pathlib.Path, env_file: pathlib.Path) -> dict[str, Any]:
    output = run_checked(compose_base(app_root, env_file) + ["config", "--format", "json"], "Compose 재렌더링")
    try:
        value = json.loads(output)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Compose 재렌더 결과가 JSON이 아닙니다: {exc}") from exc
    require(isinstance(value, dict), "Compose 재렌더 최상위 값은 object여야 합니다.")
    return value


def compose_digest(config: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(config))


def expected_references(config: dict[str, Any]) -> dict[str, str]:
    project = config.get("name")
    require(isinstance(project, str) and PROJECT_RE.fullmatch(project) is not None, "Compose project 이름이 올바르지 않습니다.")
    services = config.get("services")
    require(
        isinstance(services, dict) and set(services) == set(COMPOSE_SERVICES),
        "Compose service 집합이 변경되었습니다.",
    )
    for name in COMPOSE_SERVICES:
        require(isinstance(services[name], dict), f"Compose {name} service가 object가 아닙니다.")
    references = {name: f"{project}-{name}" for name in LOCAL_IMAGE_SERVICES}
    for name in ("postgres", "redis"):
        image = services[name].get("image")
        require(isinstance(image, str) and "@sha256:" in image, f"{name} image가 digest로 고정되지 않았습니다.")
        references[name] = image
    require(services["migrate"].get("image") == references["backend"], "migrate가 canonical backend image를 재사용하지 않습니다.")
    require(services["db-roles"].get("image") == references["postgres"], "db-roles가 canonical PostgreSQL image를 재사용하지 않습니다.")
    return references


def inspect_image(reference: str) -> tuple[str, str]:
    image_id = run_checked(
        ["docker", "image", "inspect", "--format", "{{.Id}}", reference],
        f"image ID 검사({reference})",
    ).strip()
    require(IMAGE_ID_RE.fullmatch(image_id) is not None, f"image ID 형식이 올바르지 않습니다: {reference}")
    platform = run_checked(
        ["docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image_id],
        f"image platform 검사({reference})",
    ).strip()
    require(platform == "linux/amd64", f"image platform이 linux/amd64가 아닙니다: {reference} ({platform})")
    return image_id, platform


def build_payload(
    *,
    app_root: pathlib.Path,
    env_file: pathlib.Path,
    rendered_config: pathlib.Path,
    expected_source_sha256: str,
    images: dict[str, tuple[str, str]],
    trivy_version: str,
) -> dict[str, Any]:
    require(SHA256_RE.fullmatch(expected_source_sha256) is not None, "build 전 source digest 형식이 올바르지 않습니다.")
    current_source = source_digest(app_root, env_file)
    require(current_source == expected_source_sha256, "image build/scan 중 deployment source 또는 env가 변경되었습니다.")
    config = read_json_file(rendered_config, "rendered Compose config")
    references = expected_references(config)
    require(set(images) == set(IMAGE_SERVICES), "manifest image service 집합이 완전하지 않습니다.")
    normalized_images: dict[str, dict[str, str]] = {}
    for name in IMAGE_SERVICES:
        reference, image_id = images[name]
        require(reference == references[name], f"{name} canonical image reference가 다릅니다.")
        require(IMAGE_ID_RE.fullmatch(image_id) is not None, f"{name} image ID 형식이 올바르지 않습니다.")
        normalized_images[name] = {"id": image_id, "reference": reference}
    return {
        "compose_config_sha256": compose_digest(config),
        "created_utc": datetime.now(UTC).isoformat(),
        "env_file": env_file.name,
        "images": normalized_images,
        "platform": "linux/amd64",
        "project": config["name"],
        "schema": SCHEMA,
        "source_sha256": current_source,
        "trivy_version": trivy_version,
        "vulnerability_severities": ["UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"],
    }


def envelope_for(payload: dict[str, Any]) -> dict[str, Any]:
    return {"payload": payload, "payload_sha256": sha256_bytes(canonical_json(payload))}


def atomic_write_manifest(path: pathlib.Path, payload: dict[str, Any]) -> None:
    ensure_private_directory(path.parent)
    envelope = envelope_for(payload)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".deployment-manifest.", dir=path.parent)
    temporary = pathlib.Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(canonical_json(envelope) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def invalidate_manifest(path: pathlib.Path) -> None:
    """Durably revoke an earlier deployment approval before a new gate runs."""

    ensure_private_directory(path.parent)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ManifestError(f"기존 deployment manifest를 검사하지 못했습니다: {exc}") from exc
    require(
        stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode),
        "기존 deployment manifest 경로는 일반 파일 또는 symlink여야 합니다.",
    )
    try:
        path.unlink()
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise ManifestError(f"기존 deployment manifest를 폐기하지 못했습니다: {exc}") from exc


def validate_payload(payload: Any) -> dict[str, Any]:
    require(isinstance(payload, dict), "manifest payload는 object여야 합니다.")
    expected_keys = {
        "compose_config_sha256",
        "created_utc",
        "env_file",
        "images",
        "platform",
        "project",
        "schema",
        "source_sha256",
        "trivy_version",
        "vulnerability_severities",
    }
    require(set(payload) == expected_keys, "manifest payload field 집합이 변경되었습니다.")
    require(payload.get("schema") == SCHEMA, "manifest schema가 다릅니다.")
    require(payload.get("platform") == "linux/amd64", "manifest platform이 다릅니다.")
    require(SHA256_RE.fullmatch(str(payload.get("source_sha256", ""))) is not None, "manifest source digest가 올바르지 않습니다.")
    require(
        SHA256_RE.fullmatch(str(payload.get("compose_config_sha256", ""))) is not None,
        "manifest Compose digest가 올바르지 않습니다.",
    )
    require(PROJECT_RE.fullmatch(str(payload.get("project", ""))) is not None, "manifest project 이름이 올바르지 않습니다.")
    require(payload.get("env_file") in {".env", ".env.example"}, "manifest env basename이 허용값이 아닙니다.")
    try:
        created_utc = datetime.fromisoformat(str(payload.get("created_utc")))
    except ValueError as exc:
        raise ManifestError("manifest 생성 시각이 ISO-8601이 아닙니다.") from exc
    require(created_utc.tzinfo is not None and created_utc.utcoffset() == timedelta(0), "manifest 생성 시각은 UTC여야 합니다.")
    require(isinstance(payload.get("trivy_version"), str) and payload["trivy_version"], "manifest Trivy version이 없습니다.")
    require(
        payload.get("vulnerability_severities") == ["UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"],
        "manifest severity gate가 all-severity 계약과 다릅니다.",
    )
    images = payload.get("images")
    require(
        isinstance(images, dict) and set(images) == set(IMAGE_SERVICES),
        "manifest image 집합이 완전하지 않습니다.",
    )
    for name in IMAGE_SERVICES:
        image = images[name]
        require(isinstance(image, dict) and set(image) == {"id", "reference"}, f"manifest {name} image field가 올바르지 않습니다.")
        require(isinstance(image["reference"], str) and image["reference"], f"manifest {name} reference가 없습니다.")
        require(IMAGE_ID_RE.fullmatch(str(image["id"])) is not None, f"manifest {name} image ID가 올바르지 않습니다.")
    return payload


def validate_freshness(payload: dict[str, Any]) -> None:
    created_utc = datetime.fromisoformat(payload["created_utc"])
    age = datetime.now(UTC) - created_utc
    require(age >= -MAX_CLOCK_SKEW, "deployment manifest 생성 시각이 현재보다 지나치게 미래입니다.")
    require(age <= MAX_MANIFEST_AGE, "deployment manifest가 2시간보다 오래되어 다시 검사해야 합니다.")


def load_manifest(path: pathlib.Path) -> dict[str, Any]:
    ensure_private_directory(path.parent)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ManifestError(f"deployment manifest를 검사하지 못했습니다: {exc}") from exc
    require(stat.S_ISREG(metadata.st_mode), "deployment manifest는 symlink가 아닌 일반 파일이어야 합니다.")
    require(metadata.st_uid == os.getuid(), "deployment manifest는 현재 운영 계정 소유여야 합니다.")
    require(metadata.st_nlink == 1, "deployment manifest는 hard link일 수 없습니다.")
    require(stat.S_IMODE(metadata.st_mode) == 0o600, "deployment manifest mode는 정확히 0600이어야 합니다.")
    require(metadata.st_size <= 64 * 1024, "deployment manifest가 비정상적으로 큽니다.")
    envelope = read_json_file(path, "deployment manifest")
    require(set(envelope) == {"payload", "payload_sha256"}, "manifest envelope field가 변경되었습니다.")
    payload = validate_payload(envelope.get("payload"))
    expected_hash = sha256_bytes(canonical_json(payload))
    require(envelope.get("payload_sha256") == expected_hash, "deployment manifest 자체 무결성 검증에 실패했습니다.")
    return payload


def verify_common(path: pathlib.Path, app_root: pathlib.Path, env_file: pathlib.Path) -> dict[str, Any]:
    payload = load_manifest(path)
    validate_freshness(payload)
    require(payload["env_file"] == env_file.name, "manifest와 현재 canonical env 파일이 다릅니다.")
    require(source_digest(app_root, env_file) == payload["source_sha256"], "manifest 생성 뒤 deployment source 또는 env가 변경되었습니다.")
    config = render_compose(app_root, env_file)
    require(compose_digest(config) == payload["compose_config_sha256"], "manifest 생성 뒤 rendered Compose graph가 변경되었습니다.")
    references = expected_references(config)
    require(config.get("name") == payload["project"], "manifest와 현재 Compose project가 다릅니다.")
    for name in IMAGE_SERVICES:
        expected = payload["images"][name]
        require(expected["reference"] == references[name], f"{name} manifest reference가 현재 graph와 다릅니다.")
        current_id, _platform = inspect_image(expected["reference"])
        require(current_id == expected["id"], f"{name} canonical tag가 검사한 image ID와 다릅니다.")
    return payload


def exact_service_container_map(entries: list[tuple[str, str]]) -> dict[str, str]:
    require(
        len(entries) == len(COMPOSE_SERVICES),
        "Compose project container 수가 canonical 7개 service와 다릅니다.",
    )
    containers: dict[str, str] = {}
    for container_id, service_name in entries:
        require(service_name in COMPOSE_SERVICES, f"허용되지 않은 orphan service container가 있습니다: {service_name}")
        require(service_name not in containers, f"{service_name} container가 중복되었습니다.")
        containers[service_name] = container_id
    require(set(containers) == set(COMPOSE_SERVICES), "Compose project service container 집합이 변경되었습니다.")
    return containers


def verify_running(path: pathlib.Path, app_root: pathlib.Path, env_file: pathlib.Path) -> None:
    payload = verify_common(path, app_root, env_file)
    base = compose_base(app_root, env_file)
    project_container_ids = [
        line
        for line in run_checked(
            base + ["ps", "--all", "--orphans", "--quiet"],
            "Compose project 전체 container 조회",
        ).splitlines()
        if line
    ]
    entries = [
        (
            container_id,
            run_checked(
                [
                    "docker",
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "com.docker.compose.service"}}',
                    container_id,
                ],
                f"{container_id} Compose service label 검사",
            ).strip(),
        )
        for container_id in project_container_ids
    ]
    containers = exact_service_container_map(entries)
    for name in COMPOSE_SERVICES:
        container_id = containers[name]
        status = run_checked(
            ["docker", "inspect", "--format", "{{.State.Status}}", container_id],
            f"{name} container 상태 검사",
        ).strip()
        if name in PERSISTENT_SERVICES:
            require(status == "running", f"{name} container가 running 상태가 아닙니다.")
        else:
            require(name in ONE_SHOT_SERVICES, f"{name} service 상태 계약이 없습니다.")
            exit_code = run_checked(
                ["docker", "inspect", "--format", "{{.State.ExitCode}}", container_id],
                f"{name} one-shot exit code 검사",
            ).strip()
            require(status == "exited" and exit_code == "0", f"{name} one-shot container가 성공 완료 상태가 아닙니다.")
        running_image = run_checked(
            ["docker", "inspect", "--format", "{{.Image}}", container_id],
            f"{name} 실행 image ID 검사",
        ).strip()
        image_name = SERVICE_IMAGE[name]
        require(
            running_image == payload["images"][image_name]["id"],
            f"{name} container가 검사한 {image_name} image ID를 사용하지 않습니다.",
        )


def run_self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="ctfnight-manifest-test.") as temporary_name:
        root = pathlib.Path(temporary_name)
        os.chmod(root, 0o700)
        for directory in ("backend", "frontend", "deploy", "scripts", "security-reports"):
            (root / directory).mkdir(mode=0o700)
        (root / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
        (root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        (root / "backend/Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (root / "frontend/Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (root / "deploy/Caddyfile").write_text("{}\n", encoding="utf-8")
        for relative in POLICY_FILES:
            path = root / relative
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_text(relative + "\n", encoding="utf-8")
        env_file = root / ".env.example"
        env_file.write_text(
            "ALPHA_COMPOSE_PROJECT_NAME=alpha\nALPHA_ADMIN_BOOTSTRAPPED=true\n",
            encoding="utf-8",
        )
        first_digest = source_digest(root, env_file)
        env_file.write_text(
            "ALPHA_COMPOSE_PROJECT_NAME=alpha\nALPHA_ADMIN_BOOTSTRAPPED=false\n",
            encoding="utf-8",
        )
        require(
            first_digest == source_digest(root, env_file),
            "non-Compose admin recovery marker가 source binding을 변경했습니다.",
        )
        recovery_script = root / "scripts/recover-admin-password.sh"
        recovery_original = recovery_script.read_text(encoding="utf-8")
        recovery_script.write_text(recovery_original + "# drift\n", encoding="utf-8")
        require(
            first_digest != source_digest(root, env_file),
            "admin recovery policy drift self-test가 변경을 감지하지 못했습니다.",
        )
        recovery_script.write_text(recovery_original, encoding="utf-8")
        (root / "backend/Dockerfile").write_text("FROM scratch\n# drift\n", encoding="utf-8")
        require(first_digest != source_digest(root, env_file), "source drift self-test가 변경을 감지하지 못했습니다.")

        canonical_containers = [(f"container-{index}", name) for index, name in enumerate(COMPOSE_SERVICES)]
        require(
            set(exact_service_container_map(canonical_containers)) == set(COMPOSE_SERVICES),
            "canonical project container self-test가 실패했습니다.",
        )
        for bad_entries in (
            canonical_containers + [("orphan", "removed-service")],
            canonical_containers[:-1] + [("duplicate", COMPOSE_SERVICES[0])],
            canonical_containers[:-1] + [("orphan", "removed-service")],
        ):
            try:
                exact_service_container_map(bad_entries)
            except ManifestError:
                pass
            else:
                raise ManifestError("orphan/duplicate project container self-test가 실패했습니다.")

        postgres_reference = "registry.example/postgres@sha256:" + "a" * 64
        redis_reference = "registry.example/redis@sha256:" + "b" * 64
        graph = {
            "name": "alpha",
            "services": {
                "backend": {},
                "caddy": {},
                "db-roles": {"image": postgres_reference},
                "frontend": {},
                "migrate": {"image": "alpha-backend"},
                "postgres": {"image": postgres_reference},
                "redis": {"image": redis_reference},
            },
        }
        references = expected_references(graph)
        require(references["backend"] == "alpha-backend", "local image reference self-test가 실패했습니다.")
        graph["services"]["migrate"]["image"] = "other-backend"
        try:
            expected_references(graph)
        except ManifestError:
            pass
        else:
            raise ManifestError("one-shot image alias self-test가 drift를 감지하지 못했습니다.")

        payload = {
            "compose_config_sha256": "1" * 64,
            "created_utc": datetime.now(UTC).isoformat(),
            "env_file": ".env.example",
            "images": {
                name: {"id": "sha256:" + str(index + 1) * 64, "reference": f"alpha-{name}"}
                for index, name in enumerate(IMAGE_SERVICES)
            },
            "platform": "linux/amd64",
            "project": "alpha",
            "schema": SCHEMA,
            "source_sha256": "2" * 64,
            "trivy_version": "test",
            "vulnerability_severities": ["UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"],
        }
        manifest_path = root / "security-reports/deployment-manifest.json"
        atomic_write_manifest(manifest_path, payload)
        load_manifest(manifest_path)
        envelope = read_json_file(manifest_path, "self-test manifest")
        envelope["payload"]["project"] = "tampered"
        manifest_path.write_bytes(canonical_json(envelope))
        os.chmod(manifest_path, 0o600)
        try:
            load_manifest(manifest_path)
        except ManifestError:
            pass
        else:
            raise ManifestError("tampered manifest self-test가 실패했습니다.")
        atomic_write_manifest(manifest_path, payload)
        os.chmod(manifest_path, 0o644)
        try:
            load_manifest(manifest_path)
        except ManifestError:
            pass
        else:
            raise ManifestError("broad manifest mode self-test가 실패했습니다.")
        atomic_write_manifest(manifest_path, payload)
        symlink_path = root / "security-reports/symlink-manifest.json"
        symlink_path.symlink_to(manifest_path.name)
        try:
            load_manifest(symlink_path)
        except ManifestError:
            pass
        else:
            raise ManifestError("symlink manifest self-test가 실패했습니다.")
        stale_payload = dict(payload)
        stale_payload["created_utc"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        try:
            validate_freshness(stale_payload)
        except ManifestError:
            pass
        else:
            raise ManifestError("stale manifest self-test가 실패했습니다.")
        invalidate_manifest(manifest_path)
        require(not manifest_path.exists(), "manifest invalidation self-test가 파일을 폐기하지 못했습니다.")
        protected = root / "protected"
        protected.write_text("keep\n", encoding="utf-8")
        manifest_path.symlink_to(protected)
        invalidate_manifest(manifest_path)
        require(
            not manifest_path.exists() and protected.read_text(encoding="utf-8") == "keep\n",
            "symlink manifest invalidation self-test가 link target을 보존하지 못했습니다.",
        )


def parse_images(raw_images: list[list[str]]) -> dict[str, tuple[str, str]]:
    images: dict[str, tuple[str, str]] = {}
    for service, reference, image_id in raw_images:
        require(service not in images, f"manifest image가 중복되었습니다: {service}")
        images[service] = (reference, image_id)
    return images


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    subparsers = root.add_subparsers(dest="command", required=True)
    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--app-root", required=True, type=pathlib.Path)
    snapshot.add_argument("--env-file", required=True, type=pathlib.Path)

    create = subparsers.add_parser("create")
    create.add_argument("--manifest", required=True, type=pathlib.Path)
    create.add_argument("--app-root", required=True, type=pathlib.Path)
    create.add_argument("--env-file", required=True, type=pathlib.Path)
    create.add_argument("--rendered-config", required=True, type=pathlib.Path)
    create.add_argument("--expected-source-sha256", required=True)
    create.add_argument("--trivy-version", required=True)
    create.add_argument("--image", action="append", nargs=3, metavar=("SERVICE", "REFERENCE", "ID"), default=[])

    invalidate = subparsers.add_parser("invalidate")
    invalidate.add_argument("--manifest", required=True, type=pathlib.Path)
    invalidate.add_argument("--app-root", required=True, type=pathlib.Path)

    for command in ("verify-prestart", "verify-running"):
        verify = subparsers.add_parser(command)
        verify.add_argument("--manifest", required=True, type=pathlib.Path)
        verify.add_argument("--app-root", required=True, type=pathlib.Path)
        verify.add_argument("--env-file", required=True, type=pathlib.Path)
    return root


def main(argv: list[str]) -> int:
    try:
        if argv == ["--self-test"]:
            run_self_test()
            print("Deployment manifest self-test 통과.")
            return 0
        arguments = parser().parse_args(argv)
        app_root = arguments.app_root.resolve()
        if arguments.command == "invalidate":
            manifest = absolute_without_following_leaf(arguments.manifest)
            require(
                manifest == app_root / "security-reports/deployment-manifest.json",
                "deployment manifest 경로는 canonical security-reports 경로여야 합니다.",
            )
            invalidate_manifest(manifest)
            print(f"기존 deployment manifest 승인을 폐기했습니다: {manifest}")
            return 0
        env_file = absolute_without_following_leaf(arguments.env_file)
        require(
            env_file.parent == app_root and env_file.name in {".env", ".env.example"},
            "canonical env는 app root의 .env 또는 .env.example이어야 합니다.",
        )
        if arguments.command == "snapshot":
            print(source_digest(app_root, env_file))
            return 0
        manifest = absolute_without_following_leaf(arguments.manifest)
        require(
            manifest == app_root / "security-reports/deployment-manifest.json",
            "deployment manifest 경로는 canonical security-reports 경로여야 합니다.",
        )
        if arguments.command == "create":
            payload = build_payload(
                app_root=app_root,
                env_file=env_file,
                rendered_config=absolute_without_following_leaf(arguments.rendered_config),
                expected_source_sha256=arguments.expected_source_sha256,
                images=parse_images(arguments.image),
                trivy_version=arguments.trivy_version,
            )
            atomic_write_manifest(manifest, payload)
            print(f"원자적 deployment manifest를 게시했습니다: {manifest}")
            return 0
        if arguments.command == "verify-prestart":
            verify_common(manifest, app_root, env_file)
            print("Deployment manifest/source/canonical image ID 검증 통과.")
            return 0
        verify_running(manifest, app_root, env_file)
        print("5개 상시 service와 2개 one-shot container의 image ID/state가 deployment manifest와 일치합니다.")
        return 0
    except ManifestError as exc:
        print(f"deployment manifest 오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
