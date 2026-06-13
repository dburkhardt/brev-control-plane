from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any
import urllib.error
from urllib.parse import quote
import urllib.request
import uuid

from .queue_protocol import QueueJob, QueueLease


def run_worker_once(
    *,
    server_url: str,
    token: str,
    work_dir: str | Path,
    worker_id: str,
    lease_seconds: int = 300,
) -> bool:
    client = QueueClient(server_url=server_url, token=token)
    lease = client.lease(worker_id=worker_id, lease_seconds=lease_seconds)
    if lease is None:
        return False
    job_dir = Path(work_dir) / lease.job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    result = _run_job(lease.job, job_dir)
    if result["ok"]:
        client.complete(
            job_id=lease.job_id,
            lease_id=lease.lease_id,
            artifacts=result["artifacts"],
            returncode=result["returncode"],
            stdout=result["stdout"],
            stderr=result["stderr"],
        )
    else:
        client.fail(
            job_id=lease.job_id,
            lease_id=lease.lease_id,
            error=result["error"],
            returncode=result.get("returncode"),
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
        )
    return True


def run_worker(
    *,
    server_url: str,
    token: str,
    work_dir: str | Path,
    worker_id: str | None = None,
    poll_seconds: float = 5.0,
    once: bool = False,
    lease_seconds: int = 300,
) -> None:
    resolved_worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
    while True:
        did_work = run_worker_once(
            server_url=server_url,
            token=token,
            work_dir=work_dir,
            worker_id=resolved_worker_id,
            lease_seconds=lease_seconds,
        )
        if once:
            return
        if not did_work and poll_seconds > 0:
            time.sleep(poll_seconds)


class QueueClient:
    def __init__(self, *, server_url: str, token: str) -> None:
        self.server_url = server_url.rstrip("/")
        self.token = token

    def lease(self, *, worker_id: str, lease_seconds: int) -> QueueLease | None:
        payload = self._request(
            "POST",
            "/api/v1/leases",
            {"worker_id": worker_id, "lease_seconds": lease_seconds},
        )
        lease = payload["lease"]
        return QueueLease.from_dict(lease) if lease is not None else None

    def complete(
        self,
        *,
        job_id: str,
        lease_id: str,
        artifacts: list[dict[str, Any]],
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> None:
        self._request(
            "POST",
            "/api/v1/complete",
            {
                "artifacts": artifacts,
                "job_id": job_id,
                "lease_id": lease_id,
                "returncode": returncode,
                "stderr": stderr,
                "stdout": stdout,
            },
        )

    def fail(
        self,
        *,
        job_id: str,
        lease_id: str,
        error: str,
        returncode: int | None,
        stdout: str,
        stderr: str,
    ) -> None:
        self._request(
            "POST",
            "/api/v1/fail",
            {
                "error": error,
                "job_id": job_id,
                "lease_id": lease_id,
                "returncode": returncode,
                "stderr": stderr,
                "stdout": stdout,
            },
        )

    def submit(self, job: QueueJob) -> dict[str, Any]:
        return self._request("POST", "/api/v1/jobs", job.to_dict())

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/status")

    def jobs(self, *, experiment_id: str | None = None) -> dict[str, Any]:
        suffix = ""
        if experiment_id is not None:
            suffix = f"?experiment_id={quote(experiment_id)}"
        return self._request("GET", f"/api/v1/jobs{suffix}")

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.server_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            raise RuntimeError(body.get("error", f"HTTP {exc.code}")) from exc
        if not body.get("ok"):
            raise RuntimeError(str(body.get("error", "queue request failed")))
        return body


def _run_job(job: QueueJob, job_dir: Path) -> dict[str, Any]:
    env = dict(os.environ)
    env.update(job.env)
    try:
        completed = subprocess.run(
            job.command,
            cwd=job_dir,
            env=env,
            shell=True,
            capture_output=True,
            text=True,
            timeout=job.max_runtime_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "error": f"command timed out after {job.max_runtime_seconds} seconds",
            "ok": False,
            "stderr": exc.stderr or "",
            "stdout": exc.stdout or "",
        }

    artifacts: list[dict[str, Any]] = []
    if completed.returncode == 0:
        try:
            for output_path in job.output_paths:
                artifacts.extend(_hash_output_path(job_dir, output_path))
        except RuntimeError as exc:
            return {
                "error": str(exc),
                "ok": False,
                "returncode": completed.returncode,
                "stderr": completed.stderr,
                "stdout": completed.stdout,
            }
        return {
            "artifacts": artifacts,
            "ok": True,
            "returncode": completed.returncode,
            "stderr": completed.stderr,
            "stdout": completed.stdout,
        }

    return {
        "error": f"command exited with status {completed.returncode}",
        "ok": False,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
        "stdout": completed.stdout,
    }


def _hash_output_path(job_dir: Path, output_path: str) -> list[dict[str, Any]]:
    target = job_dir / output_path
    if target.is_dir():
        artifacts = []
        for path in sorted(item for item in target.rglob("*") if item.is_file()):
            artifacts.append(_hash_file(job_dir, path))
        return artifacts
    if not target.is_file():
        raise RuntimeError(f"requested output path was not produced: {output_path}")
    return [_hash_file(job_dir, target)]


def _hash_file(job_dir: Path, path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": path.relative_to(job_dir).as_posix(),
        "sha256": digest.hexdigest(),
        "size_bytes": path.stat().st_size,
    }
