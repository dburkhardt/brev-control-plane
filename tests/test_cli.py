import io
import json
import threading
import time

from brev_control_plane.brev import BrevCommandError
from brev_control_plane.cli import main
from brev_control_plane.state import StateStore


class FakeBrevClient:
    def __init__(self):
        self.refreshed = False
        self.created = []
        self.deleted = []
        self.delete_outputs = {}
        self.exec_calls = []
        self.exec_outputs = {}
        self.copy_calls = []
        self.instances = [{"id": "inst-1", "name": "worker", "status": "running"}]
        self.list_results = None
        self.org = "personal"

    def list_instances(self):
        self.refreshed = True
        if self.list_results is not None:
            if len(self.list_results) > 1:
                return self.list_results.pop(0)
            return self.list_results[0]
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
        output = self.delete_outputs.get(name, f"deleted {name}")
        if isinstance(output, Exception):
            raise output
        return output

    def exec_instances(self, names, command, *, host=False):
        self.exec_calls.append({"names": names, "command": command, "host": host})
        return f"ran {command}"

    def exec_instance(self, name, command, *, host=False):
        self.exec_calls.append({"name": name, "command": command, "host": host})
        output = self.exec_outputs.get(name, f"ran {command} on {name}")
        if isinstance(output, Exception):
            raise output
        return output

    def copy_to_instance(self, local_path, instance_name, remote_path):
        self.copy_calls.append(
            {
                "local_path": str(local_path),
                "instance_name": instance_name,
                "remote_path": remote_path,
            }
        )
        return f"copied {instance_name}"


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


