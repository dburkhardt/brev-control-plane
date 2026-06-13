from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shlex
import shutil
import sys
import tarfile
import tempfile
import time
import uuid
from typing import Any, TextIO

from .bootstrap import render_worker_bootstrap
from .brev import BrevClient, BrevCommandError
from .bundles import BundleError, create_bundle_archive
from .checks import build_check_command, parse_check_output
from .jobs import JobSpec, JobSpecError, load_job_spec
from .planner import CpuFilter, PlanError, plan_fleet
from .queue_protocol import QueueJob, QueueProtocolError
from .queue_server import create_queue_server
from .queue_store import QueueStore
from .worker import QueueClient, run_worker
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
    fleet_apply.add_argument("--max-workers", type=int)
    fleet_apply.add_argument("--budget-usd", type=float)
    fleet_apply.add_argument("--estimated-hourly-usd", type=float)
    fleet_apply.add_argument("--max-hours", type=float)
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
    fleet_bootstrap = fleet_subparsers.add_parser(
        "bootstrap-workers",
        help="Install and launch queue workers on fleet instances.",
    )
    fleet_bootstrap.add_argument("--name-prefix", required=True)
    fleet_bootstrap.add_argument("--repo-url", required=True)
    fleet_bootstrap.add_argument("--server-url", required=True)
    fleet_bootstrap.add_argument("--token-env", default="BREV_QUEUE_TOKEN")
    fleet_bootstrap.add_argument("--require-org")
    fleet_bootstrap.add_argument("--host", action="store_true")

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
    run.add_argument("--concurrency", type=int, default=1)
    run.add_argument("--copy-attempts", type=int, default=1)
    run.add_argument("--copy-retry-delay-seconds", type=float, default=2.0)
    run.add_argument(
        "--artifact-dir",
        default="/tmp/brev-control-plane-artifacts",
        help="Local directory for artifacts collected by jobs run.",
    )
    run.add_argument(
        "--docker-group",
        action="store_true",
        help="Run the job command through 'sg docker' for hosts whose login shell lacks Docker socket access.",
    )

    queue = subparsers.add_parser("queue", help="Run and inspect the generic job queue.")
    queue_subparsers = queue.add_subparsers(dest="queue_command", required=True)
    queue_serve = queue_subparsers.add_parser("serve", help="Serve the queue HTTP API.")
    queue_serve.add_argument("--db", required=True)
    queue_serve.add_argument("--host", default="127.0.0.1")
    queue_serve.add_argument("--port", type=int, default=8080)
    queue_serve.add_argument("--token")
    queue_serve.add_argument("--token-env", default="BREV_QUEUE_TOKEN")
    queue_submit = queue_subparsers.add_parser("submit", help="Submit a shell job to a queue server.")
    queue_submit.add_argument("--server-url", required=True)
    queue_submit.add_argument("--token")
    queue_submit.add_argument("--token-env", default="BREV_QUEUE_TOKEN")
    queue_submit.add_argument("--experiment-id", required=True)
    queue_submit.add_argument("--env", action="append", default=[])
    queue_submit.add_argument("--output", action="append", default=[])
    queue_submit.add_argument("--max-runtime-seconds", type=int)
    queue_submit.add_argument("--max-attempts", type=int, default=1)
    queue_submit.add_argument("remote_command", nargs=argparse.REMAINDER)
    queue_status = queue_subparsers.add_parser("status", help="Read queue status.")
    queue_status.add_argument("--server-url", required=True)
    queue_status.add_argument("--token")
    queue_status.add_argument("--token-env", default="BREV_QUEUE_TOKEN")
    queue_wait = queue_subparsers.add_parser("wait", help="Wait for one queued job to finish.")
    queue_wait.add_argument("--server-url", required=True)
    queue_wait.add_argument("--token")
    queue_wait.add_argument("--token-env", default="BREV_QUEUE_TOKEN")
    queue_wait.add_argument("--job-id", required=True)
    queue_wait.add_argument("--timeout-seconds", type=float, default=3600.0)
    queue_wait.add_argument("--poll-seconds", type=float, default=5.0)

    worker = subparsers.add_parser("worker", help="Run queue workers.")
    worker_subparsers = worker.add_subparsers(dest="worker_command", required=True)
    worker_run = worker_subparsers.add_parser("run", help="Lease and run shell jobs.")
    worker_run.add_argument("--server-url", required=True)
    worker_run.add_argument("--token")
    worker_run.add_argument("--token-env", default="BREV_QUEUE_TOKEN")
    worker_run.add_argument("--work-dir", required=True)
    worker_run.add_argument("--worker-id")
    worker_run.add_argument("--poll-seconds", type=float, default=5.0)
    worker_run.add_argument("--lease-seconds", type=int, default=300)
    worker_run.add_argument("--once", action="store_true")

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
        if args.command == "fleet" and args.fleet_command == "bootstrap-workers":
            return _fleet_bootstrap_workers(args, stdout, client)
        if args.command == "inventory" and args.inventory_command == "refresh":
            return _inventory_refresh(args, stdout, client)
        if args.command == "jobs" and args.jobs_command == "validate":
            return _jobs_validate(args, stdout)
        if args.command == "jobs" and args.jobs_command == "run":
            return _jobs_run(args, stdout, client)
        if args.command == "queue" and args.queue_command == "serve":
            return _queue_serve(args, stdout)
        if args.command == "queue" and args.queue_command == "submit":
            return _queue_submit(args, stdout)
        if args.command == "queue" and args.queue_command == "status":
            return _queue_status(args, stdout)
        if args.command == "queue" and args.queue_command == "wait":
            return _queue_wait(args, stdout)
        if args.command == "worker" and args.worker_command == "run":
            return _worker_run(args)
    except (
        BrevCommandError,
        BundleError,
        JobSpecError,
        PlanError,
        QueueProtocolError,
        RuntimeError,
    ) as exc:
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
    _check_fleet_apply_budget(args)

    plan = plan_fleet(
        workers=args.workers,
        cpu_filter=CpuFilter(),
        name_prefix=args.name_prefix,
    )
    brev_client = client or BrevClient()
    _require_active_org(brev_client, args.require_org)
    store = _state_store(args.db)
    existing_names = set(_matching_instance_names_or_empty(brev_client, args.name_prefix))
    existing_target: list[str] = []
    created: list[str] = []
    outputs: dict[str, str] = {}
    for worker in plan["workers"]:
        name = worker["name"]
        if name in existing_names:
            existing_target.append(name)
            continue
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
            "existing": existing_target,
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
    results: list[dict[str, Any]] = []
    for name in names:
        try:
            output = brev_client.exec_instance(name, command, host=True)
        except BrevCommandError as exc:
            error = str(exc)
            results.append({"instance": name, "ok": False, "error": error})
            if store is not None:
                store.record_live_event(
                    "fleet.check.failed",
                    instance_name=name,
                    payload={"error": error},
                )
        else:
            report = parse_check_output(output)
            report["name"] = name
            checks.append(report)
            results.append({"instance": name, "ok": True, "check": report})
            if store is not None:
                store.record_live_event(
                    "fleet.check.completed",
                    instance_name=name,
                    payload=report,
                )
    _write_json(stdout, {"instances": names, "checks": checks, "results": results})
    return 0 if all(result["ok"] for result in results) else 2


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
    verified_absent = not remaining if not args.no_wait else False
    _write_json(
        stdout,
        {
            "deleted": deleted,
            "remaining": remaining,
            "verified_absent": verified_absent,
            "outputs": outputs,
            "delete_results": delete_results,
        },
    )
    wait_timed_out = not args.no_wait and bool(remaining)
    return 2 if delete_failed or wait_timed_out else 0


