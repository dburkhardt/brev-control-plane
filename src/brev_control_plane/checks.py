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
printf 'DOCKER_DIRECT=%s\n' "$(docker --version 2>&1 | head -n 1)"
printf 'DOCKER_SUDO=%s\n' "$(sudo docker --version 2>&1 | head -n 1)"
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

    direct = values.get("DOCKER_DIRECT", "")
    sudo = values.get("DOCKER_SUDO", "")
    if direct.startswith("Docker version"):
        docker_access = "direct"
        docker_version = direct
    elif sudo.startswith("Docker version"):
        docker_access = "sudo"
        docker_version = sudo
    else:
        docker_access = "missing"
        docker_version = ""

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
