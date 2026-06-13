from __future__ import annotations

import re
import shlex


def render_worker_bootstrap(
    *,
    repo_url: str,
    server_url: str,
    token_env_name: str,
    worker_name: str,
    repo_dir: str = "$HOME/brev-control-plane",
    work_dir: str = "$HOME/brev-control-plane-work",
) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env_name):
        raise ValueError("token_env_name must be a shell environment variable name")
    token_ref = f"${{{token_env_name}:?required}}"
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            f': "{token_ref}"',
            "if ! command -v git >/dev/null || ! command -v python3 >/dev/null || ! python3 -m pip --version >/dev/null 2>&1; then",
            "  sudo apt-get update",
            "  sudo apt-get install -y git python3 python3-pip",
            "fi",
            f"rm -rf {repo_dir}",
            f"git clone {shlex.quote(repo_url)} {repo_dir}",
            f"cd {repo_dir}",
            "python3 -m pip install -e .",
            "mkdir -p " + work_dir,
            "nohup python3 -m brev_control_plane worker run "
            f"--server-url {shlex.quote(server_url)} "
            f"--token-env {shlex.quote(token_env_name)} "
            f"--worker-id {shlex.quote(worker_name)} "
            f"--work-dir {work_dir} "
            f"> {work_dir}/worker.log 2>&1 &",
            "",
        ]
    )
