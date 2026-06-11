import json

import pytest

from brev_control_plane.jobs import JobSpecError, load_job_spec, validate_job_spec


def test_validate_job_spec_accepts_minimal_shell_job():
    spec = validate_job_spec(
        {
            "command": "python -c 'print(42)'",
            "env": {"EXAMPLE": "value"},
            "artifacts": ["outputs/"],
            "max_runtime_seconds": 120,
        }
    )

    assert spec.command == "python -c 'print(42)'"
    assert spec.env == {"EXAMPLE": "value"}
    assert spec.artifacts == ["outputs/"]
    assert spec.max_runtime_seconds == 120
    assert spec.bundle is None


def test_validate_job_spec_accepts_bundle_object():
    spec = validate_job_spec(
        {
            "command": "python3 -m pytest -q",
            "bundle": {"source": "./example-project", "exclude": [".git", ".venv"]},
        }
    )

    assert spec.bundle == {
        "source": "./example-project",
        "exclude": [".git", ".venv"],
    }


def test_validate_job_spec_applies_safe_defaults():
    spec = validate_job_spec({"command": "echo hello"})

    assert spec.env == {}
    assert spec.artifacts == []
    assert spec.max_runtime_seconds is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "command is required"),
        ({"command": ""}, "command must be a non-empty string"),
        ({"command": "echo hi", "env": {"A": 1}}, "env values must be strings"),
        ({"command": "echo hi", "artifacts": [3]}, "artifacts must be strings"),
        (
            {"command": "echo hi", "max_runtime_seconds": 0},
            "max_runtime_seconds must be positive",
        ),
        (
            {"command": "echo hi", "max_runtime_seconds": True},
            "max_runtime_seconds must be positive",
        ),
        ({"command": "echo hi", "bundle": []}, "bundle must be an object"),
    ],
)
def test_validate_job_spec_rejects_invalid_payloads(payload, message):
    with pytest.raises(JobSpecError, match=message):
        validate_job_spec(payload)


def test_load_job_spec_reads_json_file(tmp_path):
    path = tmp_path / "job.json"
    path.write_text(json.dumps({"command": "echo from file"}), encoding="utf-8")

    spec = load_job_spec(path)

    assert spec.command == "echo from file"
