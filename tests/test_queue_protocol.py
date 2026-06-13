import json

import pytest

from brev_control_plane.queue_protocol import (
    QueueJob,
    QueueProtocolError,
    validate_queue_token,
)


def test_queue_job_round_trips_json_with_safe_defaults():
    job = QueueJob(command="echo hello", experiment_id="exp-a")

    loaded = QueueJob.from_json(job.to_json())

    assert loaded == job
    assert json.loads(job.to_json()) == {
        "command": "echo hello",
        "env": {},
        "experiment_id": "exp-a",
        "input_files": [],
        "max_attempts": 1,
        "max_runtime_seconds": None,
        "output_paths": [],
    }


def test_queue_job_round_trips_input_files():
    job = QueueJob(
        command="python3 run.py",
        experiment_id="exp-a",
        input_files=[
            {
                "path": "payload/job.json",
                "content_b64": "eyJvayI6IHRydWV9",
                "mode": "0644",
            }
        ],
    )

    loaded = QueueJob.from_json(job.to_json())

    assert loaded == job
    assert loaded.input_files[0]["path"] == "payload/job.json"


def test_queue_job_validates_relative_output_paths_like_artifacts():
    job = QueueJob(
        command="python3 run.py",
        experiment_id="exp-a",
        output_paths=["reports/result.json"],
    )

    assert QueueJob.from_dict(job.to_dict()).output_paths == ["reports/result.json"]


@pytest.mark.parametrize(
    "input_path",
    ["", ".", "..", "../secret.txt", "/tmp/in.txt", r"C:\tmp\in.txt"],
)
def test_queue_job_rejects_unsafe_input_paths(input_path):
    with pytest.raises(QueueProtocolError, match="input_files"):
        QueueJob(
            command="echo hi",
            experiment_id="exp-a",
            input_files=[{"path": input_path, "content_b64": "AA=="}],
        )


@pytest.mark.parametrize(
    "output_path",
    ["", ".", "..", "../secret.txt", "/tmp/out.txt", r"C:\tmp\out.txt"],
)
def test_queue_job_rejects_unsafe_output_paths(output_path):
    with pytest.raises(QueueProtocolError, match="output_paths"):
        QueueJob(
            command="echo hi",
            experiment_id="exp-a",
            output_paths=[output_path],
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "command is required"),
        ({"command": "", "experiment_id": "exp-a"}, "command must be a non-empty string"),
        ({"command": "echo hi"}, "experiment_id is required"),
        (
            {"command": "echo hi", "experiment_id": "exp-a", "env": {"A": 1}},
            "env values must be strings",
        ),
        (
            {"command": "echo hi", "experiment_id": "exp-a", "max_attempts": 0},
            "max_attempts must be positive",
        ),
        (
            {"command": "echo hi", "experiment_id": "exp-a", "max_runtime_seconds": True},
            "max_runtime_seconds must be positive",
        ),
    ],
)
def test_queue_job_rejects_invalid_payloads(payload, message):
    with pytest.raises(QueueProtocolError, match=message):
        QueueJob.from_dict(payload)


def test_validate_queue_token_uses_constant_time_compare(monkeypatch):
    calls = []

    def fake_compare_digest(left, right):
        calls.append((left, right))
        return left == right

    monkeypatch.setattr(
        "brev_control_plane.queue_protocol.hmac.compare_digest",
        fake_compare_digest,
    )

    assert validate_queue_token("expected", "expected") is True
    assert validate_queue_token("expected", "wrong") is False
    assert calls == [("expected", "expected"), ("expected", "wrong")]
