# Live Control Hardening Implementation Plan

> **For implementers:** Follow this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden `brev-control-plane` after the first live two-machine smoke so fleet creation, checks, command execution, job bundles, audit logging, and teardown are safer and easier to operate.

**Architecture:** Keep the project as a generic public Brev fleet and shell-job controller. Add small focused modules for org parsing, per-instance command execution, machine capability checks, and bundle jobs; keep `cli.py` as a thin command router and persist live operation events through the existing SQLite `StateStore`.

**Tech Stack:** Python 3.10+ standard library, `argparse`, `sqlite3`, `subprocess`, JSON output, pytest.

---

## Scope And Generic Content Constraints

This plan must not add examples, tests, fixtures, comments, or docs with confidential project, customer, organization-specific, or sensitive workload details. All new CLI surfaces must be generic: shell commands, tarball/directory bundles, artifacts, machine checks, and Brev fleet lifecycle.

## File Structure

- Modify `src/brev_control_plane/brev.py`: add org parsing, single-instance exec, copy support, and structured command output helpers.
- Modify `src/brev_control_plane/cli.py`: add `--require-org`, `fleet check`, `fleet down --wait`, richer `fleet exec`, and `jobs run`.
- Modify `src/brev_control_plane/state.py`: add reusable event/audit helpers for live operations.
- Create `src/brev_control_plane/checks.py`: build and parse generic machine capability checks.
- Create `src/brev_control_plane/bundles.py`: validate and package generic source directories or tarballs for remote jobs.
- Modify `src/brev_control_plane/jobs.py`: extend generic job spec validation for bundle-based jobs.
- Modify `README.md`: document generic live workflow with safety gates.
- Modify tests: `tests/test_brev_client.py`, `tests/test_cli.py`, `tests/test_state.py`, `tests/test_jobs.py`, plus new `tests/test_checks.py` and `tests/test_bundles.py`.

---

### Task 1: Add Brev Org Guardrails

**Files:**
- Modify: `src/brev_control_plane/brev.py`
- Modify: `src/brev_control_plane/cli.py`
- Test: `tests/test_brev_client.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for active org parsing**

Add to `tests/test_brev_client.py`:

```python
def test_active_org_parses_starred_brev_org_ls_output():
    output = """Your organizations:
 NAME                       ID
 personal                   org-1
 * Example-Org  org-2

Switch orgs:
\tbrev org set <NAME>
"""

    client = BrevClient(binary="brev", runner=lambda argv: Result(stdout=output))

    assert client.active_org() == "Example-Org"
```

Add a non-starred failure case:

```python
def test_active_org_raises_when_no_starred_org_is_present():
    client = BrevClient(
        binary="brev",
        runner=lambda argv: Result(stdout="Your organizations:\n NAME ID\n"),
    )

    with pytest.raises(BrevCommandError, match="active Brev org"):
        client.active_org()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_brev_client.py::test_active_org_parses_starred_brev_org_ls_output tests/test_brev_client.py::test_active_org_raises_when_no_starred_org_is_present -q
```

Expected: fail with `AttributeError: 'BrevClient' object has no attribute 'active_org'`.

- [ ] **Step 3: Implement active org parsing**

Add to `src/brev_control_plane/brev.py`:

```python
    def active_org(self) -> str:
        result = self._run(["org", "ls"])
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line.startswith("* "):
                continue
            parts = line[2:].split()
            if not parts:
                break
            return parts[0]
        raise BrevCommandError("could not determine active Brev org from 'brev org ls'")
```

- [ ] **Step 4: Add CLI guard tests**

Extend `FakeBrevClient` in `tests/test_cli.py`:

```python
        self.org = "personal"
```

and add:

```python
    def active_org(self):
        return self.org
```

Add tests:

```python
def test_cli_fleet_apply_refuses_wrong_required_org():
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
            "Example-Org",
            "--yes",
        ],
        stdout=stdout,
        stderr=stderr,
        client=client,
    )

    assert code == 2
    assert client.created == []
    assert "active Brev org is 'personal'" in json.loads(stderr.getvalue())["error"]