def _fleet_bootstrap_workers(
    args: argparse.Namespace,
    stdout: TextIO,
    client: BrevClient | None,
) -> int:
    brev_client = client or BrevClient()
    _require_active_org(brev_client, args.require_org)
    names = _matching_instance_names(brev_client, args.name_prefix)
    results: list[dict[str, Any]] = []
    for name in names:
        script = render_worker_bootstrap(
            repo_url=args.repo_url,
            server_url=args.server_url,
            token_env_name=args.token_env,
            worker_name=name,
        )
        output = brev_client.exec_instance(name, script, host=args.host)
        results.append({"instance": name, "ok": True, "output": output})
    _write_json(stdout, {"instances": names, "results": results})
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


def _jobs_run(
    args: argparse.Namespace,
    stdout: TextIO,
    client: BrevClient | None,
) -> int:
    spec_path = Path(args.path)
    spec = load_job_spec(spec_path)
    if spec.artifacts and spec.bundle is None:
        raise JobSpecError("artifact collection requires a bundle")
    if args.concurrency <= 0:
        raise PlanError("concurrency must be positive")
    if args.copy_attempts <= 0:
        raise PlanError("copy_attempts must be positive")
    if args.copy_retry_delay_seconds < 0:
        raise PlanError("copy_retry_delay_seconds must be non-negative")
    brev_client = client or BrevClient()
    _require_active_org(brev_client, args.require_org)
    store = _state_store(args.db)
    names = _matching_instance_names(brev_client, args.name_prefix)
    run_id = uuid.uuid4().hex
    artifact_dir = Path(args.artifact_dir)
    artifact_run_dir = artifact_dir / run_id

    with tempfile.TemporaryDirectory() as temp_dir:
        local_archive: Path | None = None
        remote_archive: str | None = None
        remote_dir: str | None = None
        if spec.bundle is not None:
            local_archive = _create_job_archive(spec, spec_path, Path(temp_dir))
            remote_dir = f"/tmp/brev-control-plane-job-{run_id}"
            remote_archive = f"{remote_dir}.tar.gz"

        def run_one(name: str) -> dict[str, Any]:
            try:
                if local_archive is not None:
                    _copy_to_instance_with_retries(
                        brev_client,
                        local_archive,
                        name,
                        str(remote_archive),
                        attempts=args.copy_attempts,
                        delay_seconds=args.copy_retry_delay_seconds,
                    )
                output = brev_client.exec_instance(
                    name,
                    _job_remote_command(
                        spec,
                        remote_archive=remote_archive,
                        remote_dir=remote_dir,
                        docker_group=args.docker_group,
                    ),
                    host=args.host,
                )
                artifacts = []
                if spec.artifacts:
                    if remote_dir is None:
                        raise JobSpecError("artifact collection requires a bundle")
            except BrevCommandError as exc:
                return {"instance": name, "ok": False, "error": str(exc)}
            if spec.artifacts:
                try:
                    collected = _collect_artifacts_with_retries(
                        brev_client,
                        name,
                        spec.artifacts,
                        remote_dir=str(remote_dir),
                        artifact_run_dir=artifact_run_dir,
                        host=args.host,
                        attempts=args.copy_attempts,
                        delay_seconds=args.copy_retry_delay_seconds,
                    )
                except BrevCommandError as exc:
                    return {
                        "instance": name,
                        "ok": False,
                        "output": output,
                        "run_id": run_id,
                        "error": str(exc),
                    }
                artifacts = collected["artifacts"]
                return {
                    "instance": name,
                    "ok": True,
                    "output": output,
                    "run_id": run_id,
                    "artifacts": artifacts,
                    "artifact_archive": collected["archive"],
                }
            return {
                "instance": name,
                "ok": True,
                "output": output,
            }

        with ThreadPoolExecutor(max_workers=min(args.concurrency, len(names))) as executor:
            results = list(executor.map(run_one, names))

        if store is not None:
            for result in results:
                if result["ok"]:
                    store.record_live_event(
                        "jobs.run.completed",
                        instance_name=str(result["instance"]),
                        payload=_job_event_payload(
                            spec,
                            result,
                            run_id=run_id,
                            artifact_dir=artifact_dir,
                        ),
                    )
                else:
                    store.record_live_event(
                        "jobs.run.failed",
                        instance_name=str(result["instance"]),
                        payload=_job_event_payload(
                            spec,
                            result,
                            run_id=run_id,
                            artifact_dir=artifact_dir,
                        ),
                    )

    _write_json(stdout, {"instances": names, "results": results})
    return 0 if all(result["ok"] for result in results) else 2


