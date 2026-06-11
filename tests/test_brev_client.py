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


def test_search_cpu_parses_brev_search_cpu_json():
    def runner(argv):
        assert argv == ["brev", "search", "cpu", "--json"]
        return Result(stdout=json.dumps([{"type": "cpu-small", "vcpus": 4}]))

    client = BrevClient(binary="brev", runner=runner)

    assert client.search_cpu() == [{"type": "cpu-small", "vcpus": 4}]


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