```

```python
def test_cli_fleet_apply_allows_matching_required_org():
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.org = "Example-Org"

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
            "Example-Org",
            "--yes",
        ],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    assert client.created[0]["name"] == "smoke-001"
```

- [ ] **Step 5: Run CLI guard tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_cli.py::test_cli_fleet_apply_refuses_wrong_required_org tests/test_cli.py::test_cli_fleet_apply_allows_matching_required_org -q
```

Expected: fail because `--require-org` is not defined.

- [ ] **Step 6: Implement `--require-org`**

In `src/brev_control_plane/cli.py`, add `--require-org` to `fleet apply`, `fleet exec`, `fleet check`, `fleet down`, and `jobs run` as those commands are introduced:

```python
fleet_apply.add_argument("--require-org")
```

Add helper:

```python
def _require_active_org(client: BrevClient, required_org: str | None) -> None:
    if not required_org:
        return
    active = client.active_org()
    if active != required_org:
        raise PlanError(
            f"active Brev org is {active!r}, expected {required_org!r}; "
            "run 'brev org set <name>' before creating or deleting instances"
        )
```

Call it in `_fleet_apply` before creating:

```python
    brev_client = client or BrevClient()
    _require_active_org(brev_client, args.require_org)
```

- [ ] **Step 7: Verify and commit**

Run:

```bash
python3 -m pytest tests/test_brev_client.py tests/test_cli.py -q
python3 -m pytest -q
```

Expected: all tests pass.

Commit:

```bash
git add src/brev_control_plane/brev.py src/brev_control_plane/cli.py tests/test_brev_client.py tests/test_cli.py
git commit -m "Add active Brev org guardrails"
```

---

### Task 2: Split Fleet Exec Into Per-Instance Results

**Files:**
- Modify: `src/brev_control_plane/brev.py`
- Modify: `src/brev_control_plane/cli.py`
- Test: `tests/test_brev_client.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing adapter tests for single-instance exec**

Add to `tests/test_brev_client.py`:

```python
def test_exec_instance_runs_brev_exec_for_one_name():
    calls = []

    def runner(argv):
        calls.append(argv)
        return Result(stdout="ok")

    client = BrevClient(binary="brev", runner=runner)

    assert client.exec_instance("smoke-001", "echo hello", host=True) == "ok"
    assert calls == [["brev", "exec", "smoke-001", "--host", "echo hello"]]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_brev_client.py::test_exec_instance_runs_brev_exec_for_one_name -q
```

Expected: fail with `AttributeError`.

- [ ] **Step 3: Implement `exec_instance`**

Add to `src/brev_control_plane/brev.py`:

```python
    def exec_instance(self, name: str, command: str, *, host: bool = False) -> str:
        args = ["exec", name]
        if host:
            args.append("--host")
        args.append(command)
        result = self._run(args)
        return result.stdout.strip()
```

- [ ] **Step 4: Write failing CLI test for per-instance JSON**

Update `FakeBrevClient` in `tests/test_cli.py`:

```python
    def exec_instance(self, name, command, *, host=False):
        self.exec_calls.append({"name": name, "command": command, "host": host})
        return f"output from {name}"
