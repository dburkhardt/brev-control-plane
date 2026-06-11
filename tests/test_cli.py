import io
import json

from brev_control_plane.cli import main


class FakeBrevClient:
    def __init__(self):
        self.refreshed = False

    def list_instances(self):
        self.refreshed = True
        return [{"id": "inst-1", "name": "worker", "status": "running"}]

    def search_cpu(self):
        return [{"type": "cpu-small", "vcpus": 4}]


def test_cli_fleet_plan_outputs_json():
    stdout = io.StringIO()

    code = main(
        [
            "fleet",
            "plan",
            "--workers",
            "2",
            "--cpu-min-vcpus",
            "4",
            "--name-prefix",
            "worker",
        ],
        stdout=stdout,
    )

    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["action"] == "plan"
    assert payload["workers"] == [
        {"index": 1, "name": "worker-001"},
        {"index": 2, "name": "worker-002"},
    ]
    assert payload["safety"]["creates_instances"] is False


def test_cli_inventory_refresh_uses_injected_brev_client(tmp_path):
    stdout = io.StringIO()
    client = FakeBrevClient()

    code = main(
        ["inventory", "refresh", "--db", str(tmp_path / "state.db")],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    assert client.refreshed is True
    payload = json.loads(stdout.getvalue())
    assert payload == {"instances": 1, "events": 1}


def test_cli_jobs_validate_outputs_valid_status(tmp_path):
    path = tmp_path / "job.json"
    path.write_text(json.dumps({"command": "echo hi"}), encoding="utf-8")
    stdout = io.StringIO()

    code = main(["jobs", "validate", str(path)], stdout=stdout)

    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload == {"valid": True, "command": "echo hi"}
