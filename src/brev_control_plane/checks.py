from __future__ import annotations

import shlex


def build_check_command() -> str:
    script = r"""
set +e
printf 'INSTANCE=%s\n' "$(hostname 2>/dev/null | head -n 1)"
printf 'EGRESS_IP=%s\n' "$(curl -fsSL --max-time 10 https://ifconfig.me 2>/dev/null | head -n 1)"
printf 'UNAME=%s\n' "$(uname -a 2>/dev/null | head -n 1)"
printf 'USER=%s\n' "$(id -un 2>/dev/null | head -n 1)"
printf 'DOCKER_PATH=%s\n' "$(command -v docker 2>/dev/null | head -n 1)"
printf 'DOCKER_DIRECT_VERSION=%s\n' "$(timeout 10 docker --version 2>&1 | head -n 1)"
printf 'DOCKER_DIRECT_API=%s\n' "$(timeout 10 bash -lc 'docker ps >/dev/null' >/dev/null 2>&1 && echo ok || echo failed)"
printf 'DOCKER_SUDO_VERSION=%s\n' "$(timeout 10 sudo -n docker --version 2>&1 | head -n 1)"
printf 'DOCKER_SUDO_API=%s\n' "$(timeout 10 sudo -n docker ps >/dev/null 2>&1 && echo ok || echo failed)"
printf 'DOCKER_SG_VERSION=%s\n' "$(timeout 10 sg docker -c 'docker --version' 2>&1 | head -n 1)"
printf 'DOCKER_SG_API=%s\n' "$(timeout 10 sg docker -c 'docker ps >/dev/null' >/dev/null 2>&1 && echo ok || echo failed)"
printf 'PYTHON3=%s\n' "$(python3 --version 2>&1 | head -n 1)"
printf 'DISK_ROOT=%s\n' "$(df -h / 2>/dev/null | tail -n 1)"
"""
    return f"bash -lc {shlex.quote(script)}"


def parse_check_output(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value

    docker_access = "missing"
    docker_version = ""
    for access, prefix in (
        ("direct", "DOCKER_DIRECT"),
        ("sg", "DOCKER_SG"),
        ("sudo", "DOCKER_SUDO"),
    ):
        version = values.get(f"{prefix}_VERSION", values.get(prefix, ""))
        api = values.get(f"{prefix}_API")
        if api is None:
            api = "ok" if version.startswith("Docker version") else "failed"
        if api == "ok" and version.startswith("Docker version"):
            docker_access = access
            docker_version = version
            break

    return {
        "instance": values.get("INSTANCE", ""),
        "egress_ip": values.get("EGRESS_IP", ""),
        "uname": values.get("UNAME", ""),
        "user": values.get("USER", ""),
        "docker_path": values.get("DOCKER_PATH", ""),
        "docker_access": docker_access,
        "docker_version": docker_version,
        "python3": values.get("PYTHON3", ""),
        "disk_root": values.get("DISK_ROOT", ""),
    }
