from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import time
import uuid
from typing import Any, TextIO

from .brev import BrevClient, BrevCommandError
from .bundles import BundleError, create_bundle_archive
from .checks import build_check_command, parse_check_output
from .jobs import JobSpec, JobSpecError, load_job_spec
from .planner import CpuFilter, PlanError, plan_fleet
from .state import StateStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brev-control-plane",
        description="Plan and track Brev fleets for generic shell jobs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local CLI prerequisites.")
    doctor.add_argument("--brev-binary", default="brev")

    fleet = subparsers.add_parser("fleet", help="Plan fleet capacity.")
    fleet_subparsers = fleet.add_subparsers(dest="fleet_command", required=True)
    fleet_plan = fleet_subparsers.add_parser("plan", help="Create a dry-run fleet plan.")
    fleet_plan.add_argument("--workers", type=int, required=True)
    fleet_plan.add_argument("--cpu-min-vcpus", type=int)
    fleet_plan.add_argument("--cpu-min-memory-gb", type=int)
    fleet_plan.add_argument("--region")
    fleet_plan.add_argument("--name-prefix", default="worker")
    fleet_apply = fleet_subparsers.add_parser(
        "apply",
        help="Create a named fleet from an explicit instance type.",
    )
    fleet_apply.add_argument("--workers", type=int, required=True)
    fleet_apply.add_argument("--type", required=True, dest="instance_type")
    fleet_apply.add_argument("--name-prefix", default="worker")
    fleet_apply.add_argument("--timeout-seconds", type=int, default=900)
    fleet_apply.add_argument("--require-org")
    fleet_apply.add_argument("--db")
    fleet_apply.add_argument("--yes", action="store_true")
    fleet_exec = fleet_subparsers.add_parser(
        "exec",
        help="Run a command on fleet instances matching a name prefix.",
    )
    fleet_exec.add_argument("--name-prefix", required=True)
    fleet_exec.add_argument("--require-org")
    fleet_exec.add_argument("--db")
    fleet_exec.add_argument("--host", action="store_true")
    fleet_exec.add_argument("remote_command", nargs=argparse.REMAINDER)
    fleet_check = fleet_subparsers.add_parser(
        "check",
        help="Run generic capability checks on fleet instances.",
    )
    fleet_check.add_argument("--name-prefix", required=True)
    fleet_check.add_argument("--require-org")
    fleet_check.add_argument("--db")
    fleet_down = fleet_subparsers.add_parser(
        "down",
        help="Delete fleet instances matching a name prefix.",
    )
    fleet_down.add_argument("--name-prefix", required=True)
    fleet_down.add_argument("--require-org")
    fleet_down.add_argument("--db")
    fleet_down.add_argument("--timeout-seconds", type=float, default=900.0)
    fleet_down.add_argument("--poll-seconds", type=float, default=10.0)
    fleet_down.add_argument("--no-wait", action="store_true")
    fleet_down.add_argument("--yes", action="store_true")

    inventory = subparsers.add_parser("inventory", help="Manage local inventory state.")
    inventory_subparsers = inventory.add_subparsers(
        dest="inventory_command",
        required=True,
    )
    refresh = inventory_subparsers.add_parser(
        "refresh",
        help="Refresh local inventory from 'brev ls --json'.",
    )
    refresh.add_argument("--db", default="brev-control-plane.sqlite3")
    refresh.add_argument("--brev-binary", default="brev")

    jobs = subparsers.add_parser("jobs", help="Validate generic shell job specs.")
    jobs_subparsers = jobs.add_subparsers(dest="jobs_command", required=True)
    validate = jobs_subparsers.add_parser("validate", help="Validate a job JSON file.")
    validate.add_argument("path")
    run = jobs_subparsers.add_parser("run", help="Run a generic shell job on a fleet.")
    run.add_argument("path")
    run.add_argument("--name-prefix", required=True)
    run.add_argument("--require-org")
    run.add_argument("--db")
    run.add_argument("--host", action="store_true")

    return parser


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    client: BrevClient | None = None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "doctor":
            return _doctor(args, stdout)
        if args.command == "fleet" and args.fleet_command == "plan":
            return _fleet_plan(args, stdout)
        if args.command == "fleet" and args.fleet_command == "apply":
            return _fleet_apply(args, stdout, client)
        if args.command == "fleet" and args.fleet_command == "exec":
            return _fleet_exec(args, stdout, client)
        if args.command == "fleet" and args.fleet_command == "check":
            return _fleet_check(args, stdout, client)
        if args.command == "fleet" and args.fleet_command == "down":
            return _fleet_down(args, stdout, client)
        if args.command == "inventory" and args.inventory_command == "refresh":
            return _inventory_refresh(args, stdout, client)
        if args.command == "jobs" and args.jobs_command == "validate":
            return _jobs_validate(args, stdout)
        if args.command == "jobs" and args.jobs_command == "run":
            return _jobs_run(args, stdout, client)
    except (BrevCommandError, BundleError, JobSpecError, PlanError) as exc:
        _write_json(stderr, {"error": str(exc)})
        return 2

    _write_json(stderr, {"error": "unsupported command"})
    return 2


