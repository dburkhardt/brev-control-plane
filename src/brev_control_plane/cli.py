from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import shutil
import sys
from typing import Any, TextIO

from .brev import BrevClient, BrevCommandError
from .jobs import JobSpecError, load_job_spec
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
    fleet_apply.add_argument("--yes", action="store_true")
    fleet_exec = fleet_subparsers.add_parser(
        "exec",
        help="Run a command on fleet instances matching a name prefix.",
    )
    fleet_exec.add_argument("--name-prefix", required=True)
    fleet_exec.add_argument("--host", action="store_true")
    fleet_exec.add_argument("remote_command", nargs=argparse.REMAINDER)
    fleet_down = fleet_subparsers.add_parser(
        "down",
        help="Delete fleet instances matching a name prefix.",
    )
    fleet_down.add_argument("--name-prefix", required=True)
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
        if args.command == "fleet" and args.fleet_command == "down":
            return _fleet_down(args, stdout, client)
        if args.command == "inventory" and args.inventory_command == "refresh":
            return _inventory_refresh(args, stdout, client)
        if args.command == "jobs" and args.jobs_command == "validate":
            return _jobs_validate(args, stdout)
    except (BrevCommandError, JobSpecError, PlanError) as exc:
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
    created: list[str] = []
    outputs: dict[str, str] = {}
    for worker in plan["workers"]:
        name = worker["name"]
        outputs[name] = brev_client.create_instance(
            name=name,
            instance_type=args.instance_type,
            timeout_seconds=args.timeout_seconds,
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
    names = _matching_instance_names(brev_client, args.name_prefix)
    output = brev_client.exec_instances(names, command, host=args.host)
    _write_json(stdout, {"instances": names, "output": output})
    return 0


def _fleet_down(
    args: argparse.Namespace,
    stdout: TextIO,
    client: BrevClient | None,
) -> int:
    if not args.yes:
        raise PlanError("fleet down deletes instances and requires --yes")
    brev_client = client or BrevClient()
    names = _matching_instance_names(brev_client, args.name_prefix)
    outputs = {name: brev_client.delete_instance(name) for name in names}
    _write_json(stdout, {"deleted": names, "outputs": outputs})
    return 0


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


def _matching_instance_names(
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
    if not names:
        raise PlanError(f"no instances found for name_prefix {prefix!r}")
    return sorted(names)


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