def _queue_serve(args: argparse.Namespace, stdout: TextIO) -> int:
    token = _queue_token(args)
    store = QueueStore(Path(args.db))
    server = create_queue_server(args.host, args.port, store=store, token=token)
    _write_json(
        stdout,
        {
            "ok": True,
            "server": {
                "host": server.server_address[0],
                "port": server.server_address[1],
            },
        },
    )
    server.serve_forever()
    return 0


def _queue_submit(args: argparse.Namespace, stdout: TextIO) -> int:
    job = QueueJob(
        command=_command_from_remainder(args.remote_command),
        env=_env_from_args(args.env),
        experiment_id=args.experiment_id,
        max_attempts=args.max_attempts,
        max_runtime_seconds=args.max_runtime_seconds,
        output_paths=list(args.output),
    )
    payload = QueueClient(server_url=args.server_url, token=_queue_token(args)).submit(job)
    _write_json(stdout, payload)
    return 0


def _queue_status(args: argparse.Namespace, stdout: TextIO) -> int:
    payload = QueueClient(server_url=args.server_url, token=_queue_token(args)).status()
    _write_json(stdout, payload)
    return 0


def _queue_wait(args: argparse.Namespace, stdout: TextIO) -> int:
    if args.timeout_seconds < 0:
        raise PlanError("timeout_seconds must be non-negative")
    if args.poll_seconds < 0:
        raise PlanError("poll_seconds must be non-negative")
    client = QueueClient(server_url=args.server_url, token=_queue_token(args))
    deadline = time.monotonic() + args.timeout_seconds
    while True:
        payload = client.jobs()
        matches = [job for job in payload["jobs"] if job["id"] == args.job_id]
        if not matches:
            raise PlanError(f"job not found: {args.job_id}")
        job = matches[0]
        if job["status"] in {"completed", "failed"}:
            _write_json(stdout, {"job": job, "ok": True})
            return 0 if job["status"] == "completed" else 2
        if time.monotonic() >= deadline:
            _write_json(stdout, {"error": "timeout", "job": job, "ok": False})
            return 1
        if args.poll_seconds > 0:
            time.sleep(args.poll_seconds)