def entrypoint() -> None:
    raise SystemExit(main())


def _doctor(args: argparse.Namespace, stdout: TextIO) -> int:
    brev_path = shutil.which(args.brev_binary)
    payload = {
        "ok": brev_path is not None,
        "checks": {
            "brev_binary": {
                "ok": brev_path is not None,
                "path": brev_path,
            },
            "sqlite": {"ok": True},
        },
    }
    _write_json(stdout, payload)
    return 0 if brev_path is not None else 1


def _fleet_plan(args: argparse.Namespace, stdout: TextIO) -> int:
    plan = plan_fleet(
        workers=args.workers,
        cpu_filter=CpuFilter(
            min_vcpus=args.cpu_min_vcpus,
            min_memory_gb=args.cpu_min_memory_gb,
            region=args.region,
        ),
        name_prefix=args.name_prefix,
    )
    _write_json(stdout, plan)
    return 0


def _fleet_apply(
    args: argparse.Namespace,
    stdout: TextIO,
    client: BrevClient | None,
) -> int:
    if not args.yes:
        raise PlanError("fleet apply creates instances and requires --yes")
    if args.timeout_seconds <= 0:
        raise PlanError("timeout_seconds must be positive")

    plan = plan_fleet(
        workers=args.workers,
        cpu_filter=CpuFilter(),
        name_prefix=args.name_prefix,
    )
    brev_client = client or BrevClient()
    _require_active_org(brev_client, args.require_org)
    store = _state_store(args.db)
    created: list[str] = []
    outputs: dict[str, str] = {}
    for worker in plan["workers"]:
        name = worker["name"]
        outputs[name] = brev_client.create_instance(
            name=name,
            instance_type=args.instance_type,
            timeout_seconds=args.timeout_seconds,
        )
        if store is not None:
            store.record_live_event(
                "fleet.apply.created",
                instance_name=name,
                payload={
                    "instance_type": args.instance_type,
                    "output": outputs[name],
                },
            )
        created.append(name)
    _write_json(
        stdout,
        {
            "created": created,
            "instance_type": args.instance_type,
            "outputs": outputs,
        },
    )
    return 0


def _fleet_exec(
    args: argparse.Namespace,
    stdout: TextIO,
    client: BrevClient | None,
) -> int:
    command = _command_from_remainder(args.remote_command)
    brev_client = client or BrevClient()
    _require_active_org(brev_client, args.require_org)
    store = _state_store(args.db)
    names = _matching_instance_names(brev_client, args.name_prefix)
    results: list[dict[str, Any]] = []
    for name in names:
        try:
            output = brev_client.exec_instance(name, command, host=args.host)
        except BrevCommandError as exc:
            error = str(exc)
            results.append({"instance": name, "ok": False, "error": error})
            if store is not None:
                store.record_live_event(
                    "fleet.exec.failed",
                    instance_name=name,
                    payload={"command": command, "error": error},
                )
        else:
            results.append({"instance": name, "ok": True, "output": output})
            if store is not None:
                store.record_live_event(
                    "fleet.exec.completed",
                    instance_name=name,
                    payload={"command": command, "output": output},
                )
    _write_json(stdout, {"instances": names, "results": results})
    return 0 if all(result["ok"] for result in results) else 2