```

Add:

```python
def test_cli_fleet_exec_outputs_per_instance_results():
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [
        {"id": "inst-1", "name": "smoke-001", "status": "RUNNING"},
        {"id": "inst-2", "name": "smoke-002", "status": "RUNNING"},
    ]

    code = main(
        ["fleet", "exec", "--name-prefix", "smoke", "--", "echo", "hello"],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["results"] == [
        {"instance": "smoke-001", "ok": True, "output": "output from smoke-001"},
        {"instance": "smoke-002", "ok": True, "output": "output from smoke-002"},
    ]
```

- [ ] **Step 5: Run CLI test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_cli.py::test_cli_fleet_exec_outputs_per_instance_results -q
```

Expected: fail because `_fleet_exec` emits aggregate `"output"` instead of `"results"`.

- [ ] **Step 6: Implement per-instance execution**

In `_fleet_exec`, replace aggregate `exec_instances` call:

```python
    results = []
    for name in names:
        try:
            output = brev_client.exec_instance(name, command, host=args.host)
            results.append({"instance": name, "ok": True, "output": output})
        except BrevCommandError as exc:
            results.append({"instance": name, "ok": False, "error": str(exc)})
    exit_code = 0 if all(result["ok"] for result in results) else 2
    _write_json(stdout if exit_code == 0 else stdout, {"instances": names, "results": results})
    return exit_code
```

Keep `exec_instances` in `BrevClient` for backward compatibility, but make the CLI use `exec_instance`.

- [ ] **Step 7: Verify and commit**

Run:

```bash
python3 -m pytest tests/test_brev_client.py tests/test_cli.py -q
python3 -m pytest -q
```

Expected: all tests pass.

Commit:

```bash
git add src/brev_control_plane/brev.py src/brev_control_plane/cli.py tests/test_brev_client.py tests/test_cli.py
git commit -m "Report fleet exec results per instance"
```

---

### Task 3: Add `fleet check` Capability Reports

**Files:**
- Create: `src/brev_control_plane/checks.py`
- Modify: `src/brev_control_plane/cli.py`
- Test: `tests/test_checks.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing check parser tests**

Create `tests/test_checks.py`:

```python
from brev_control_plane.checks import build_check_command, parse_check_output


def test_parse_check_output_extracts_machine_capabilities():
    output = """INSTANCE=worker-001
EGRESS=203.0.113.10
UNAME=Linux 6.8.0 x86_64
USER=ubuntu
DOCKER_PATH=/usr/bin/docker
DOCKER_DIRECT=permission denied
DOCKER_SUDO=Docker version 29.5.3
PYTHON3=Python 3.10.12
DISK_ROOT=/dev/root 125G 4G 121G 4% /
"""

    assert parse_check_output(output) == {
        "instance": "worker-001",
        "egress_ip": "203.0.113.10",
        "uname": "Linux 6.8.0 x86_64",
        "user": "ubuntu",
        "docker_path": "/usr/bin/docker",
        "docker_access": "sudo",
        "docker_version": "Docker version 29.5.3",
        "python3": "Python 3.10.12",
        "disk_root": "/dev/root 125G 4G 121G 4% /",
    }


def test_build_check_command_is_generic_shell_probe():
    command = build_check_command()

    assert "https://ifconfig.me" in command
    assert "timeout 10 sudo -n docker --version" in command
    assert "python3 --version" in command
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_checks.py -q
```

Expected: fail with import error for `brev_control_plane.checks`.

- [ ] **Step 3: Implement `checks.py`**

Create `src/brev_control_plane/checks.py`:

```python
from __future__ import annotations


def build_check_command() -> str:
    return (
        "bash -lc '"
        "set +e; "
        "echo INSTANCE=$(hostname); "
        "echo EGRESS=$(curl -fsS https://ifconfig.me 2>/dev/null || true); "
        "echo UNAME=$(uname -srm); "
        "echo USER=$(whoami); "
        "echo DOCKER_PATH=$(command -v docker || true); "
        "echo DOCKER_DIRECT=$(timeout 10 docker --version 2>&1 || true); "
        "echo DOCKER_SUDO=$(timeout 10 sudo -n docker --version 2>&1 || true); "
        "echo PYTHON3=$(python3 --version 2>&1 || true); "
        "echo DISK_ROOT=$(df -h / | tail -1)"
        "'"
    )


def parse_check_output(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    direct = values.get("DOCKER_DIRECT", "")
    sudo = values.get("DOCKER_SUDO", "")
    docker_access = "missing"
    docker_version = ""
    if direct.startswith("Docker version"):
        docker_access = "direct"
        docker_version = direct
    elif sudo.startswith("Docker version"):
        docker_access = "sudo"
        docker_version = sudo

    return {
        "instance": values.get("INSTANCE", ""),
        "egress_ip": values.get("EGRESS", ""),
        "uname": values.get("UNAME", ""),
        "user": values.get("USER", ""),
        "docker_path": values.get("DOCKER_PATH", ""),
        "docker_access": docker_access,
        "docker_version": docker_version,
        "python3": values.get("PYTHON3", ""),
        "disk_root": values.get("DISK_ROOT", ""),
    }
```

- [ ] **Step 4: Write failing CLI test for `fleet check`**

Add to `tests/test_cli.py`:

```python
def test_cli_fleet_check_returns_capability_reports():
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [{"id": "inst-1", "name": "smoke-001", "status": "RUNNING"}]

    def exec_instance(name, command, *, host=False):
        client.exec_calls.append({"name": name, "command": command, "host": host})
        return """INSTANCE=smoke-001
EGRESS=203.0.113.10
UNAME=Linux x86_64
USER=ubuntu
DOCKER_PATH=/usr/bin/docker
DOCKER_DIRECT=permission denied
DOCKER_SUDO=Docker version 29.5.3
PYTHON3=Python 3.10.12
DISK_ROOT=/dev/root 125G 4G 121G 4% /
"""

    client.exec_instance = exec_instance

    code = main(["fleet", "check", "--name-prefix", "smoke"], stdout=stdout, client=client)

    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["checks"][0]["egress_ip"] == "203.0.113.10"
    assert payload["checks"][0]["docker_access"] == "sudo"
```

- [ ] **Step 5: Run CLI test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_cli.py::test_cli_fleet_check_returns_capability_reports -q
```

Expected: fail because `fleet check` is not defined.

- [ ] **Step 6: Implement `fleet check`**

In `cli.py`, import:

```python
from .checks import build_check_command, parse_check_output
```

Add parser:

```python
fleet_check = fleet_subparsers.add_parser(
    "check",
    help="Probe fleet machine capabilities.",
)
fleet_check.add_argument("--name-prefix", required=True)
fleet_check.add_argument("--require-org")
```

Add dispatch:

```python
        if args.command == "fleet" and args.fleet_command == "check":
            return _fleet_check(args, stdout, client)
```

Add implementation:

```python
def _fleet_check(args: argparse.Namespace, stdout: TextIO, client: BrevClient | None) -> int:
    brev_client = client or BrevClient()
    _require_active_org(brev_client, args.require_org)
    names = _matching_instance_names(brev_client, args.name_prefix)
    checks = []
    for name in names:
        output = brev_client.exec_instance(name, build_check_command(), host=True)
        report = parse_check_output(output)
        report["name"] = name
        checks.append(report)
    _write_json(stdout, {"instances": names, "checks": checks})
    return 0
```

- [ ] **Step 7: Verify and commit**

Run:

```bash
python3 -m pytest tests/test_checks.py tests/test_cli.py -q
python3 -m pytest -q
```

Expected: all tests pass.

Commit:

```bash
git add src/brev_control_plane/checks.py src/brev_control_plane/cli.py tests/test_checks.py tests/test_cli.py
git commit -m "Add fleet capability checks"
```

---

### Task 4: Make `fleet down` Wait For Cleanup

**Files:**
- Modify: `src/brev_control_plane/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Extend fake client with changing inventory**

In `tests/test_cli.py`, add to `FakeBrevClient.__init__`:

```python
        self.list_results = None
```

Update `list_instances`:

```python
    def list_instances(self):
        self.refreshed = True
        if self.list_results:
            return self.list_results.pop(0)
        return self.instances
```

- [ ] **Step 2: Write failing wait test**

Add:

```python
def test_cli_fleet_down_waits_until_matching_instances_are_absent():
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [
        {"id": "inst-1", "name": "smoke-001", "status": "RUNNING"},
    ]
    client.list_results = [
        [{"id": "inst-1", "name": "smoke-001", "status": "RUNNING"}],
        [{"id": "inst-1", "name": "smoke-001", "status": "DELETING"}],
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
            "5",
            "--poll-seconds",
            "0",
        ],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["deleted"] == ["smoke-001"]
    assert payload["remaining"] == []
```

- [ ] **Step 3: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_cli.py::test_cli_fleet_down_waits_until_matching_instances_are_absent -q
```

Expected: fail because `--timeout-seconds` and `--poll-seconds` are not accepted by `fleet down`.

- [ ] **Step 4: Implement wait flags and polling**

In parser:

```python
fleet_down.add_argument("--timeout-seconds", type=float, default=900.0)
fleet_down.add_argument("--poll-seconds", type=float, default=10.0)
fleet_down.add_argument("--no-wait", action="store_true")
```

Add import:

```python
import time
```

Add helper:

```python
def _wait_for_prefix_absent(
    client: BrevClient,
    name_prefix: str,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> list[str]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = _matching_instance_names_or_empty(client, name_prefix)
        if not remaining:
            return []
        if time.monotonic() >= deadline:
            return remaining
        time.sleep(max(0.0, poll_seconds))
```

Split `_matching_instance_names` so wait can return an empty list:

```python
def _matching_instance_names_or_empty(client: BrevClient, name_prefix: str) -> list[str]:
    prefix = name_prefix.strip()
    if len(prefix) < 3:
        raise PlanError("name_prefix must be at least 3 characters")
    return sorted(
        str(instance.get("name", ""))
        for instance in client.list_instances()
        if str(instance.get("name", "")).startswith(f"{prefix}-")
    )
```

Update `_matching_instance_names` to call the new helper and error on empty.

Update `_fleet_down`:

```python
    remaining = names if args.no_wait else _wait_for_prefix_absent(
        brev_client,
        args.name_prefix,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
    )
    _write_json(stdout, {"deleted": names, "remaining": remaining, "outputs": outputs})
    return 0 if not remaining else 2
```

- [ ] **Step 5: Verify and commit**

Run:

```bash
python3 -m pytest tests/test_cli.py -q
python3 -m pytest -q
```

Expected: all tests pass.

Commit:

```bash
git add src/brev_control_plane/cli.py tests/test_cli.py
git commit -m "Wait for fleet teardown completion"
```

---

### Task 5: Persist Live Operation Audit Events

**Files:**
- Modify: `src/brev_control_plane/state.py`
- Modify: `src/brev_control_plane/cli.py`
- Test: `tests/test_state.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing state helper test**

Add to `tests/test_state.py`:

```python
def test_record_live_event_includes_operation_and_instance(tmp_path):
    store = StateStore(tmp_path / "state.db")

    event_id = store.record_live_event(
        "fleet.apply.created",
        instance_name="smoke-001",
        payload={"instance_type": "n2d-highcpu-2"},
    )

    events = store.list_events()
    assert event_id == 1
    assert events[0]["event_type"] == "fleet.apply.created"
    assert events[0]["payload"] == {
        "instance_name": "smoke-001",
        "instance_type": "n2d-highcpu-2",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_state.py::test_record_live_event_includes_operation_and_instance -q
```

Expected: fail with `AttributeError`.

- [ ] **Step 3: Implement state helper**

Add to `StateStore`:

```python
    def record_live_event(
        self,
        event_type: str,
        *,
        instance_name: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        event_payload = {"instance_name": instance_name}
        if payload:
            event_payload.update(payload)
        return self.record_event(event_type, event_payload)
```

- [ ] **Step 4: Add CLI `--db` tests for live event recording**

Add to `tests/test_cli.py`:

```python
from brev_control_plane.state import StateStore
```

Add:

```python
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
    assert events[0]["event_type"] == "fleet.apply.created"
    assert events[0]["payload"]["instance_name"] == "smoke-001"
```

Add equivalent tests for `fleet exec`, `fleet check`, `jobs run`, and `fleet down` as those commands exist:

```python
def test_cli_fleet_down_records_deleted_events(tmp_path):
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [{"id": "inst-1", "name": "smoke-001", "status": "RUNNING"}]
    client.list_results = [[]]
    db_path = tmp_path / "state.db"

    code = main(
        [
            "fleet",
            "down",
            "--name-prefix",
            "smoke",
            "--db",
            str(db_path),
            "--poll-seconds",
            "0",
            "--yes",
        ],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    events = StateStore(db_path).list_events()
    assert events[0]["event_type"] == "fleet.down.deleted"
    assert events[0]["payload"]["instance_name"] == "smoke-001"
```

- [ ] **Step 5: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_cli.py::test_cli_fleet_apply_records_created_events tests/test_cli.py::test_cli_fleet_down_records_deleted_events -q
```

Expected: fail because `--db` is not supported on live fleet commands.

- [ ] **Step 6: Implement `--db` on live commands**

Add helper:

```python
def _state_store(path: str | None) -> StateStore | None:
    if not path:
        return None
    store = StateStore(Path(path))
    store.initialize()
    return store
```

Add parser args to `fleet apply`, `fleet exec`, `fleet check`, `fleet down`, and `jobs run`:

```python
fleet_apply.add_argument("--db")
```

Record events:

```python
    store = _state_store(args.db)
    ...
        if store:
            store.record_live_event(
                "fleet.apply.created",
                instance_name=name,
                payload={"instance_type": args.instance_type},
            )
```

Use event types:

- `fleet.apply.created`
- `fleet.exec.completed`
- `fleet.exec.failed`
- `fleet.check.completed`
- `fleet.down.deleted`
- `fleet.down.wait_timeout`
- `jobs.run.completed`
- `jobs.run.failed`

- [ ] **Step 7: Verify and commit**

Run:

```bash
python3 -m pytest tests/test_state.py tests/test_cli.py -q
python3 -m pytest -q
```

Expected: all tests pass.

Commit:

```bash
git add src/brev_control_plane/state.py src/brev_control_plane/cli.py tests/test_state.py tests/test_cli.py
git commit -m "Record live fleet operation events"
```

---

### Task 6: Add Generic Job Bundle Support

**Files:**
- Create: `src/brev_control_plane/bundles.py`
- Modify: `src/brev_control_plane/brev.py`
- Modify: `src/brev_control_plane/jobs.py`
- Modify: `src/brev_control_plane/cli.py`
- Test: `tests/test_bundles.py`
- Test: `tests/test_brev_client.py`
- Test: `tests/test_jobs.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing bundle packaging tests**

Create `tests/test_bundles.py`:

```python
import tarfile

from brev_control_plane.bundles import create_bundle_archive


def test_create_bundle_archive_excludes_named_directories(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("print('hi')\n", encoding="utf-8")
    excluded = source / "runs"
    excluded.mkdir()
    (excluded / "old.txt").write_text("old\n", encoding="utf-8")

    archive = create_bundle_archive(
        source,
        tmp_path / "bundle.tgz",
        exclude_names={"runs"},
    )

    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    assert "app.py" in names
    assert "runs/old.txt" not in names
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_bundles.py -q
```

Expected: fail with import error.

- [ ] **Step 3: Implement `bundles.py`**

Create `src/brev_control_plane/bundles.py`:

```python
from __future__ import annotations

from pathlib import Path
import tarfile


class BundleError(ValueError):
    """Raised when a job bundle cannot be created."""


def create_bundle_archive(
    source_dir: str | Path,
    output_path: str | Path,
    *,
    exclude_names: set[str] | None = None,
) -> Path:
    source = Path(source_dir)
    output = Path(output_path)
    if not source.is_dir():
        raise BundleError(f"bundle source is not a directory: {source}")
    exclude_names = exclude_names or set()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(output, "w:gz") as tar:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if any(part in exclude_names for part in relative.parts):
                continue
            tar.add(path, arcname=str(relative), recursive=False)
    return output
```

- [ ] **Step 4: Write failing Brev copy test**

Add to `tests/test_brev_client.py`:

```python
def test_copy_to_instance_runs_brev_copy():
    calls = []

    def runner(argv):
        calls.append(argv)
        return Result(stdout="copied")

    client = BrevClient(binary="brev", runner=runner)

    assert client.copy_to_instance("/tmp/bundle.tgz", "smoke-001", "/tmp/bundle.tgz") == "copied"
    assert calls == [["brev", "copy", "/tmp/bundle.tgz", "smoke-001:/tmp/bundle.tgz"]]
```

- [ ] **Step 5: Implement `copy_to_instance`**

Add to `src/brev_control_plane/brev.py`:

```python
    def copy_to_instance(self, local_path: str, instance_name: str, remote_path: str) -> str:
        result = self._run(["copy", local_path, f"{instance_name}:{remote_path}"])
        return result.stdout.strip()
```

- [ ] **Step 6: Extend generic job spec tests**

In `tests/test_jobs.py`, add:

```python
def test_job_spec_accepts_bundle_and_artifacts(tmp_path):
    path = tmp_path / "job.json"
    path.write_text(
        json.dumps(
            {
                "command": "bash -lc 'echo ok'",
                "bundle": {"source": "./example", "exclude": ["runs", ".venv"]},
                "artifacts": ["out/", "logs/"],
                "max_runtime_seconds": 60,
            }
        ),
        encoding="utf-8",
    )

    spec = load_job_spec(path)

    assert spec.command == "bash -lc 'echo ok'"
    assert spec.bundle == {"source": "./example", "exclude": ["runs", ".venv"]}
```

- [ ] **Step 7: Implement job spec extension**

Update `src/brev_control_plane/jobs.py` dataclass to include:

```python
    bundle: dict[str, object] | None = None
```

When parsing, validate:

```python
    bundle = payload.get("bundle")
    if bundle is not None and not isinstance(bundle, dict):
        raise JobSpecError("bundle must be an object when provided")
```

Return it in `JobSpec`.

- [ ] **Step 8: Write failing CLI test for `jobs run`**

Add to `tests/test_cli.py`:

```python
def test_cli_jobs_run_copies_bundle_and_executes_command(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.txt").write_text("hello\n", encoding="utf-8")
    spec_path = tmp_path / "job.json"
    spec_path.write_text(
        json.dumps(
            {
                "command": "bash -lc 'cat app.txt'",
                "bundle": {"source": str(source), "exclude": []},
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    client = FakeBrevClient()
    client.instances = [{"id": "inst-1", "name": "smoke-001", "status": "RUNNING"}]
    client.copied = []

    def copy_to_instance(local_path, instance_name, remote_path):
        client.copied.append(
            {
                "local_path": local_path,
                "instance_name": instance_name,
                "remote_path": remote_path,
            }
        )
        return "copied"

    client.copy_to_instance = copy_to_instance
    client.exec_instance = lambda name, command, host=False: "job ok"

    code = main(
        ["jobs", "run", str(spec_path), "--name-prefix", "smoke"],
        stdout=stdout,
        client=client,
    )

    assert code == 0
    assert client.copied[0]["instance_name"] == "smoke-001"
    payload = json.loads(stdout.getvalue())
    assert payload["results"][0]["output"] == "job ok"
```

- [ ] **Step 9: Implement `jobs run`**

In parser:

```python
run = jobs_subparsers.add_parser("run", help="Run a generic shell-job spec on a fleet.")
run.add_argument("path")
run.add_argument("--name-prefix", required=True)
run.add_argument("--require-org")
run.add_argument("--db")
run.add_argument("--host", action="store_true")
```

In dispatch:

```python
        if args.command == "jobs" and args.jobs_command == "run":
            return _jobs_run(args, stdout, client)
```

Implementation outline:

```python
def _jobs_run(args: argparse.Namespace, stdout: TextIO, client: BrevClient | None) -> int:
    spec = load_job_spec(args.path)
    brev_client = client or BrevClient()
    _require_active_org(brev_client, args.require_org)
    names = _matching_instance_names(brev_client, args.name_prefix)
    archive_path = None
    if spec.bundle:
        source = str(spec.bundle["source"])
        exclude = set(spec.bundle.get("exclude", []))
        archive_path = create_bundle_archive(source, Path(tempfile.mkdtemp()) / "job-bundle.tgz", exclude_names=exclude)
    results = []
    for name in names:
        if archive_path:
            remote_dir = "/tmp/brev-control-plane-job-<run-id>"
            remote_archive = f"{remote_dir}.tgz"
            brev_client.copy_to_instance(str(archive_path), name, remote_archive)
            setup = f"rm -rf {remote_dir} && mkdir -p {remote_dir} && tar -xzf {remote_archive} -C {remote_dir}"
            command = f"bash -lc {shlex.quote(setup + ' && cd ' + remote_dir + ' && ' + spec.command)}"
        else:
            command = spec.command
        output = brev_client.exec_instance(name, command, host=args.host)
        results.append({"instance": name, "ok": True, "output": output})
    _write_json(stdout, {"instances": names, "results": results})
    return 0
```

Add imports:

```python
import tempfile
from .bundles import create_bundle_archive
```

- [ ] **Step 10: Verify and commit**

Run:

```bash
python3 -m pytest tests/test_bundles.py tests/test_brev_client.py tests/test_jobs.py tests/test_cli.py -q
python3 -m pytest -q
```

Expected: all tests pass.

Commit:

```bash
git add src/brev_control_plane/bundles.py src/brev_control_plane/brev.py src/brev_control_plane/jobs.py src/brev_control_plane/cli.py tests/test_bundles.py tests/test_brev_client.py tests/test_jobs.py tests/test_cli.py
git commit -m "Add generic bundle job runner"
```

---

### Task 7: Update README And Verify

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README live workflow**

Add sections:

```markdown
## Live Fleet Workflow

Use `--require-org` on commands that create, check, run, or delete remote
instances when you want a hard guard against running in the wrong Brev
organization.

```bash
brev-control-plane fleet apply \
  --workers 2 \
  --type n2d-highcpu-2 \
  --name-prefix smoke \
  --require-org "$BREV_REQUIRED_ORG" \
  --yes
```

```bash
brev-control-plane fleet check --name-prefix smoke
```

```bash
brev-control-plane jobs run ./job.json --name-prefix smoke
```

```bash
brev-control-plane fleet down \
  --name-prefix smoke \
  --timeout-seconds 900 \
  --yes
```
```

Add generic `job.json` example:

```json
{
  "command": "bash -lc 'python3 -m pytest -q'",
  "bundle": {
    "source": "./example-project",
    "exclude": [".git", ".venv", "runs", "dist"]
  },
  "artifacts": ["reports/", "logs/"],
  "max_runtime_seconds": 3600
}
```

- [ ] **Step 2: Review generic wording**

Confirm README examples and command descriptions stay generic and do not include
confidential project, customer, organization-specific, or sensitive workload
details.

- [ ] **Step 3: Run tests**

Run:

```bash
python3 -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Document hardened live fleet workflow"
```

---

### Task 8: Final Verification

**Files:**
- No code changes unless verification reveals a defect.

- [ ] **Step 1: Full test suite**

Run:

```bash
python3 -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: CLI safety smoke**

Run:

```bash
PYTHONPATH=src python3 -m brev_control_plane fleet apply \
  --workers 1 \
  --type n2d-highcpu-2 \
  --name-prefix smoke
```

Expected: exit code `2` and JSON error containing `requires --yes`.

- [ ] **Step 3: CLI plan smoke**

Run:

```bash
PYTHONPATH=src python3 -m brev_control_plane fleet plan \
  --workers 2 \
  --cpu-min-vcpus 2 \
  --name-prefix smoke
```

Expected: JSON with workers `smoke-001` and `smoke-002`, and `creates_instances: false`.

- [ ] **Step 4: Git status**

Run:

```bash
git status --short --branch
```

Expected: clean branch ahead of or synced with `origin/main`, depending on whether commits have been pushed.

---

## Live Follow-Up After Implementation

After these tasks are merged, repeat a two-machine live smoke:

```bash
brev org set "$BREV_REQUIRED_ORG"
PYTHONPATH=src python3 -m brev_control_plane fleet apply \
  --workers 2 \
  --type n2d-highcpu-2 \
  --name-prefix bcp-smoke \
  --require-org "$BREV_REQUIRED_ORG" \
  --timeout-seconds 900 \
  --yes
PYTHONPATH=src python3 -m brev_control_plane fleet check \
  --name-prefix bcp-smoke \
  --require-org "$BREV_REQUIRED_ORG"
PYTHONPATH=src python3 -m brev_control_plane jobs run ./job.json \
  --name-prefix bcp-smoke \
  --require-org "$BREV_REQUIRED_ORG" \
  --db /tmp/brev-control-plane-live.sqlite3
PYTHONPATH=src python3 -m brev_control_plane fleet down \
  --name-prefix bcp-smoke \
  --require-org "$BREV_REQUIRED_ORG" \
  --timeout-seconds 900 \
  --yes
```

Expected result: `fleet check` reports distinct egress IPs and Docker access mode per instance; `jobs run` reports per-instance job output; `fleet down` waits until no `bcp-smoke-*` instances remain.