def _worker_run(args: argparse.Namespace) -> int:
    run_worker(
        server_url=args.server_url,
        token=_queue_token(args),
        work_dir=args.work_dir,
        worker_id=args.worker_id,
        poll_seconds=args.poll_seconds,
        once=args.once,
        lease_seconds=args.lease_seconds,
    )
    return 0


def _copy_to_instance_with_retries(
    brev_client: BrevClient,
    local_archive: Path,
    name: str,
    remote_archive: str,
    *,
    attempts: int,
    delay_seconds: float,
) -> None:
    for attempt in range(1, attempts + 1):
        try:
            brev_client.copy_to_instance(local_archive, name, remote_archive)
            return
        except BrevCommandError:
            if attempt == attempts:
                raise
            if delay_seconds > 0:
                time.sleep(delay_seconds)


def _collect_artifacts_with_retries(
    brev_client: BrevClient,
    name: str,
    artifacts: list[str],
    *,
    remote_dir: str,
    artifact_run_dir: Path,
    host: bool,
    attempts: int,
    delay_seconds: float,
) -> dict[str, Any]:
    remote_archive = f"{remote_dir}.artifacts.tar.gz"
    local_instance_name = _safe_local_instance_name(name)
    local_instance_dir = artifact_run_dir / local_instance_name
    local_archive = artifact_run_dir / "_archives" / f"{local_instance_name}.tar.gz"
    try:
        local_instance_dir.mkdir(parents=True, exist_ok=True)
        local_archive.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BrevCommandError(
            "failed to prepare local artifact directory "
            f"destination={local_instance_dir} archive={local_archive}: {exc}"
        ) from exc

    try:
        _archive_remote_artifacts(
            brev_client,
            name,
            artifacts,
            remote_dir=remote_dir,
            remote_archive=remote_archive,
            host=host,
        )
        _copy_from_instance_with_retries(
            brev_client,
            name,
            remote_archive,
            local_archive,
            attempts=attempts,
            delay_seconds=delay_seconds,
        )
        _extract_tar_safely(local_archive, local_instance_dir, artifacts)
    finally:
        _cleanup_remote_artifact_archive(
            brev_client,
            name,
            remote_archive=remote_archive,
            host=host,
        )

    return {
        "archive": {
            "remote_path": remote_archive,
            "local_path": str(local_archive),
        },
        "artifacts": [
            {
                "path": artifact,
                "local_path": str(local_instance_dir / _local_artifact_path(artifact)),
            }
            for artifact in artifacts
        ],
    }