def _fleet_check(
    args: argparse.Namespace,
    stdout: TextIO,
    client: BrevClient | None,
) -> int:
    brev_client = client or BrevClient()
    _require_active_org(brev_client, args.require_org)
    store = _state_store(args.db)
    names = _matching_instance_names(brev_client, args.name_prefix)
    command = build_check_command()
    checks: list[dict[str, str]] = []
    for name in names:
        output = brev_client.exec_instance(name, command, host=True)
        report = parse_check_output(output)
        report["name"] = name
        checks.append(report)
        if store is not None:
            store.record_live_event(
                "fleet.check.completed",
                instance_name=name,
                payload=report,
            )
    _write_json(stdout, {"instances": names, "checks": checks})
    return 0


def _fleet_down(
    args: argparse.Namespace,
    stdout: TextIO,
    client: BrevClient | None,
) -> int:
    if not args.yes:
        raise PlanError("fleet down deletes instances and requires --yes")
    if args.timeout_seconds < 0:
        raise PlanError("timeout_seconds must be non-negative")
    if args.poll_seconds < 0:
        raise PlanError("poll_seconds must be non-negative")
    brev_client = client or BrevClient()
    _require_active_org(brev_client, args.require_org)
    store = _state_store(args.db)
    names = _matching_instance_names(brev_client, args.name_prefix)
    deleted: list[str] = []
    outputs: dict[str, str] = {}
    delete_results: list[dict[str, Any]] = []
    for name in names:
        try:
            output = brev_client.delete_instance(name)
        except BrevCommandError as exc:
            error = str(exc)
            delete_results.append({"instance": name, "ok": False, "error": error})
            if store is not None:
                store.record_live_event(
                    "fleet.down.failed",
                    instance_name=name,
                    payload={"error": error},
                )
        else:
            deleted.append(name)
            outputs[name] = output
            delete_results.append({"instance": name, "ok": True, "output": output})
            if store is not None:
                store.record_live_event(
                    "fleet.down.deleted",
                    instance_name=name,
                    payload={"output": output},
                )
    delete_failed = any(not result["ok"] for result in delete_results)
    remaining: list[str] = list(names) if args.no_wait else []
    if not args.no_wait:
        remaining = _wait_for_no_matching_instances(
            brev_client,
            args.name_prefix,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
        if remaining and store is not None:
            for name in remaining:
                store.record_live_event(
                    "fleet.down.wait_timeout",
                    instance_name=name,
                    payload={"remaining": remaining},
                )
    _write_json(
        stdout,
        {
            "deleted": deleted,
            "remaining": remaining,
            "outputs": outputs,
            "delete_results": delete_results,
        },
    )
    return 2 if delete_failed or remaining else 0


def _inventory_refresh(
    args: argparse.Namespace,
    stdout: TextIO,
    client: BrevClient | None,
) -> int:
    brev_client = client or BrevClient(binary=args.brev_binary)
    instances = brev_client.list_instances()
    store = StateStore(Path(args.db))
    store.initialize()
    store.upsert_instances(instances)
    store.record_event("inventory.refresh", {"instances": len(instances)})
    _write_json(
        stdout,
        {
            "instances": len(instances),
            "events": len(store.list_events()),
        },
    )
    return 0


def _jobs_validate(args: argparse.Namespace, stdout: TextIO) -> int:
    spec = load_job_spec(args.path)
    _write_json(stdout, {"valid": True, "command": spec.command})
    return 0


def _jobs_run(
    args: argparse.Namespace,
    stdout: TextIO,
    client: BrevClient | None,
) -> int:
    spec_path = Path(args.path)
    spec = load_job_spec(spec_path)
    if spec.artifacts:
        raise JobSpecError("artifact collection is not implemented for jobs run")
    brev_client = client or BrevClient()
    _require_active_org(brev_client, args.require_org)
    store = _state_store(args.db)
    names = _matching_instance_names(brev_client, args.name_prefix)
    run_id = uuid.uuid4().hex

    with tempfile.TemporaryDirectory() as temp_dir:
        local_archive: Path | None = None
        remote_archive: str | None = None
        remote_dir: str | None = None
        if spec.bundle is not None:
            local_archive = _create_job_archive(spec, spec_path, Path(temp_dir))
            remote_dir = f"/tmp/brev-control-plane-job-{run_id}"
            remote_archive = f"{remote_dir}.tar.gz"

        results: list[dict[str, Any]] = []
        for name in names:
            try:
                if local_archive is not None:
                    brev_client.copy_to_instance(
                        local_archive,
                        name,
                        str(remote_archive),
                    )
                output = brev_client.exec_instance(
                    name,
                    _job_remote_command(
                        spec,
                        remote_archive=remote_archive,
                        remote_dir=remote_dir,
                    ),
                    host=args.host,
                )
            except BrevCommandError as exc:
                error = str(exc)
                results.append({"instance": name, "ok": False, "error": error})
                if store is not None:
                    store.record_live_event(
                        "jobs.run.failed",
                        instance_name=name,
                        payload={"command": spec.command, "error": error},
                    )
            else:
                results.append({"instance": name, "ok": True, "output": output})
                if store is not None:
                    store.record_live_event(
                        "jobs.run.completed",
                        instance_name=name,
                        payload={"command": spec.command, "output": output},
                    )

    _write_json(stdout, {"instances": names, "results": results})
    return 0 if all(result["ok"] for result in results) else 2


def _matching_instance_names(
    client: BrevClient,
    name_prefix: str,
) -> list[str]:
    names = _matching_instance_names_or_empty(client, name_prefix)
    if not names:
        raise PlanError(f"no instances found for name_prefix {name_prefix.strip()!r}")
    return names


def _matching_instance_names_or_empty(
    client: BrevClient,
    name_prefix: str,
) -> list[str]:
    prefix = name_prefix.strip()
    if len(prefix) < 3:
        raise PlanError("name_prefix must be at least 3 characters")
    names = [
        str(instance.get("name", ""))
        for instance in client.list_instances()
        if str(instance.get("name", "")).startswith(f"{prefix}-")
    ]
    return sorted(names)


def _wait_for_no_matching_instances(
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
        if poll_seconds > 0:
            time.sleep(poll_seconds)


def _require_active_org(client: BrevClient, required_org: str | None) -> None:
    if not required_org:
        return
    active_org = client.active_org()
    if active_org != required_org:
        raise PlanError(
            f"active Brev org is {active_org!r}; expected {required_org!r}"
        )


def _state_store(path: str | None) -> StateStore | None:
    if not path:
        return None
    store = StateStore(Path(path))
    store.initialize()
    return store


def _create_job_archive(spec: JobSpec, spec_path: Path, temp_dir: Path) -> Path:
    bundle = spec.bundle or {}
    source = bundle.get("source")
    if not isinstance(source, str) or not source.strip():
        raise JobSpecError("bundle.source must be a non-empty string")
    exclude = bundle.get("exclude", [])
    if not isinstance(exclude, list) or not all(isinstance(item, str) for item in exclude):
        raise JobSpecError("bundle.exclude must be an array of strings")
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = spec_path.parent / source_path
    return create_bundle_archive(
        source_path,
        temp_dir / "brev-control-plane-job.tar.gz",
        exclude_names=exclude,
    )


def _job_remote_command(
    spec: JobSpec,
    *,
    remote_archive: str | None,
    remote_dir: str | None,
) -> str:
    command = _job_shell_invocation(spec)
    if remote_archive is None or remote_dir is None:
        return command
    setup = [
        f"rm -rf {shlex.quote(remote_dir)}",
        f"mkdir -p {shlex.quote(remote_dir)}",
        f"tar -xzf {shlex.quote(remote_archive)} -C {shlex.quote(remote_dir)}",
        f"cd {shlex.quote(remote_dir)}",
    ]
    return " && ".join([*setup, command])


def _job_shell_invocation(spec: JobSpec) -> str:
    env_args = [f"{key}={value}" for key, value in spec.env.items()]
    if env_args:
        command = ["env", *env_args, "bash", "-lc", spec.command]
    else:
        command = ["bash", "-lc", spec.command]
    if spec.max_runtime_seconds is not None:
        command = ["timeout", str(spec.max_runtime_seconds), *command]
    return shlex.join(command)


def _command_from_remainder(remainder: list[str]) -> str:
    command_parts = list(remainder)
    if command_parts and command_parts[0] == "--":
        command_parts = command_parts[1:]
    if not command_parts:
        raise PlanError("fleet exec requires a command after --")
    return shlex.join(command_parts)


def _write_json(stream: TextIO, payload: dict[str, Any]) -> None:
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
