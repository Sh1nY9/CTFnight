#!/usr/bin/env python3
"""Fail-closed validation for CTFnight's rendered Compose trust boundaries."""

from __future__ import annotations

import copy
import json
import pathlib
import stat
import sys
from collections.abc import Mapping
from typing import Any


class ComposeSecurityError(ValueError):
    pass


EXPECTED_SERVICES = {
    "backend",
    "caddy",
    "db-roles",
    "frontend",
    "migrate",
    "postgres",
    "redis",
}
EXPECTED_NETWORKS = {"public", "api", "web", "database", "cache"}
EXPECTED_VOLUMES = {"postgres_data", "redis_data", "caddy_data"}
EXPECTED_SECRETS = {
    "alpha_secret_key",
    "postgres_owner_password",
    "postgres_migrator_password",
    "postgres_runtime_password",
    "redis_password",
    "admin_password",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ComposeSecurityError(message)


def require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    require(isinstance(value, Mapping), f"{path}는 object여야 합니다.")
    return value


def require_list(value: Any, path: str) -> list[Any]:
    require(isinstance(value, list), f"{path}는 list여야 합니다.")
    return value


def read_required_env(path: pathlib.Path) -> dict[str, str]:
    required = {
        "ALPHA_BIND_ADDRESS",
        "ALPHA_COMPOSE_PROJECT_NAME",
        "ALPHA_HTTP_PORT",
        "ALPHA_HTTPS_PORT",
        "ALPHA_POSTGRES_DB",
        "ALPHA_POSTGRES_OWNER_USER",
        "ALPHA_POSTGRES_MIGRATOR_USER",
        "ALPHA_POSTGRES_RUNTIME_USER",
    }
    values: dict[str, str] = {}
    counts = {name: 0 for name in required}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ComposeSecurityError(f"Compose port 기준 env를 읽지 못했습니다: {exc}") from exc
    for line in lines:
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in required:
            counts[key] += 1
            values[key] = value
    for key in sorted(required):
        require(counts[key] == 1, f"{path}에 {key}가 정확히 한 번 있어야 합니다.")
    project_name = values["ALPHA_COMPOSE_PROJECT_NAME"]
    require(
        bool(project_name)
        and len(project_name) <= 63
        and project_name == project_name.lower()
        and project_name.strip("abcdefghijklmnopqrstuvwxyz0123456789-") == ""
        and not project_name.startswith("-")
        and not project_name.endswith("-")
        and "--" not in project_name,
        "Compose project 이름이 canonical 형식이 아닙니다.",
    )
    require(values["ALPHA_BIND_ADDRESS"] in {"127.0.0.1", "0.0.0.0"}, "host bind 주소가 허용값이 아닙니다.")
    for key in ("ALPHA_HTTP_PORT", "ALPHA_HTTPS_PORT"):
        try:
            port = int(values[key])
        except ValueError as exc:
            raise ComposeSecurityError(f"{key}가 정수 port가 아닙니다.") from exc
        require(1 <= port <= 65535 and str(port) == values[key], f"{key}가 canonical port가 아닙니다.")
    require(values["ALPHA_HTTP_PORT"] != values["ALPHA_HTTPS_PORT"], "HTTP/HTTPS host port는 서로 달라야 합니다.")
    identifiers = (
        values["ALPHA_POSTGRES_DB"],
        values["ALPHA_POSTGRES_OWNER_USER"],
        values["ALPHA_POSTGRES_MIGRATOR_USER"],
        values["ALPHA_POSTGRES_RUNTIME_USER"],
    )
    require(
        all(value and len(value) <= 63 and value.strip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_") == "" for value in identifiers),
        "PostgreSQL identifier가 canonical 형식이 아닙니다.",
    )
    require(values["ALPHA_POSTGRES_MIGRATOR_USER"] == "alpha_migrator", "migrator role 이름이 변경되었습니다.")
    require(values["ALPHA_POSTGRES_RUNTIME_USER"] == "alpha_app", "runtime role 이름이 변경되었습니다.")
    require(len(set(identifiers[1:])) == 3, "PostgreSQL role 이름은 서로 달라야 합니다.")
    return values


def validate_builds(services: Mapping[str, Any], app_root: pathlib.Path) -> None:
    contexts = {
        "backend": app_root / "backend",
        "caddy": app_root / "deploy/caddy",
        "frontend": app_root / "frontend",
    }
    for name, context in contexts.items():
        service = require_mapping(services[name], f"services.{name}")
        build = require_mapping(service.get("build"), f"services.{name}.build")
        require(service.get("image") is None, f"{name}은 canonical local build여야 합니다.")
        # No target is intentional: Compose and the scanner both build the
        # Dockerfile's final stage, so adding a new final stage cannot escape scan.
        require(
            set(build) == {"context", "dockerfile"},
            f"{name} build에는 context/dockerfile 외 override를 허용하지 않습니다.",
        )
        require(
            build.get("context") == str(context.resolve()) and build.get("dockerfile") == "Dockerfile",
            f"{name} build context가 canonical 경로와 다릅니다.",
        )
    for name in ("postgres", "redis", "db-roles"):
        service = require_mapping(services[name], f"services.{name}")
        require(service.get("build") is None, f"{name}은 local build를 사용할 수 없습니다.")
        require(isinstance(service.get("image"), str) and service["image"], f"{name} image ref가 없습니다.")
    migrate = require_mapping(services["migrate"], "services.migrate")
    require(migrate.get("build") is None, "migrate는 별도 local build를 사용할 수 없습니다.")
    require(isinstance(migrate.get("image"), str) and migrate["image"], "migrate image ref가 없습니다.")
    require(services["db-roles"]["image"] == services["postgres"]["image"], "db-roles는 검사한 PostgreSQL image를 재사용해야 합니다.")


def validate_process_boundaries(services: Mapping[str, Any]) -> None:
    expected_users = {name: "65532:65532" for name in EXPECTED_SERVICES}
    # The pinned Chainguard PostgreSQL entrypoint must start as root to prepare
    # its named volume and then drops to the built-in postgres UID/GID 70.
    expected_users["postgres"] = "0:0"
    expected_read_only = {
        "backend": True,
        "caddy": True,
        "db-roles": True,
        "frontend": True,
        "migrate": True,
        "postgres": True,
        "redis": True,
    }
    expected_cap_add = {
        "backend": set(),
        "caddy": {"NET_BIND_SERVICE"},
        "db-roles": set(),
        "frontend": set(),
        "migrate": set(),
        "postgres": {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"},
        "redis": set(),
    }
    expected_tmpfs = {
        "backend": ["/tmp:size=64m,mode=1777"],
        "caddy": ["/tmp:size=16m,mode=1777"],
        "db-roles": ["/tmp:size=4m,mode=0700,uid=65532,gid=65532"],
        "frontend": [
            "/tmp:size=16m,mode=1777,uid=65532,gid=65532",
            "/run:size=1m,mode=0755,uid=65532,gid=65532",
            "/var/lib/nginx/tmp:size=16m,mode=0700,uid=65532,gid=65532",
        ],
        "migrate": ["/tmp:size=64m,mode=1777"],
        "postgres": [
            "/tmp:size=16m,mode=1777,uid=70,gid=70",
            "/var/run/postgresql:size=4m,mode=0700,uid=70,gid=70",
        ],
        "redis": ["/tmp:size=8m,mode=0700,uid=65532,gid=65532"],
    }
    forbidden_nonempty = {
        "configs",
        "credential_spec",
        "cgroup",
        "cgroup_parent",
        "device_cgroup_rules",
        "devices",
        "deploy",
        "develop",
        "dns",
        "dns_opt",
        "dns_search",
        "external_links",
        "extra_hosts",
        "gpus",
        "group_add",
        "ipc",
        "links",
        "models",
        "network_mode",
        "pid",
        "post_start",
        "pre_start",
        "pull_policy",
        "runtime",
        "storage_opt",
        "sysctls",
        "use_api_socket",
        "userns_mode",
        "uts",
        "volumes_from",
    }
    for name in sorted(EXPECTED_SERVICES):
        service = require_mapping(services[name], f"services.{name}")
        require(service.get("platform") == "linux/amd64", f"{name} platform은 linux/amd64여야 합니다.")
        require(service.get("user") == expected_users[name], f"{name} user가 canonical 값과 다릅니다.")
        require(service.get("privileged") in (None, False), f"{name} privileged 실행은 금지됩니다.")
        actual_read_only = service.get("read_only", False)
        require(
            actual_read_only is expected_read_only[name],
            f"{name} read_only가 canonical 기대값과 다릅니다.",
        )
        cap_drop = require_list(service.get("cap_drop", []), f"services.{name}.cap_drop")
        require(cap_drop == ["ALL"], f"{name}은 모든 capability를 먼저 drop해야 합니다.")
        cap_add = require_list(service.get("cap_add", []), f"services.{name}.cap_add")
        require(
            len(cap_add) == len(set(cap_add)) and set(cap_add) == expected_cap_add[name],
            f"{name} cap_add가 exact allowlist와 다릅니다.",
        )
        security_opt = require_list(service.get("security_opt", []), f"services.{name}.security_opt")
        require(
            security_opt == ["no-new-privileges:true"],
            f"{name} security_opt는 no-new-privileges만 허용합니다.",
        )
        tmpfs = require_list(service.get("tmpfs", []), f"services.{name}.tmpfs")
        require(tmpfs == expected_tmpfs[name], f"{name} tmpfs size/mode가 exact allowlist와 다릅니다.")
        for field in forbidden_nonempty:
            require(service.get(field) in (None, False, [], {}), f"{name}.{field}는 허용되지 않습니다.")


def normalize_port(item: Any, path: str) -> tuple[str, str, int, str, str]:
    port = require_mapping(item, path)
    require(
        set(port) <= {"host_ip", "mode", "protocol", "published", "target"},
        f"{path}에 허용되지 않은 port option이 있습니다.",
    )
    try:
        target = int(port.get("target"))
    except (TypeError, ValueError) as exc:
        raise ComposeSecurityError(f"{path}.target이 정수 port가 아닙니다.") from exc
    published = str(port.get("published", ""))
    require(published.isdecimal() and str(int(published)) == published, f"{path}.published가 단일 port가 아닙니다.")
    return (
        str(port.get("host_ip", "")),
        published,
        target,
        str(port.get("protocol", "tcp")),
        str(port.get("mode", "ingress")),
    )


def validate_ports(services: Mapping[str, Any], env: Mapping[str, str]) -> None:
    for name in EXPECTED_SERVICES - {"caddy"}:
        require(services[name].get("ports") in (None, []), f"public port는 caddy만 publish할 수 있습니다: {name}")
    ports = require_list(services["caddy"].get("ports"), "services.caddy.ports")
    actual = [normalize_port(item, f"services.caddy.ports[{index}]") for index, item in enumerate(ports)]
    bind = env["ALPHA_BIND_ADDRESS"]
    expected = {
        (bind, env["ALPHA_HTTP_PORT"], 80, "tcp", "ingress"),
        (bind, env["ALPHA_HTTPS_PORT"], 443, "tcp", "ingress"),
        (bind, env["ALPHA_HTTPS_PORT"], 443, "udp", "ingress"),
    }
    require(len(actual) == len(expected) and set(actual) == expected, "caddy host bind/target/protocol이 exact allowlist와 다릅니다.")


def validate_top_level_networks(config: Mapping[str, Any], project: str) -> None:
    networks = require_mapping(config.get("networks"), "networks")
    require(set(networks) == EXPECTED_NETWORKS, "canonical Compose network 집합이 변경되었습니다.")
    for name in sorted(EXPECTED_NETWORKS):
        network = require_mapping(networks[name], f"networks.{name}")
        require(
            set(network) <= {"driver", "external", "internal", "ipam", "name"},
            f"network {name}에 허용되지 않은 option이 있습니다.",
        )
        require(network.get("name") == f"{project}_{name}", f"network {name}의 daemon name이 canonical 값과 다릅니다.")
        internal = network.get("internal", False)
        require(internal is (name != "public"), f"network {name}의 internal 경계가 변경되었습니다.")
        require(network.get("external") in (None, False), f"network {name}은 external일 수 없습니다.")
        require(network.get("driver") in (None, "bridge"), f"network {name}은 bridge driver만 허용합니다.")
        ipam = network.get("ipam")
        if name == "api":
            ipam_map = require_mapping(ipam, "networks.api.ipam")
            require(set(ipam_map) <= {"config", "driver"}, "api IPAM에 허용되지 않은 option이 있습니다.")
            require(ipam_map.get("driver") in (None, "default"), "api IPAM driver가 변경되었습니다.")
            entries = require_list(ipam_map.get("config"), "networks.api.ipam.config")
            require(entries == [{"subnet": "172.31.250.0/24"}], "api subnet은 172.31.250.0/24여야 합니다.")
        else:
            require(ipam in (None, {}, {"config": []}), f"network {name}에는 custom IPAM을 허용하지 않습니다.")


def validate_service_networks(services: Mapping[str, Any]) -> None:
    expected_membership = {
        "backend": {"api", "database", "cache"},
        "caddy": {"public", "api", "web"},
        "db-roles": {"database"},
        "frontend": {"web"},
        "migrate": {"database"},
        "postgres": {"database"},
        "redis": {"cache"},
    }
    expected_options: dict[tuple[str, str], dict[str, Any]] = {
        ("backend", "api"): {"ipv4_address": "172.31.250.3"},
        ("caddy", "api"): {"ipv4_address": "172.31.250.2"},
        ("caddy", "public"): {"gw_priority": 1},
    }
    for service_name, expected_networks in expected_membership.items():
        service = services[service_name]
        networks = require_mapping(service.get("networks"), f"services.{service_name}.networks")
        require(set(networks) == expected_networks, f"{service_name} network membership가 변경되었습니다.")
        for network_name, raw_options in networks.items():
            options = {} if raw_options is None else dict(require_mapping(raw_options, f"services.{service_name}.networks.{network_name}"))
            require(
                options == expected_options.get((service_name, network_name), {}),
                f"{service_name}/{network_name} attachment option이 exact allowlist와 다릅니다.",
            )


def validate_top_level_volumes(config: Mapping[str, Any], project: str) -> None:
    volumes = require_mapping(config.get("volumes"), "volumes")
    require(set(volumes) == EXPECTED_VOLUMES, "canonical named volume 집합이 변경되었습니다.")
    for name, raw_volume in volumes.items():
        volume = require_mapping(raw_volume, f"volumes.{name}")
        require(set(volume) <= {"name"}, f"volume {name}에는 driver/external option을 허용하지 않습니다.")
        require(volume.get("name") == f"{project}_{name}", f"volume {name}의 daemon name이 canonical 값과 다릅니다.")


def normalize_mount(item: Any, path: str) -> tuple[str, str, str, bool]:
    mount = require_mapping(item, path)
    mount_type = mount.get("type")
    require(mount_type in {"bind", "volume"}, f"{path}.type은 bind 또는 volume이어야 합니다.")
    allowed = {"type", "source", "target", "read_only", mount_type}
    require(set(mount) <= allowed, f"{path}에 허용되지 않은 mount option이 있습니다.")
    source = mount.get("source")
    target = mount.get("target")
    require(isinstance(source, str) and isinstance(target, str), f"{path} source/target이 문자열이 아닙니다.")
    read_only = mount.get("read_only", False)
    require(isinstance(read_only, bool), f"{path}.read_only가 boolean이 아닙니다.")
    options = mount.get(mount_type)
    if mount_type == "bind":
        require(
            options in (None, {}, {"create_host_path": True}),
            f"{path} bind propagation/relabel option은 허용하지 않습니다.",
        )
    else:
        require(options in (None, {}), f"{path} volume subpath/nocopy option은 허용하지 않습니다.")
    return str(mount_type), source, target, read_only


def validate_service_volumes(services: Mapping[str, Any], app_root: pathlib.Path, *, check_paths: bool) -> None:
    caddyfile = app_root / "deploy/Caddyfile"
    role_script = app_root / "deploy/postgres/provision-roles.sh"
    expected = {
        "backend": set(),
        "db-roles": {
            ("bind", str(role_script.resolve()), "/usr/local/bin/provision-roles.sh", True)
        },
        "frontend": set(),
        "migrate": set(),
        "postgres": {("volume", "postgres_data", "/var/lib/postgresql/data", False)},
        "redis": {("volume", "redis_data", "/data", False)},
        "caddy": {
            ("bind", str(caddyfile.resolve()), "/etc/caddy/Caddyfile", True),
            ("volume", "caddy_data", "/data", False),
        },
    }
    if check_paths:
        for path, label in ((caddyfile, "Caddyfile"), (role_script, "DB role script")):
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise ComposeSecurityError(f"canonical {label}을 검사하지 못했습니다: {exc}") from exc
            require(
                stat.S_ISREG(metadata.st_mode) and not path.is_symlink(),
                f"canonical {label}은 symlink가 아닌 일반 파일이어야 합니다.",
            )
    sensitive_sources = {"/var/run/docker.sock", "/run/docker.sock", "/proc", "/sys", "/dev", "/"}
    for name in sorted(EXPECTED_SERVICES):
        mounts = require_list(services[name].get("volumes", []), f"services.{name}.volumes")
        normalized = [normalize_mount(item, f"services.{name}.volumes[{index}]") for index, item in enumerate(mounts)]
        for mount_type, source, _target, _read_only in normalized:
            if mount_type == "bind":
                require(source not in sensitive_sources, f"{name}에 민감 host path/socket mount가 있습니다: {source}")
        require(len(normalized) == len(expected[name]) and set(normalized) == expected[name], f"{name} volume mount가 exact allowlist와 다릅니다.")


def validate_secrets(
    config: Mapping[str, Any], services: Mapping[str, Any], app_root: pathlib.Path, project: str
) -> None:
    secrets = require_mapping(config.get("secrets"), "secrets")
    require(set(secrets) == EXPECTED_SECRETS, "canonical secret source 집합이 변경되었습니다.")
    for name in sorted(EXPECTED_SECRETS):
        secret = require_mapping(secrets[name], f"secrets.{name}")
        require(set(secret) <= {"file", "name"}, f"secret {name}은 canonical file source만 허용합니다.")
        require(secret.get("name") == f"{project}_{name}", f"secret {name}의 daemon name이 canonical 값과 다릅니다.")
        require(secret.get("file") == str((app_root / ".secrets" / name).resolve()), f"secret {name} file source가 변경되었습니다.")

    expected_mounts = {
        "backend": {"alpha_secret_key", "postgres_runtime_password", "redis_password"},
        "caddy": set(),
        "db-roles": {
            "postgres_owner_password",
            "postgres_migrator_password",
            "postgres_runtime_password",
        },
        "frontend": set(),
        "migrate": {"alpha_secret_key", "postgres_migrator_password", "redis_password", "admin_password"},
        "postgres": {"postgres_owner_password"},
        "redis": {"redis_password"},
    }
    for service_name, expected_sources in expected_mounts.items():
        raw_mounts = require_list(services[service_name].get("secrets", []), f"services.{service_name}.secrets")
        actual: set[str] = set()
        for index, raw_mount in enumerate(raw_mounts):
            mount = require_mapping(raw_mount, f"services.{service_name}.secrets[{index}]")
            require(set(mount) == {"source", "target"}, f"{service_name} secret mount에는 source/target만 허용합니다.")
            source = mount.get("source")
            require(isinstance(source, str), f"{service_name} secret source가 문자열이 아닙니다.")
            require(mount.get("target") == f"/run/secrets/{source}", f"{service_name} secret target이 변경되었습니다.")
            actual.add(source)
        require(len(raw_mounts) == len(expected_sources) and actual == expected_sources, f"{service_name} secret grant가 exact allowlist와 다릅니다.")


def validate_no_plaintext_secret_environment(services: Mapping[str, Any]) -> None:
    sensitive_fragments = ("PASSWORD", "SECRET", "TOKEN", "PRIVATE_KEY")
    for service_name, service in services.items():
        environment = require_mapping(service.get("environment", {}), f"services.{service_name}.environment")
        mounted_secrets = {
            mount["source"]
            for mount in require_list(service.get("secrets", []), f"services.{service_name}.secrets")
            if isinstance(mount, Mapping) and isinstance(mount.get("source"), str)
        }
        for key, value in environment.items():
            if any(fragment in key.upper() for fragment in sensitive_fragments):
                require(key.upper().endswith("_FILE"), f"{service_name}.{key}는 file-backed secret이어야 합니다.")
                require(
                    isinstance(value, str)
                    and value.startswith("/run/secrets/")
                    and value.removeprefix("/run/secrets/") in mounted_secrets
                    and value == f"/run/secrets/{value.removeprefix('/run/secrets/')}",
                    f"{service_name}.{key}는 해당 service에 grant된 exact secret path여야 합니다.",
                )


def validate_dependency(
    services: Mapping[str, Any], service_name: str, dependency_name: str, condition: str
) -> None:
    dependencies = require_mapping(
        services[service_name].get("depends_on", {}), f"services.{service_name}.depends_on"
    )
    dependency = require_mapping(
        dependencies.get(dependency_name),
        f"services.{service_name}.depends_on.{dependency_name}",
    )
    require(
        set(dependency) <= {"condition", "required", "restart"},
        f"{service_name}/{dependency_name} dependency option이 변경되었습니다.",
    )
    require(dependency.get("condition") == condition, f"{service_name}/{dependency_name} condition이 다릅니다.")
    require(dependency.get("required", True) is True, f"{service_name}/{dependency_name}는 필수 dependency여야 합니다.")
    require(dependency.get("restart", False) is False, f"{service_name}/{dependency_name} restart 연동은 허용하지 않습니다.")


def validate_database_role_boundary(
    services: Mapping[str, Any], env: Mapping[str, str], project: str
) -> None:
    expected_dependencies = {
        "backend": {"migrate": "service_completed_successfully", "redis": "service_healthy"},
        "caddy": {"backend": "service_healthy", "frontend": "service_healthy"},
        "db-roles": {"postgres": "service_healthy"},
        "frontend": {},
        "migrate": {"db-roles": "service_completed_successfully"},
        "postgres": {},
        "redis": {},
    }
    for service_name, expected in expected_dependencies.items():
        actual = require_mapping(
            services[service_name].get("depends_on", {}), f"services.{service_name}.depends_on"
        )
        require(set(actual) == set(expected), f"{service_name} dependency 집합이 변경되었습니다.")
        for dependency_name, condition in expected.items():
            validate_dependency(services, service_name, dependency_name, condition)

    for service_name in EXPECTED_SERVICES:
        expected_restart = "no" if service_name in {"db-roles", "migrate"} else "unless-stopped"
        require(
            services[service_name].get("restart") == expected_restart,
            f"{service_name} restart policy가 canonical 값과 다릅니다.",
        )

    postgres_environment = require_mapping(services["postgres"].get("environment"), "services.postgres.environment")
    require(postgres_environment.get("POSTGRES_DB") == env["ALPHA_POSTGRES_DB"], "PostgreSQL database wiring이 다릅니다.")
    require(postgres_environment.get("POSTGRES_USER") == env["ALPHA_POSTGRES_OWNER_USER"], "PostgreSQL owner wiring이 다릅니다.")
    require(
        postgres_environment.get("POSTGRES_PASSWORD_FILE") == "/run/secrets/postgres_owner_password",
        "PostgreSQL owner password wiring이 다릅니다.",
    )

    role_environment = require_mapping(services["db-roles"].get("environment"), "services.db-roles.environment")
    expected_role_environment = {
        "ALPHA_POSTGRES_DB": env["ALPHA_POSTGRES_DB"],
        "ALPHA_POSTGRES_OWNER_USER": env["ALPHA_POSTGRES_OWNER_USER"],
        "ALPHA_POSTGRES_MIGRATOR_USER": env["ALPHA_POSTGRES_MIGRATOR_USER"],
        "ALPHA_POSTGRES_RUNTIME_USER": env["ALPHA_POSTGRES_RUNTIME_USER"],
    }
    require(role_environment == expected_role_environment, "db-roles identifier wiring이 exact allowlist와 다릅니다.")
    require(
        services["db-roles"].get("entrypoint") == ["/bin/sh", "/usr/local/bin/provision-roles.sh"],
        "db-roles entrypoint가 canonical script와 다릅니다.",
    )
    require(services["db-roles"].get("command") in (None, []), "db-roles command override는 허용하지 않습니다.")

    migrate_environment = require_mapping(services["migrate"].get("environment"), "services.migrate.environment")
    backend_environment = require_mapping(services["backend"].get("environment"), "services.backend.environment")
    for service_name, service_environment, expected_user, expected_secret in (
        ("migrate", migrate_environment, env["ALPHA_POSTGRES_MIGRATOR_USER"], "postgres_migrator_password"),
        ("backend", backend_environment, env["ALPHA_POSTGRES_RUNTIME_USER"], "postgres_runtime_password"),
    ):
        require(service_environment.get("ALPHA_DATABASE_HOST") == "postgres", f"{service_name} DB host가 다릅니다.")
        require(str(service_environment.get("ALPHA_DATABASE_PORT")) == "5432", f"{service_name} DB port가 다릅니다.")
        require(service_environment.get("ALPHA_DATABASE_NAME") == env["ALPHA_POSTGRES_DB"], f"{service_name} DB name이 다릅니다.")
        require(service_environment.get("ALPHA_DATABASE_USER") == expected_user, f"{service_name} DB role이 다릅니다.")
        require(
            service_environment.get("ALPHA_DATABASE_PASSWORD_FILE") == f"/run/secrets/{expected_secret}",
            f"{service_name} DB secret wiring이 다릅니다.",
        )

    require(services["migrate"].get("image") == f"{project}-backend", "migrate는 검사한 backend image를 재사용해야 합니다.")
    require(services["migrate"].get("entrypoint") == ["/bin/sh", "-ec"], "migrate entrypoint가 다릅니다.")
    migrate_command = require_list(services["migrate"].get("command"), "services.migrate.command")
    require(
        len(migrate_command) == 1
        and isinstance(migrate_command[0], str)
        and migrate_command[0].strip() == "alembic upgrade head\npython -m alpha.cli bootstrap",
        "migrate command가 migration/bootstrap one-shot 계약과 다릅니다.",
    )


def validate_compose(
    config: Mapping[str, Any],
    app_root: pathlib.Path,
    env: Mapping[str, str],
    *,
    check_paths: bool = True,
) -> dict[str, str]:
    services = require_mapping(config.get("services"), "services")
    require(set(services) == EXPECTED_SERVICES, "canonical Compose service 집합이 변경되었습니다.")
    project = env["ALPHA_COMPOSE_PROJECT_NAME"]
    require(
        config.get("name") == project,
        "렌더링된 Compose project 이름이 canonical env와 다릅니다.",
    )
    require(config.get("configs") in (None, {}), "top-level configs mount는 허용하지 않습니다.")
    validate_builds(services, app_root)
    validate_process_boundaries(services)
    validate_ports(services, env)
    validate_top_level_networks(config, project)
    validate_service_networks(services)
    validate_top_level_volumes(config, project)
    validate_service_volumes(services, app_root, check_paths=check_paths)
    validate_secrets(config, services, app_root, project)
    validate_no_plaintext_secret_environment(services)
    validate_database_role_boundary(services, env, project)
    return {name: services[name]["image"] for name in ("postgres", "redis")}


def canonical_fixture(app_root: pathlib.Path) -> tuple[dict[str, Any], dict[str, str]]:
    service_defaults: dict[str, Any] = {
        "platform": "linux/amd64",
        "user": "65532:65532",
        "cap_drop": ["ALL"],
        "restart": "unless-stopped",
        "security_opt": ["no-new-privileges:true"],
    }
    services: dict[str, Any] = {
        "backend": {
            **service_defaults,
            "build": {"context": str(app_root / "backend"), "dockerfile": "Dockerfile"},
            "read_only": True,
            "tmpfs": ["/tmp:size=64m,mode=1777"],
            "networks": {"api": {"ipv4_address": "172.31.250.3"}, "database": None, "cache": None},
            "secrets": [
                {"source": name, "target": f"/run/secrets/{name}"}
                for name in sorted({"alpha_secret_key", "postgres_runtime_password", "redis_password"})
            ],
            "environment": {
                "ALPHA_SECRET_KEY_FILE": "/run/secrets/alpha_secret_key",
                "ALPHA_DATABASE_HOST": "postgres",
                "ALPHA_DATABASE_PORT": "5432",
                "ALPHA_DATABASE_NAME": "alpha",
                "ALPHA_DATABASE_USER": "alpha_app",
                "ALPHA_DATABASE_PASSWORD_FILE": "/run/secrets/postgres_runtime_password",
                "ALPHA_REDIS_PASSWORD_FILE": "/run/secrets/redis_password",
            },
            "depends_on": {
                "migrate": {"condition": "service_completed_successfully", "required": True, "restart": False},
                "redis": {"condition": "service_healthy", "required": True, "restart": False},
            },
        },
        "caddy": {
            **service_defaults,
            "build": {"context": str(app_root / "deploy/caddy"), "dockerfile": "Dockerfile"},
            "read_only": True,
            "tmpfs": ["/tmp:size=16m,mode=1777"],
            "cap_add": ["NET_BIND_SERVICE"],
            "ports": [
                {"host_ip": "127.0.0.1", "published": "80", "target": 80, "protocol": "tcp", "mode": "ingress"},
                {"host_ip": "127.0.0.1", "published": "443", "target": 443, "protocol": "tcp", "mode": "ingress"},
                {"host_ip": "127.0.0.1", "published": "443", "target": 443, "protocol": "udp", "mode": "ingress"},
            ],
            "networks": {"public": {"gw_priority": 1}, "api": {"ipv4_address": "172.31.250.2"}, "web": None},
            "depends_on": {
                "backend": {"condition": "service_healthy", "required": True, "restart": False},
                "frontend": {"condition": "service_healthy", "required": True, "restart": False},
            },
            "volumes": [
                {
                    "type": "bind",
                    "source": str(app_root / "deploy/Caddyfile"),
                    "target": "/etc/caddy/Caddyfile",
                    "read_only": True,
                    "bind": {"create_host_path": True},
                },
                {"type": "volume", "source": "caddy_data", "target": "/data", "volume": {}},
            ],
        },
        "frontend": {
            **service_defaults,
            "build": {"context": str(app_root / "frontend"), "dockerfile": "Dockerfile"},
            "read_only": True,
            "tmpfs": [
                "/tmp:size=16m,mode=1777,uid=65532,gid=65532",
                "/run:size=1m,mode=0755,uid=65532,gid=65532",
                "/var/lib/nginx/tmp:size=16m,mode=0700,uid=65532,gid=65532",
            ],
            "networks": {"web": None},
        },
        "db-roles": {
            **service_defaults,
            "image": "postgres@example",
            "restart": "no",
            "read_only": True,
            "tmpfs": ["/tmp:size=4m,mode=0700,uid=65532,gid=65532"],
            "entrypoint": ["/bin/sh", "/usr/local/bin/provision-roles.sh"],
            "environment": {
                "ALPHA_POSTGRES_DB": "alpha",
                "ALPHA_POSTGRES_OWNER_USER": "alpha",
                "ALPHA_POSTGRES_MIGRATOR_USER": "alpha_migrator",
                "ALPHA_POSTGRES_RUNTIME_USER": "alpha_app",
            },
            "depends_on": {
                "postgres": {"condition": "service_healthy", "required": True, "restart": False}
            },
            "networks": {"database": None},
            "secrets": [
                {"source": name, "target": f"/run/secrets/{name}"}
                for name in (
                    "postgres_owner_password",
                    "postgres_migrator_password",
                    "postgres_runtime_password",
                )
            ],
            "volumes": [
                {
                    "type": "bind",
                    "source": str(app_root / "deploy/postgres/provision-roles.sh"),
                    "target": "/usr/local/bin/provision-roles.sh",
                    "read_only": True,
                    "bind": {"create_host_path": True},
                }
            ],
        },
        "migrate": {
            **service_defaults,
            "image": "alpha-backend",
            "restart": "no",
            "read_only": True,
            "tmpfs": ["/tmp:size=64m,mode=1777"],
            "entrypoint": ["/bin/sh", "-ec"],
            "command": ["alembic upgrade head\npython -m alpha.cli bootstrap\n"],
            "environment": {
                "ALPHA_SECRET_KEY_FILE": "/run/secrets/alpha_secret_key",
                "ALPHA_DATABASE_HOST": "postgres",
                "ALPHA_DATABASE_PORT": "5432",
                "ALPHA_DATABASE_NAME": "alpha",
                "ALPHA_DATABASE_USER": "alpha_migrator",
                "ALPHA_DATABASE_PASSWORD_FILE": "/run/secrets/postgres_migrator_password",
                "ALPHA_REDIS_PASSWORD_FILE": "/run/secrets/redis_password",
                "ALPHA_ADMIN_PASSWORD_FILE": "/run/secrets/admin_password",
            },
            "depends_on": {
                "db-roles": {
                    "condition": "service_completed_successfully",
                    "required": True,
                    "restart": False,
                }
            },
            "networks": {"database": None},
            "secrets": [
                {"source": name, "target": f"/run/secrets/{name}"}
                for name in (
                    "alpha_secret_key",
                    "postgres_migrator_password",
                    "redis_password",
                    "admin_password",
                )
            ],
        },
        "postgres": {
            **service_defaults,
            "user": "0:0",
            "image": "postgres@example",
            "read_only": True,
            "tmpfs": [
                "/tmp:size=16m,mode=1777,uid=70,gid=70",
                "/var/run/postgresql:size=4m,mode=0700,uid=70,gid=70",
            ],
            "cap_add": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"],
            "networks": {"database": None},
            "secrets": [
                {
                    "source": "postgres_owner_password",
                    "target": "/run/secrets/postgres_owner_password",
                }
            ],
            "environment": {
                "POSTGRES_DB": "alpha",
                "POSTGRES_USER": "alpha",
                "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres_owner_password",
            },
            "volumes": [
                {"type": "volume", "source": "postgres_data", "target": "/var/lib/postgresql/data", "volume": {}}
            ],
        },
        "redis": {
            **service_defaults,
            "image": "redis@example",
            "read_only": True,
            "tmpfs": ["/tmp:size=8m,mode=0700,uid=65532,gid=65532"],
            "networks": {"cache": None},
            "secrets": [{"source": "redis_password", "target": "/run/secrets/redis_password"}],
            "volumes": [{"type": "volume", "source": "redis_data", "target": "/data", "volume": {}}],
        },
    }
    config = {
        "services": services,
        "networks": {
            "public": {"name": "alpha_public"},
            "api": {
                "name": "alpha_api",
                "internal": True,
                "ipam": {"config": [{"subnet": "172.31.250.0/24"}]},
            },
            "web": {"name": "alpha_web", "internal": True},
            "database": {"name": "alpha_database", "internal": True},
            "cache": {"name": "alpha_cache", "internal": True},
        },
        "volumes": {name: {"name": f"alpha_{name}"} for name in EXPECTED_VOLUMES},
        "secrets": {
            name: {"name": f"alpha_{name}", "file": str(app_root / ".secrets" / name)}
            for name in EXPECTED_SECRETS
        },
    }
    env = {
        "ALPHA_BIND_ADDRESS": "127.0.0.1",
        "ALPHA_COMPOSE_PROJECT_NAME": "alpha",
        "ALPHA_HTTP_PORT": "80",
        "ALPHA_HTTPS_PORT": "443",
        "ALPHA_POSTGRES_DB": "alpha",
        "ALPHA_POSTGRES_OWNER_USER": "alpha",
        "ALPHA_POSTGRES_MIGRATOR_USER": "alpha_migrator",
        "ALPHA_POSTGRES_RUNTIME_USER": "alpha_app",
    }
    config["name"] = "alpha"
    return config, env


def run_self_test() -> None:
    app_root = pathlib.Path("/srv/ctfnight")
    canonical, env = canonical_fixture(app_root)
    validate_compose(canonical, app_root, env, check_paths=False)
    mutations = {
        "extra_service": lambda value: value["services"].update(evil={}),
        "privileged": lambda value: value["services"]["backend"].update(privileged=True),
        "read_only": lambda value: value["services"]["backend"].update(read_only=False),
        "cap_drop": lambda value: value["services"]["backend"].update(cap_drop=[]),
        "capability": lambda value: value["services"]["backend"].update(cap_add=["SYS_ADMIN"]),
        "postgres_user": lambda value: value["services"]["postgres"].update(user="65532:65532"),
        "security_opt": lambda value: value["services"]["backend"].update(security_opt=["seccomp=unconfined"]),
        "tmpfs_unbounded": lambda value: value["services"]["backend"].update(tmpfs=["/tmp"]),
        "device_via_deploy": lambda value: value["services"]["backend"].update(
            deploy={"resources": {"reservations": {"devices": [{"capabilities": ["gpu"]}]}}}
        ),
        "resolver_override": lambda value: value["services"]["caddy"].update(
            extra_hosts=["backend:203.0.113.10"]
        ),
        "pull_policy": lambda value: value["services"]["backend"].update(pull_policy="always"),
        "published_backend": lambda value: value["services"]["backend"].update(ports=[{"target": 8000}]),
        "caddy_bind": lambda value: value["services"]["caddy"]["ports"][0].update(host_ip="0.0.0.0"),
        "caddy_protocol": lambda value: value["services"]["caddy"]["ports"][0].update(protocol="udp"),
        "secret_grant": lambda value: value["services"]["frontend"].update(
            secrets=[{"source": "alpha_secret_key", "target": "/run/secrets/alpha_secret_key"}]
        ),
        "plaintext_file_suffix": lambda value: value["services"]["backend"]["environment"].update(
            EVIL_PASSWORD_FILE="still-plaintext"
        ),
        "runtime_owner_role": lambda value: value["services"]["backend"]["environment"].update(
            ALPHA_DATABASE_USER="alpha"
        ),
        "runtime_owner_secret": lambda value: value["services"]["backend"]["secrets"].append(
            {
                "source": "postgres_owner_password",
                "target": "/run/secrets/postgres_owner_password",
            }
        ),
        "migrate_image": lambda value: value["services"]["migrate"].update(image="unscanned-migrate"),
        "migration_dependency": lambda value: value["services"]["backend"]["depends_on"][
            "migrate"
        ].update(condition="service_started"),
        "role_entrypoint": lambda value: value["services"]["db-roles"].update(
            entrypoint=["/bin/sh"]
        ),
        "secret_source": lambda value: value["secrets"]["admin_password"].update(file="/tmp/password"),
        "network_internal": lambda value: value["networks"]["database"].update(internal=False),
        "network_daemon_name": lambda value: value["networks"]["database"].update(name="shared"),
        "network_membership": lambda value: value["services"]["backend"]["networks"].update(public=None),
        "static_api_ip": lambda value: value["services"]["backend"]["networks"]["api"].update(
            ipv4_address="172.31.250.2"
        ),
        "docker_socket": lambda value: value["services"]["backend"].update(
            volumes=[
                {
                    "type": "bind",
                    "source": "/var/run/docker.sock",
                    "target": "/var/run/docker.sock",
                    "read_only": True,
                }
            ]
        ),
        "volume_daemon_name": lambda value: value["volumes"]["postgres_data"].update(name="shared"),
        "secret_daemon_name": lambda value: value["secrets"]["admin_password"].update(name="shared"),
        "build_target": lambda value: value["services"]["backend"]["build"].update(target="builder"),
    }
    for label, mutate in mutations.items():
        candidate = copy.deepcopy(canonical)
        mutate(candidate)
        try:
            validate_compose(candidate, app_root, env, check_paths=False)
        except ComposeSecurityError:
            continue
        raise ComposeSecurityError(f"negative regression이 거부되지 않았습니다: {label}")


def main(argv: list[str]) -> int:
    try:
        if argv == ["--self-test"]:
            run_self_test()
            print("Compose security validator self-test 통과.")
            return 0
        if len(argv) != 3:
            raise ComposeSecurityError("사용법: validate-compose-security.py CONFIG_JSON APP_ROOT ENV_FILE")
        config_path = pathlib.Path(argv[0])
        app_root = pathlib.Path(argv[1]).resolve()
        env_file = pathlib.Path(argv[2])
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ComposeSecurityError(f"Compose render를 읽지 못했습니다: {exc}") from exc
        require_mapping(config, "Compose render")
        env = read_required_env(env_file)
        images = validate_compose(config, app_root, env)
        print(f"project={config['name']}")
        for name in ("postgres", "redis"):
            print(f"{name}={images[name]}")
        return 0
    except ComposeSecurityError as exc:
        print(f"security gate 오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
