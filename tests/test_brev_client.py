import json

import pytest

from brev_control_plane.brev import BrevClient, BrevCommandError


class Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_list_instances_parses_brev_ls_json():
    calls = []

    def runner(argv):
        calls.append(argv)
        return Result(stdout=json.dumps([{"id": "inst-1", "name": "worker"}]))

    client = BrevClient(binary="brev", runner=runner)

    assert client.list_instances() == [{"id": "inst-1", "name": "worker"}]
    assert calls == [["brev", "ls", "--json"]]


def test_list_instances_parses_current_brev_workspace_envelope():
    def runner(argv):
        assert argv == ["brev", "ls", "--json"]
        return Result(stdout=json.dumps({"workspaces": [{"id": "inst-1", "name": "worker"}]}))

    client = BrevClient(binary="brev", runner=runner)

    assert client.list_instances() == [{"id": "inst-1", "name": "worker"}]


def test_search_cpu_parses_brev_search_cpu_json():
    def runner(argv):
        assert argv == ["brev", "search", "cpu", "--json"]
        return Result(stdout=json.dumps([{"type": "cpu-small", "vcpus": 4}]))

    client = BrevClient(binary="brev", runner=runner)

    assert client.search_cpu() == [{"type": "cpu-small", "vcpus": 4}]


def test_create_instance_runs_brev_create_with_type_and_timeout():
    calls = []

    def runner(argv):
        calls.append(argv)
        return Result(stdout="created")

    client = BrevClient(binary="brev", runner=runner)

    assert (
        client.create_instance(
            name="smoke-001",
            instance_type="n2d-highcpu-2",
            timeout_seconds=900,
        )
        == "created"
    )
    assert calls == [
        [
            "brev",
            "create",
            "smoke-001",
            "--type",
            "n2d-highcpu-2",
            "--timeout",
            "900",
        ]
    ]


def test_delete_instance_runs_brev_delete():
    calls = []

    def runner(argv):
        calls.append(argv)
        return Result(stdout="deleted")

    client = BrevClient(binary="brev", runner=runner)

    assert client.delete_instance("smoke-001") == "deleted"
    assert calls == [["brev", "delete", "smoke-001"]]


def test_exec_instances_runs_brev_exec_for_names_and_command():
    calls = []

    def runner(argv):
        calls.append(argv)
        return Result(stdout="ran")

    client = BrevClient(binary="brev", runner=runner)

    assert (
        client.exec_instances(["smoke-001", "smoke-002"], "echo hello", host=True)
        == "ran"
    )
    assert calls == [
        ["brev", "exec", "smoke-001", "smoke-002", "--host", "echo hello"]
    ]


def test_exec_instance_runs_brev_exec_for_one_name_and_command():
    calls = []

    def runner(argv):
        calls.append(argv)
        return Result(stdout="ran\n")

    client = BrevClient(binary="brev", runner=runner)

    assert client.exec_instance("smoke-001", "echo hello", host=True) == "ran"
    assert calls == [["brev", "exec", "smoke-001", "--host", "echo hello"]]


def test_copy_to_instance_runs_brev_copy_to_remote_path(tmp_path):
    calls = []
    local_path = tmp_path / "bundle.tar.gz"
    local_path.write_text("data", encoding="utf-8")

    def runner(argv):
        calls.append(argv)
        return Result(stdout="copied\n")

    client = BrevClient(binary="brev", runner=runner)

    assert (
        client.copy_to_instance(local_path, "smoke-001", "/tmp/job.tar.gz")
        == "copied"
    )
    assert calls == [
        ["brev", "copy", str(local_path), "smoke-001:/tmp/job.tar.gz"]
    ]


def test_copy_from_instance_runs_brev_copy_from_remote_path(tmp_path):
    calls = []
    local_path = tmp_path / "artifacts"

    def runner(argv):
        calls.append(argv)
        return Result(stdout="copied\n")

    client = BrevClient(binary="brev", runner=runner)

    assert (
        client.copy_from_instance("smoke-001", "/tmp/job/output", local_path)
        == "copied"
    )
    assert calls == [
        ["brev", "copy", "smoke-001:/tmp/job/output", str(local_path)]
    ]


def test_active_org_parses_starred_brev_org_ls_output():
    calls = []

    def runner(argv):
        calls.append(argv)
        return Result(stdout="  personal\n* team-a\n  team-b\n")

    client = BrevClient(binary="brev", runner=runner)

    assert client.active_org() == "team-a"
    assert calls == [["brev", "org", "ls"]]


def test_active_org_raises_when_no_starred_org_is_present():
    client = BrevClient(
        binary="brev",
        runner=lambda argv: Result(stdout="  personal\n  team-a\n"),
    )

    with pytest.raises(
        BrevCommandError,
        match="could not determine active Brev org from 'brev org ls'",
    ):
        client.active_org()


def test_brev_client_raises_for_nonzero_exit():
    client = BrevClient(
        binary="brev",
        runner=lambda argv: Result(returncode=2, stderr="not authenticated"),
    )

    with pytest.raises(BrevCommandError, match="brev command failed"):
        client.list_instances()


def test_brev_client_raises_for_invalid_json():
    client = BrevClient(binary="brev", runner=lambda argv: Result(stdout="not json"))

    with pytest.raises(BrevCommandError, match="invalid JSON"):
        client.search_cpu()


def test_brev_client_raises_clean_error_when_binary_is_missing():
    client = BrevClient(binary="definitely-not-a-brev-binary")

    with pytest.raises(BrevCommandError, match="brev binary not found"):
        client.list_instances()