def test_cli_fleet_apply_skips_existing_named_instances_when_scaling_up():
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [
        {"id": "inst-1", "name": "smoke-001", "status": "RUNNING"},
        {"id": "inst-2", "name": "smoke-002", "status": "RUNNING"},
    ]

    code = main(
        [
            "fleet",
            "apply",
            "--workers",
            "4",
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
    assert [item["name"] for item in client.created] == ["smoke-003", "smoke-004"]
    payload = json.loads(stdout.getvalue())
    assert payload["existing"] == ["smoke-001", "smoke-002"]
    assert payload["created"] == ["smoke-003", "smoke-004"]


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


def test_cli_fleet_apply_records_created_events(tmp_path):
    stdout = io.StringIO()
    client = FakeBrevClient()
    db_path = tmp_path / "state.db"

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
            "--db",
            str(db_path),
            "--yes",
        ],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    events = StateStore(db_path).list_events()
    assert [(event["event_type"], event["payload"]) for event in events] == [
        (
            "fleet.apply.created",
            {
                "instance_name": "smoke-001",
                "instance_type": "n2d-highcpu-2",
                "output": "created smoke-001",
            },
        )
    ]


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


def test_cli_fleet_exec_records_completed_and_failed_events(tmp_path):
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
    db_path = tmp_path / "state.db"

    code = main(
        [
            "fleet",
            "exec",
            "--name-prefix",
            "smoke",
            "--db",
            str(db_path),
            "--",
            "uptime",
        ],
        stdout=stdout,
        client=client,
    )

    assert code == 2
    events = StateStore(db_path).list_events()
    assert [event["event_type"] for event in events] == [
        "fleet.exec.completed",
        "fleet.exec.failed",
    ]
    assert events[0]["payload"]["instance_name"] == "smoke-001"
    assert events[0]["payload"]["command"] == "uptime"
    assert events[0]["payload"]["output"] == "ok"
    assert events[1]["payload"]["instance_name"] == "smoke-002"
    assert events[1]["payload"]["error"] == "remote command failed"


def test_cli_fleet_check_reports_matching_instance_capabilities():
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [
        {"id": "inst-1", "name": "smoke-001", "status": "RUNNING"},
        {"id": "inst-2", "name": "other-001", "status": "RUNNING"},
    ]
    client.exec_outputs = {
        "smoke-001": "\n".join(
            [
                "INSTANCE=remote-host",
                "EGRESS_IP=203.0.113.20",
                "UNAME=Linux remote-host",
                "USER=ubuntu",
                "DOCKER_PATH=/usr/bin/docker",
                "DOCKER_DIRECT=permission denied",
                "DOCKER_SUDO=Docker version 25.0.0, build abc",
                "PYTHON3=Python 3.11.8",
                "DISK_ROOT=/dev/root 30G 10G 20G 34% /",
            ]
        )
    }

    code = main(
        ["fleet", "check", "--name-prefix", "smoke"],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    assert len(client.exec_calls) == 1
    assert client.exec_calls[0]["name"] == "smoke-001"
    assert client.exec_calls[0]["host"] is True
    assert "ifconfig.me" in client.exec_calls[0]["command"]
    payload = json.loads(stdout.getvalue())
    assert payload["instances"] == ["smoke-001"]
    assert payload["results"] == [
        {
            "instance": "smoke-001",
            "ok": True,
            "check": {
                "name": "smoke-001",
                "instance": "remote-host",
                "egress_ip": "203.0.113.20",
                "uname": "Linux remote-host",
                "user": "ubuntu",
                "docker_path": "/usr/bin/docker",
                "docker_access": "sudo",
                "docker_version": "Docker version 25.0.0, build abc",
                "python3": "Python 3.11.8",
                "disk_root": "/dev/root 30G 10G 20G 34% /",
            },
        }
    ]
    assert payload["checks"] == [
        {
            "name": "smoke-001",
            "instance": "remote-host",
            "egress_ip": "203.0.113.20",
            "uname": "Linux remote-host",
            "user": "ubuntu",
            "docker_path": "/usr/bin/docker",
            "docker_access": "sudo",
            "docker_version": "Docker version 25.0.0, build abc",
            "python3": "Python 3.11.8",
            "disk_root": "/dev/root 30G 10G 20G 34% /",
        }
    ]


def test_cli_fleet_check_continues_after_failed_instance_and_records_events(tmp_path):
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [
        {"id": "inst-1", "name": "smoke-001", "status": "RUNNING"},
        {"id": "inst-2", "name": "smoke-002", "status": "RUNNING"},
    ]
    client.exec_outputs = {
        "smoke-001": BrevCommandError("check failed"),
        "smoke-002": "\n".join(
            [
                "INSTANCE=remote-host-2",
                "EGRESS_IP=203.0.113.21",
                "UNAME=Linux remote-host-2",
                "USER=ubuntu",
                "DOCKER_PATH=/usr/bin/docker",
                "DOCKER_DIRECT=Docker version 25.0.0, build abc",
                "DOCKER_SUDO=Docker version 25.0.0, build abc",
                "PYTHON3=Python 3.11.8",
                "DISK_ROOT=/dev/root 30G 10G 20G 34% /",
            ]
        ),
    }
    db_path = tmp_path / "state.db"

    code = main(
        [
            "fleet",
            "check",
            "--name-prefix",
            "smoke",
            "--db",
            str(db_path),
        ],
        stdout=stdout,
        client=client,
    )

    assert code == 2
    assert [call["name"] for call in client.exec_calls] == ["smoke-001", "smoke-002"]
    payload = json.loads(stdout.getvalue())
    assert payload["instances"] == ["smoke-001", "smoke-002"]
    assert payload["checks"] == [
        {
            "name": "smoke-002",
            "instance": "remote-host-2",
            "egress_ip": "203.0.113.21",
            "uname": "Linux remote-host-2",
            "user": "ubuntu",
            "docker_path": "/usr/bin/docker",
            "docker_access": "direct",
            "docker_version": "Docker version 25.0.0, build abc",
            "python3": "Python 3.11.8",
            "disk_root": "/dev/root 30G 10G 20G 34% /",
        }
    ]
    assert payload["results"] == [
        {"instance": "smoke-001", "ok": False, "error": "check failed"},
        {"instance": "smoke-002", "ok": True, "check": payload["checks"][0]},
    ]
    events = StateStore(db_path).list_events()
    assert [event["event_type"] for event in events] == [
        "fleet.check.failed",
        "fleet.check.completed",
    ]
    assert events[0]["payload"] == {
        "instance_name": "smoke-001",
        "error": "check failed",
    }
    assert events[1]["payload"]["instance_name"] == "smoke-002"


def test_cli_fleet_down_deletes_only_matching_instances_with_confirmation():
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [
        {"id": "inst-1", "name": "smoke-001", "status": "RUNNING"},
        {"id": "inst-2", "name": "smoke-002", "status": "RUNNING"},
        {"id": "inst-3", "name": "other-001", "status": "RUNNING"},
    ]

    code = main(
        ["fleet", "down", "--name-prefix", "smoke", "--yes", "--no-wait"],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    assert client.deleted == ["smoke-001", "smoke-002"]
    payload = json.loads(stdout.getvalue())
    assert payload["deleted"] == ["smoke-001", "smoke-002"]
    assert payload["remaining"] == ["smoke-001", "smoke-002"]
    assert payload["verified_absent"] is False
    assert payload["delete_results"] == [
        {"instance": "smoke-001", "ok": True, "output": "deleted smoke-001"},
        {"instance": "smoke-002", "ok": True, "output": "deleted smoke-002"},
    ]


def test_cli_fleet_down_records_deleted_events(tmp_path):
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [{"id": "inst-1", "name": "smoke-001", "status": "RUNNING"}]
    db_path = tmp_path / "state.db"

    code = main(
        [
            "fleet",
            "down",
            "--name-prefix",
            "smoke",
            "--db",
            str(db_path),
            "--yes",
            "--no-wait",
        ],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    events = StateStore(db_path).list_events()
    assert [(event["event_type"], event["payload"]) for event in events] == [
        (
            "fleet.down.deleted",
            {"instance_name": "smoke-001", "output": "deleted smoke-001"},
        )
    ]


def test_cli_fleet_down_waits_until_matching_instances_are_gone():
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.list_results = [
        [
            {"id": "inst-1", "name": "smoke-001", "status": "RUNNING"},
            {"id": "inst-2", "name": "smoke-002", "status": "RUNNING"},
        ],
        [{"id": "inst-2", "name": "smoke-002", "status": "DELETING"}],
        [],
    ]

    code = main(
        [
            "fleet",
            "down",
            "--name-prefix",
            "smoke",
            "--yes",
            "--timeout-seconds",
            "1",
            "--poll-seconds",
            "0",
        ],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    assert client.deleted == ["smoke-001", "smoke-002"]
    payload = json.loads(stdout.getvalue())
    assert payload["deleted"] == ["smoke-001", "smoke-002"]
    assert payload["remaining"] == []
    assert payload["verified_absent"] is True


def test_cli_fleet_down_returns_two_when_wait_times_out():
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.list_results = [
        [{"id": "inst-1", "name": "smoke-001", "status": "RUNNING"}],
        [{"id": "inst-1", "name": "smoke-001", "status": "DELETING"}],
    ]

    code = main(
        [
            "fleet",
            "down",
            "--name-prefix",
            "smoke",
            "--yes",
            "--timeout-seconds",
            "0",
            "--poll-seconds",
            "0",
        ],
        stdout=stdout,
        client=client,
    )

    assert code == 2
    payload = json.loads(stdout.getvalue())
    assert payload["remaining"] == ["smoke-001"]
    assert payload["verified_absent"] is False


def test_cli_fleet_down_attempts_all_deletes_and_reports_failures(tmp_path):
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [
        {"id": "inst-1", "name": "smoke-001", "status": "RUNNING"},
        {"id": "inst-2", "name": "smoke-002", "status": "RUNNING"},
        {"id": "inst-3", "name": "smoke-003", "status": "RUNNING"},
    ]
    client.delete_outputs = {"smoke-002": BrevCommandError("delete failed")}
    db_path = tmp_path / "state.db"

    code = main(
        [
            "fleet",
            "down",
            "--name-prefix",
            "smoke",
            "--db",
            str(db_path),
            "--yes",
            "--no-wait",
        ],
        stdout=stdout,
        client=client,
    )

    assert code == 2
    assert client.deleted == ["smoke-001", "smoke-002", "smoke-003"]
    payload = json.loads(stdout.getvalue())
    assert payload["deleted"] == ["smoke-001", "smoke-003"]
    assert payload["remaining"] == ["smoke-001", "smoke-002", "smoke-003"]
    assert payload["verified_absent"] is False
    assert payload["delete_results"] == [
        {"instance": "smoke-001", "ok": True, "output": "deleted smoke-001"},
        {"instance": "smoke-002", "ok": False, "error": "delete failed"},
        {"instance": "smoke-003", "ok": True, "output": "deleted smoke-003"},
    ]
    events = StateStore(db_path).list_events()
    assert [event["event_type"] for event in events] == [
        "fleet.down.deleted",
        "fleet.down.failed",
        "fleet.down.deleted",
    ]
    assert events[1]["payload"] == {
        "instance_name": "smoke-002",
        "error": "delete failed",
    }


def test_cli_fleet_down_records_wait_timeout_events(tmp_path):
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.list_results = [
        [{"id": "inst-1", "name": "smoke-001", "status": "RUNNING"}],
        [{"id": "inst-1", "name": "smoke-001", "status": "DELETING"}],
    ]
    db_path = tmp_path / "state.db"

    code = main(
        [
            "fleet",
            "down",
            "--name-prefix",
            "smoke",
            "--db",
            str(db_path),
            "--yes",
            "--timeout-seconds",
            "0",
            "--poll-seconds",
            "0",
        ],
        stdout=stdout,
        client=client,
    )

    assert code == 2
    events = StateStore(db_path).list_events()
    assert [event["event_type"] for event in events] == [
        "fleet.down.deleted",
        "fleet.down.wait_timeout",
    ]
    assert events[1]["payload"] == {
        "instance_name": "smoke-001",
        "remaining": ["smoke-001"],
    }


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


def test_cli_jobs_run_copies_bundle_and_executes_command(tmp_path):
    source = tmp_path / "example-project"
    source.mkdir()
    (source / "test_example.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    ignored = source / ".git"
    ignored.mkdir()
    (ignored / "config").write_text("ignored", encoding="utf-8")
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps(
            {
                "command": "python3 -m pytest -q",
                "bundle": {"source": str(source), "exclude": [".git"]},
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [{"id": "inst-1", "name": "smoke-001", "status": "RUNNING"}]
    client.exec_outputs = {"smoke-001": "1 passed"}

    code = main(
        ["jobs", "run", str(job_path), "--name-prefix", "smoke", "--host"],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    assert client.copy_calls == [
        {
            "local_path": client.copy_calls[0]["local_path"],
            "instance_name": "smoke-001",
            "remote_path": client.copy_calls[0]["remote_path"],
        }
    ]
    assert client.copy_calls[0]["local_path"].endswith(".tar.gz")
    assert client.copy_calls[0]["remote_path"].startswith("/tmp/brev-control-plane-job-")
    assert client.copy_calls[0]["remote_path"].endswith(".tar.gz")
    assert client.exec_calls == [
        {
            "name": "smoke-001",
            "command": client.exec_calls[0]["command"],
            "host": True,
        }
    ]
    remote_archive = client.copy_calls[0]["remote_path"]
    remote_dir = remote_archive.removesuffix(".tar.gz")
    assert f"tar -xzf {remote_archive}" in client.exec_calls[0]["command"]
    assert f"cd {remote_dir}" in client.exec_calls[0]["command"]
    assert "python3 -m pytest -q" in client.exec_calls[0]["command"]
    payload = json.loads(stdout.getvalue())
    assert payload["instances"] == ["smoke-001"]
    assert payload["results"] == [
        {"instance": "smoke-001", "ok": True, "output": "1 passed"}
    ]


def test_cli_jobs_run_rejects_artifacts_until_collection_is_implemented(tmp_path):
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps({"command": "echo hi", "artifacts": ["reports/"]}),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()
    client = FakeBrevClient()
    client.instances = [{"id": "inst-1", "name": "smoke-001", "status": "RUNNING"}]

    code = main(
        ["jobs", "run", str(job_path), "--name-prefix", "smoke"],
        stdout=stdout,
        stderr=stderr,
        client=client,
    )

    assert code == 2
    assert client.exec_calls == []
    assert "artifact collection is not implemented" in json.loads(stderr.getvalue())["error"]


def test_cli_jobs_run_wraps_command_with_timeout_when_runtime_limit_is_set(tmp_path):
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps({"command": "echo hi", "max_runtime_seconds": 30}),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [{"id": "inst-1", "name": "smoke-001", "status": "RUNNING"}]

    code = main(
        ["jobs", "run", str(job_path), "--name-prefix", "smoke"],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    assert client.exec_calls == [
        {"name": "smoke-001", "command": "timeout 30 bash -lc 'echo hi'", "host": False}
    ]


def test_cli_jobs_run_can_execute_under_docker_group(tmp_path):
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps({"command": "docker ps && python run.py", "max_runtime_seconds": 30}),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [{"id": "inst-1", "name": "smoke-001", "status": "RUNNING"}]

    code = main(
        [
            "jobs",
            "run",
            str(job_path),
            "--name-prefix",
            "smoke",
            "--docker-group",
        ],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    assert len(client.exec_calls) == 1
    command = client.exec_calls[0]["command"]
    assert command.startswith("timeout 30 sg docker -c ")
    assert "bash -lc" in command
    assert "docker ps && python run.py" in command


def test_cli_jobs_run_retries_transient_bundle_copy_failures(tmp_path):
    source = tmp_path / "example-project"
    source.mkdir()
    (source / "test_example.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps(
            {
                "command": "python3 -m pytest -q",
                "bundle": {"source": str(source)},
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [{"id": "inst-1", "name": "smoke-001", "status": "RUNNING"}]
    copy_attempts = 0

    def flaky_copy(local_path, instance_name, remote_path):
        nonlocal copy_attempts
        copy_attempts += 1
        client.copy_calls.append(
            {
                "local_path": str(local_path),
                "instance_name": instance_name,
                "remote_path": remote_path,
            }
        )
        if copy_attempts == 1:
            raise BrevCommandError("rpc error: code = Unauthenticated")
        return f"copied {instance_name}"

    client.copy_to_instance = flaky_copy

    code = main(
        [
            "jobs",
            "run",
            str(job_path),
            "--name-prefix",
            "smoke",
            "--copy-attempts",
            "2",
            "--copy-retry-delay-seconds",
            "0",
        ],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    assert copy_attempts == 2
    assert len(client.exec_calls) == 1
    assert json.loads(stdout.getvalue())["results"] == [
        {
            "instance": "smoke-001",
            "ok": True,
            "output": client.exec_calls[0]["command"].join(["ran ", " on smoke-001"]),
        }
    ]


def test_cli_jobs_run_limits_parallel_instance_execution(tmp_path):
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps({"command": "echo hi"}), encoding="utf-8")
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [
        {"id": "inst-1", "name": "smoke-001", "status": "RUNNING"},
        {"id": "inst-2", "name": "smoke-002", "status": "RUNNING"},
        {"id": "inst-3", "name": "smoke-003", "status": "RUNNING"},
    ]
    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0

    def exec_instance(name, command, *, host=False):
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.03)
        with lock:
            in_flight -= 1
        return f"ran on {name}"

    client.exec_instance = exec_instance

    code = main(
        [
            "jobs",
            "run",
            str(job_path),
            "--name-prefix",
            "smoke",
            "--concurrency",
            "2",
        ],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    assert max_in_flight == 2
    payload = json.loads(stdout.getvalue())
    assert [result["instance"] for result in payload["results"]] == [
        "smoke-001",
        "smoke-002",
        "smoke-003",
    ]