def _archive_remote_artifacts(
    brev_client: BrevClient,
    name: str,
    artifacts: list[str],
    *,
    remote_dir: str,
    remote_archive: str,
    host: bool,
) -> None:
    artifact_args = " ".join(shlex.quote(artifact) for artifact in artifacts)
    command = " && ".join(
        [
            f"cd {shlex.quote(remote_dir)}",
            f"tar -czf {shlex.quote(remote_archive)} -- {artifact_args}",
        ]
    )
    try:
        brev_client.exec_instance(name, command, host=host)
    except BrevCommandError as exc:
        raise BrevCommandError(
            "failed to archive requested artifacts "
            f"{artifacts!r}: remote_dir={remote_dir} "
            f"remote_archive={remote_archive}: {exc}"
        ) from exc


def _copy_from_instance_with_retries(
    brev_client: BrevClient,
    name: str,
    remote_path: str,
    local_path: Path,
    *,
    attempts: int,
    delay_seconds: float,
) -> None:
    for attempt in range(1, attempts + 1):
        try:
            brev_client.copy_from_instance(name, remote_path, local_path)
            return
        except BrevCommandError as exc:
            if attempt == attempts:
                raise BrevCommandError(
                    "failed to copy artifact archive "
                    f"remote_path={remote_path} local_path={local_path} "
                    f"attempts={attempts}: {exc}"
                ) from exc
            if delay_seconds > 0:
                time.sleep(delay_seconds)


def _cleanup_remote_artifact_archive(
    brev_client: BrevClient,
    name: str,
    *,
    remote_archive: str,
    host: bool,
) -> None:
    try:
        brev_client.exec_instance(
            name,
            f"rm -f {shlex.quote(remote_archive)}",
            host=host,
        )
    except BrevCommandError:
        pass


