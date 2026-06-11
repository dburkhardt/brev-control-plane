from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


class JobSpecError(ValueError):
    """Raised when a shell job specification is invalid."""


@dataclass(frozen=True)
class JobSpec:
    command: str
    env: dict[str, str]
    artifacts: list[str]
    max_runtime_seconds: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "env": dict(self.env),
            "artifacts": list(self.artifacts),
            "max_runtime_seconds": self.max_runtime_seconds,
        }


def load_job_spec(path: str | Path) -> JobSpec:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise JobSpecError(f"job spec is not valid JSON: {exc}") from exc
    return validate_job_spec(payload)


def validate_job_spec(payload: Any) -> JobSpec:
    if not isinstance(payload, dict):
        raise JobSpecError("job spec must be a JSON object")

    command = payload.get("command")
    if command is None:
        raise JobSpecError("command is required")
    if not isinstance(command, str) or not command.strip():
        raise JobSpecError("command must be a non-empty string")

    env = payload.get("env", {})
    if not isinstance(env, dict):
        raise JobSpecError("env must be an object")
    for key, value in env.items():
        if not isinstance(key, str):
            raise JobSpecError("env keys must be strings")
        if not isinstance(value, str):
            raise JobSpecError("env values must be strings")

    artifacts = payload.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise JobSpecError("artifacts must be an array")
    if not all(isinstance(item, str) for item in artifacts):
        raise JobSpecError("artifacts must be strings")

    max_runtime_seconds = payload.get("max_runtime_seconds")
    if max_runtime_seconds is not None and not _is_positive_int(max_runtime_seconds):
        raise JobSpecError("max_runtime_seconds must be positive")

    return JobSpec(
        command=command,
        env=dict(env),
        artifacts=list(artifacts),
        max_runtime_seconds=max_runtime_seconds,
    )


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
