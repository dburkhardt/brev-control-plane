import io
import json

from brev_control_plane.brev import BrevCommandError
from brev_control_plane.cli import main


class FakeBrevClient:
    def __init__(self):
        self.refreshed = False
        self.created = []
        self.deleted = []
        self.exec_calls = []
        self.exec_outputs = {}
        self.instances = [{"id": "inst-1", "name": "worker", "status": "running"}]
        self.org = "personal"

    def list_instances(self):
        self.refreshed = True
        return self.instances

    def search_cpu(self):
        return [{"type": "cpu-small", "vcpus": 4}]

    def active_org(self):
        return self.org

    def create_instance(self, *, name, instance_type, timeout_seconds):
        self.created.append(
            {
                "name": name,
                "instance_type": instance_type,
                "timeout_seconds": timeout_seconds,
            }
        )
        return f"created {name}"

    def delete_instance(self, name):
        self.deleted.append(name)
        return f"deleted {name}"

    def exec_instances(self, names, command, *, host=False):
        self.exec_calls.append({"names": names, "command": command, "host": host})
        return f"ran {command}"

    def exec_instance(self, name, command, *, host=False):
        self.exec_calls.append({"name": name, "command": command, "host": host})
        output = self.exec_outputs.get(name, f"ran {command} on {name}")
        if isinstance(output, Exception):
            raise output
        return output


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


def test_cli_fleet_apply_requires_confirmation():
    stdout = io.StringIO()
    stderr = io.StringIO()
    client = FakeBrevClient()

    code = main(
        [
            "fleet",
            "apply",
            "--workers",
            "2",
            "--type",
            "n2d-highcpu-2",
            "--name-prefix",
            "smoke",
        ],
        stdout=stdout,
        stderr=stderr,
        client=client,
    )

    assert code == 2
    assert client.created == []
    assert "requires --yes" in json.loads(stderr.getvalue())["error"]


def test_cli_fleet_apply_creates_named_instances_with_confirmation():
    stdout = io.StringIO()
    client = FakeBrevClient()

    code = main(
        [
            "fleet",
            "apply",
            "--workers",
            "2",
            "--type",
            "n2d-highcpu-2",
            "--name-prefix",
            "smoke",
            "--timeout-seconds",
            "900",
            "--yes",
        ],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    assert client.created == [
        {
            "name": "smoke-001",
            "instance_type": "n2d-highcpu-2",
            "timeout_seconds": 900,
        },
        {
            "name": "smoke-002",
            "instance_type": "n2d-highcpu-2",
            "timeout_seconds": 900,
        },
    ]
    payload = json.loads(stdout.getvalue())
    assert payload["created"] == ["smoke-001", "smoke-002"]


def test_cli_fleet_apply_rejects_unexpected_active_org():
    stdout = io.StringIO()
    stderr = io.StringIO()
    client = FakeBrevClient()
    client.org = "personal"

    code = main(
        [
            "fleet",
            "apply",
            "--workers",
            "1",
            "--type",
            "n2d-highcpu-2",
            "--name-prefix",
            "smoke",
            "--require-org",
            "team-a",
            "--yes",
        ],
        stdout=stdout,
        stderr=stderr,
        client=client,
    )

    assert code == 2
    assert client.created == []
    assert "active Brev org is 'personal'" in json.loads(stderr.getvalue())["error"]


def test_cli_fleet_exec_runs_command_on_matching_instances():
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [
        {"id": "inst-1", "name": "smoke-001", "status": "RUNNING"},
        {"id": "inst-2", "name": "smoke-002", "status": "RUNNING"},
        {"id": "inst-3", "name": "other-001", "status": "RUNNING"},
    ]

    code = main(
        ["fleet", "exec", "--name-prefix", "smoke", "--", "echo", "hello"],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    assert client.exec_calls == [
        {
            "name": "smoke-001",
            "command": "echo hello",
            "host": False,
        },
        {
            "name": "smoke-002",
            "command": "echo hello",
            "host": False,
        }
    ]
    payload = json.loads(stdout.getvalue())
    assert payload["instances"] == ["smoke-001", "smoke-002"]
    assert payload["results"] == [
        {"instance": "smoke-001", "ok": True, "output": "ran echo hello on smoke-001"},
        {"instance": "smoke-002", "ok": True, "output": "ran echo hello on smoke-002"},
    ]


def test_cli_fleet_exec_reports_each_failed_instance():
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [
        {"id": "inst-1", "name": "smoke-001", "status": "RUNNING"},
        {"id": "inst-2", "name": "smoke-002", "status": "RUNNING"},
    ]
    client.exec_outputs = {
        "smoke-001": "ok",
        "smoke-002": BrevCommandError("remote command failed"),
    }

    code = main(
        ["fleet", "exec", "--name-prefix", "smoke", "--host", "--", "uptime"],
        stdout=stdout,
        client=client,
    )

    assert code == 2
    assert client.exec_calls == [
        {"name": "smoke-001", "command": "uptime", "host": True},
        {"name": "smoke-002", "command": "uptime", "host": True},
    ]
    payload = json.loads(stdout.getvalue())
    assert payload["results"] == [
        {"instance": "smoke-001", "ok": True, "output": "ok"},
        {"instance": "smoke-002", "ok": False, "error": "remote command failed"},
    ]


def test_cli_fleet_down_deletes_only_matching_instances_with_confirmation():
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [
        {"id": "inst-1", "name": "smoke-001", "status": "RUNNING"},
        {"id": "inst-2", "name": "smoke-002", "status": "RUNNING"},
        {"id": "inst-3", "name": "other-001", "status": "RUNNING"},
    ]

    code = main(
        ["fleet", "down", "--name-prefix", "smoke", "--yes"],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    assert client.deleted == ["smoke-001", "smoke-002"]
    payload = json.loads(stdout.getvalue())
    assert payload["deleted"] == ["smoke-001", "smoke-002"]


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