def _safe_local_instance_name(name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
    sanitized = sanitized.replace("/", "_").replace("\\", "_")
    if sanitized in {"", ".", ".."}:
        return "instance"
    return sanitized


def _local_artifact_path(artifact: str) -> Path:
    return Path(*PurePosixPath(artifact).parts)


def _extract_tar_safely(
    archive_path: Path,
    destination: Path,
    requested_artifacts: list[str],
) -> None:
    requested = [_artifact_prefix(artifact) for artifact in requested_artifacts]
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                member_path = PurePosixPath(member.name)
                if (
                    member_path.is_absolute()
                    or not member_path.parts
                    or "\\" in member.name
                    or PureWindowsPath(member.name).drive
                    or ".." in member_path.parts
                    or member.issym()
                    or member.islnk()
                    or not (member.isfile() or member.isdir())
                ):
                    raise BrevCommandError(
                        f"unsafe artifact archive member: {member.name!r}"
                    )
                if not any(_archive_member_matches(member.name, item) for item in requested):
                    raise BrevCommandError(
                        f"unrequested artifact archive member: {member.name!r}"
                    )
            archive.extractall(destination)
    except tarfile.TarError as exc:
        raise BrevCommandError(f"failed to extract artifact archive {archive_path}: {exc}") from exc
    except OSError as exc:
        raise BrevCommandError(
            f"failed to extract artifact archive {archive_path} into {destination}: {exc}"
        ) from exc


def _artifact_prefix(artifact: str) -> tuple[str, bool]:
    stripped = artifact.strip("/")
    return stripped, artifact.endswith("/")


def _archive_member_matches(member_name: str, requested: tuple[str, bool]) -> bool:
    requested_path, is_directory = requested
    member = member_name.rstrip("/")
    if is_directory:
        return member == requested_path or member.startswith(f"{requested_path}/")
    return member == requested_path


def _job_event_payload(
    spec: JobSpec,
    result: dict[str, Any],
    *,
    run_id: str,
    artifact_dir: Path,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"command": spec.command}
    if "output" in result:
        payload["output"] = result["output"]
    if "error" in result:
        payload["error"] = result["error"]
    if spec.artifacts:
        payload.update(
            {
                "run_id": run_id,
                "artifact_dir": str(artifact_dir),
                "artifacts_requested": list(spec.artifacts),
            }
        )
        if "artifacts" in result:
            payload["artifacts"] = result["artifacts"]
        if "artifact_archive" in result:
            payload["artifact_archive"] = result["artifact_archive"]
    return payload


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


def _check_fleet_apply_budget(args: argparse.Namespace) -> None:
    if args.max_workers is not None:
        if args.max_workers <= 0:
            raise PlanError("max-workers must be positive")
        if args.workers > args.max_workers:
            raise PlanError(
                f"requested workers {args.workers} exceeds max-workers {args.max_workers}"
            )
    budget_args = [args.budget_usd, args.estimated_hourly_usd, args.max_hours]
    if any(value is not None for value in budget_args):
        if any(value is None for value in budget_args):
            raise PlanError(
                "budget guard requires --budget-usd, --estimated-hourly-usd, and --max-hours"
            )
        if args.budget_usd < 0:
            raise PlanError("budget-usd must be non-negative")
        if args.estimated_hourly_usd < 0:
            raise PlanError("estimated-hourly-usd must be non-negative")
        if args.max_hours < 0:
            raise PlanError("max-hours must be non-negative")
        estimated = args.workers * args.estimated_hourly_usd * args.max_hours
        if estimated > args.budget_usd:
            raise PlanError(
                "estimated fleet cost "
                f"{estimated:.2f} exceeds budget-usd {args.budget_usd:.2f}"
            )


def _queue_token(args: argparse.Namespace) -> str:
    token = getattr(args, "token", None)
    if token:
        return token
    token_env = getattr(args, "token_env", "BREV_QUEUE_TOKEN")
    env_token = os.environ.get(token_env)
    if env_token:
        return env_token
    raise PlanError(f"queue token must be provided with --token or ${token_env}")


def _env_from_args(items: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise PlanError("queue submit --env values must be KEY=VALUE")
        key, value = item.split("=", 1)
        if not key:
            raise PlanError("queue submit --env keys must be non-empty")
        env[key] = value
    return env


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
    docker_group: bool = False,
) -> str:
    command = _job_shell_invocation(spec, docker_group=docker_group)
    if remote_archive is None or remote_dir is None:
        return command
    setup = [
        f"rm -rf {shlex.quote(remote_dir)}",
        f"mkdir -p {shlex.quote(remote_dir)}",
        f"tar -xzf {shlex.quote(remote_archive)} -C {shlex.quote(remote_dir)}",
        f"cd {shlex.quote(remote_dir)}",
    ]
    return " && ".join([*setup, command])


def _job_shell_invocation(spec: JobSpec, *, docker_group: bool = False) -> str:
    env_args = [f"{key}={value}" for key, value in spec.env.items()]
    if env_args:
        command = ["env", *env_args, "bash", "-lc", spec.command]
    else:
        command = ["bash", "-lc", spec.command]
    if docker_group:
        command = ["sg", "docker", "-c", shlex.join(command)]
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
