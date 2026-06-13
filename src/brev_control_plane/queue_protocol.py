from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime
import hmac
import json
from typing import Any

from .jobs import JobSpecError, _validate_artifact_path


class QueueProtocolError(ValueError):
    """Raised when a queue request or response payload is invalid."""


@dataclass(frozen=True)
class QueueJob:
    command: str
    experiment_id: str
    env: dict[str, str] = field(default_factory=dict)
    input_files: list[dict[str, str]] = field(default_factory=list)
    output_paths: list[str] = field(default_factory=list)
    max_runtime_seconds: int | None = None
    max_attempts: int = 1

    def __post_init__(self) -> None:
        validated = self.from_dict(self.to_dict())
        object.__setattr__(self, "command", validated.command)
        object.__setattr__(self, "experiment_id", validated.experiment_id)
        object.__setattr__(self, "env", validated.env)
        object.__setattr__(self, "input_files", validated.input_files)
        object.__setattr__(self, "output_paths", validated.output_paths)
        object.__setattr__(self, "max_runtime_seconds", validated.max_runtime_seconds)
        object.__setattr__(self, "max_attempts", validated.max_attempts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "env": dict(self.env),
            "experiment_id": self.experiment_id,
            "input_files": [dict(item) for item in self.input_files],
            "max_attempts": self.max_attempts,
            "max_runtime_seconds": self.max_runtime_seconds,
            "output_paths": list(self.output_paths),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> "QueueJob":
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise QueueProtocolError(f"queue job is not valid JSON: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, payload: Any) -> "QueueJob":
        if not isinstance(payload, dict):
            raise QueueProtocolError("queue job must be a JSON object")

        command = payload.get("command")
        if command is None:
            raise QueueProtocolError("command is required")
        if not isinstance(command, str) or not command.strip():
            raise QueueProtocolError("command must be a non-empty string")

        experiment_id = payload.get("experiment_id")
        if experiment_id is None:
            raise QueueProtocolError("experiment_id is required")
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            raise QueueProtocolError("experiment_id must be a non-empty string")

        env = payload.get("env", {})
        if not isinstance(env, dict):
            raise QueueProtocolError("env must be an object")
        for key, value in env.items():
            if not isinstance(key, str):
                raise QueueProtocolError("env keys must be strings")
            if not isinstance(value, str):
                raise QueueProtocolError("env values must be strings")

        input_files = payload.get("input_files", [])
        if not isinstance(input_files, list):
            raise QueueProtocolError("input_files must be an array")
        normalized_input_files = [_validate_input_file(item) for item in input_files]

        output_paths = payload.get("output_paths", [])
        if not isinstance(output_paths, list):
            raise QueueProtocolError("output_paths must be an array")
        if not all(isinstance(item, str) for item in output_paths):
            raise QueueProtocolError("output_paths must be strings")
        for item in output_paths:
            try:
                _validate_artifact_path(item)
            except JobSpecError as exc:
                raise QueueProtocolError(f"output_paths: {exc}") from exc

        max_runtime_seconds = payload.get("max_runtime_seconds")
        if max_runtime_seconds is not None and not _is_positive_int(max_runtime_seconds):
            raise QueueProtocolError("max_runtime_seconds must be positive")

        max_attempts = payload.get("max_attempts", 1)
        if not _is_positive_int(max_attempts):
            raise QueueProtocolError("max_attempts must be positive")

        return cls.__new_validated(
            command=command,
            experiment_id=experiment_id,
            env=dict(env),
            input_files=normalized_input_files,
            output_paths=list(output_paths),
            max_runtime_seconds=max_runtime_seconds,
            max_attempts=max_attempts,
        )

    @classmethod
    def __new_validated(
        cls,
        *,
        command: str,
        experiment_id: str,
        env: dict[str, str],
        input_files: list[dict[str, str]],
        output_paths: list[str],
        max_runtime_seconds: int | None,
        max_attempts: int,
    ) -> "QueueJob":
        job = object.__new__(cls)
        object.__setattr__(job, "command", command)
        object.__setattr__(job, "experiment_id", experiment_id)
        object.__setattr__(job, "env", env)
        object.__setattr__(job, "input_files", input_files)
        object.__setattr__(job, "output_paths", output_paths)
        object.__setattr__(job, "max_runtime_seconds", max_runtime_seconds)
        object.__setattr__(job, "max_attempts", max_attempts)
        return job


@dataclass(frozen=True)
class QueueLease:
    job_id: str
    lease_id: str
    worker_id: str
    attempt: int
    expires_at: datetime
    job: QueueJob

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "expires_at": self.expires_at.isoformat(),
            "job": self.job.to_dict(),
            "job_id": self.job_id,
            "lease_id": self.lease_id,
            "worker_id": self.worker_id,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "QueueLease":
        if not isinstance(payload, dict):
            raise QueueProtocolError("queue lease must be a JSON object")
        try:
            return cls(
                job_id=_required_string(payload, "job_id"),
                lease_id=_required_string(payload, "lease_id"),
                worker_id=_required_string(payload, "worker_id"),
                attempt=_required_positive_int(payload, "attempt"),
                expires_at=datetime.fromisoformat(_required_string(payload, "expires_at")),
                job=QueueJob.from_dict(payload.get("job")),
            )
        except ValueError as exc:
            raise QueueProtocolError(str(exc)) from exc


def validate_queue_token(expected: str, supplied: str | None) -> bool:
    if supplied is None:
        supplied = ""
    return hmac.compare_digest(expected, supplied)


def _validate_input_file(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise QueueProtocolError("input_files entries must be objects")
    path = value.get("path")
    if not isinstance(path, str):
        raise QueueProtocolError("input_files path must be a string")
    try:
        _validate_artifact_path(path)
    except JobSpecError as exc:
        raise QueueProtocolError(f"input_files: {exc}") from exc
    content_b64 = value.get("content_b64")
    if not isinstance(content_b64, str):
        raise QueueProtocolError("input_files content_b64 must be a string")
    try:
        base64.b64decode(content_b64.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise QueueProtocolError("input_files content_b64 must be valid base64") from exc
    mode = value.get("mode", "0644")
    if not isinstance(mode, str):
        raise QueueProtocolError("input_files mode must be a string")
    try:
        parsed = int(mode, 8)
    except ValueError as exc:
        raise QueueProtocolError("input_files mode must be octal") from exc
    if parsed < 0 or parsed > 0o777:
        raise QueueProtocolError("input_files mode must be between 0000 and 0777")
    return {"path": path, "content_b64": content_b64, "mode": mode}


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise QueueProtocolError(f"{key} must be a non-empty string")
    return value


def _required_positive_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not _is_positive_int(value):
        raise QueueProtocolError(f"{key} must be positive")
    return value
