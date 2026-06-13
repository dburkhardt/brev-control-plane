import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from brev_control_plane.queue_protocol import QueueJob
from brev_control_plane.queue_server import create_queue_server
from brev_control_plane.queue_store import QueueStore


def _request_json(base_url, method, path, *, token=None, payload=None):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_queue_server_requires_token_and_serves_json_queue_endpoints(tmp_path):
    store = QueueStore(tmp_path / "queue.sqlite3")
    server = create_queue_server("127.0.0.1", 0, store=store, token="secret-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, body = _request_json(
            base_url,
            "POST",
            "/api/v1/jobs",
            payload=QueueJob(command="echo hi", experiment_id="exp-a").to_dict(),
        )
        assert status == 401
        assert body == {"ok": False, "error": "unauthorized"}

        status, body = _request_json(
            base_url,
            "POST",
            "/api/v1/jobs",
            token="secret-token",
            payload=QueueJob(
                command="echo hi",
                experiment_id="exp-a",
                output_paths=["out.txt"],
            ).to_dict(),
        )
        assert status == 200
        assert body["ok"] is True
        job_id = body["job_id"]

        status, body = _request_json(
            base_url,
            "POST",
            "/api/v1/leases",
            token="secret-token",
            payload={"worker_id": "worker-1", "lease_seconds": 60},
        )
        assert status == 200
        assert body["ok"] is True
        assert body["lease"]["job_id"] == job_id
        assert body["lease"]["job"]["command"] == "echo hi"

        status, body = _request_json(
            base_url,
            "POST",
            "/api/v1/complete",
            token="secret-token",
            payload={
                "job_id": job_id,
                "lease_id": body["lease"]["lease_id"],
                "artifacts": [
                    {
                        "path": "out.txt",
                        "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
                        "size_bytes": 5,
                    }
                ],
                "returncode": 0,
                "stdout": "hello\n",
                "stderr": "",
            },
        )
        assert status == 200
        assert body == {"ok": True}

        status, body = _request_json(
            base_url,
            "GET",
            "/api/v1/jobs?experiment_id=exp-a",
            token="secret-token",
        )
        assert status == 200
        assert body["ok"] is True
        assert body["jobs"][0]["id"] == job_id
        assert body["jobs"][0]["status"] == "completed"
    finally:
        server.shutdown()
        server.server_close()


def test_queue_server_filters_jobs_by_id_and_experiment_id(tmp_path):
    store = QueueStore(tmp_path / "queue.sqlite3")
    first_job_id = store.submit_job(QueueJob(command="echo one", experiment_id="exp-a"))
    second_job_id = store.submit_job(QueueJob(command="echo two", experiment_id="exp-b"))
    server = create_queue_server("127.0.0.1", 0, store=store, token="secret-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, body = _request_json(
            base_url,
            "GET",
            f"/api/v1/jobs?id={second_job_id}",
            token="secret-token",
        )
        assert status == 200
        assert body["ok"] is True
        assert [job["id"] for job in body["jobs"]] == [second_job_id]

        status, body = _request_json(
            base_url,
            "GET",
            f"/api/v1/jobs?id={second_job_id}&experiment_id=exp-b",
            token="secret-token",
        )
        assert status == 200
        assert body["ok"] is True
        assert [job["id"] for job in body["jobs"]] == [second_job_id]

        status, body = _request_json(
            base_url,
            "GET",
            f"/api/v1/jobs?id={second_job_id}&experiment_id=exp-a",
            token="secret-token",
        )
        assert status == 200
        assert body["ok"] is True
        assert body["jobs"] == []

        status, body = _request_json(
            base_url,
            "GET",
            f"/api/v1/jobs?id={first_job_id}&experiment_id=exp-a",
            token="secret-token",
        )
        assert status == 200
        assert body["ok"] is True
        assert [job["id"] for job in body["jobs"]] == [first_job_id]
    finally:
        server.shutdown()
        server.server_close()


def test_queue_server_status_sweeps_expired_leases(tmp_path):
    store = QueueStore(tmp_path / "queue.sqlite3")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store.submit_job(
        QueueJob(command="echo stale", experiment_id="exp-a", max_attempts=1),
        now=now,
    )
    lease = store.lease_next(
        "worker-1",
        lease_seconds=1,
        now=now,
    )
    assert lease is not None
    server = create_queue_server("127.0.0.1", 0, store=store, token="secret-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, body = _request_json(
            base_url,
            "GET",
            "/api/v1/status",
            token="secret-token",
        )

        assert status == 200
        assert body["ok"] is True
        assert body["status"]["counts"] == {"failed": 1}
        job = store.list_jobs()[0]
        assert job["status"] == "failed"
        assert job["completed_at"] != (now + timedelta(seconds=1)).isoformat()
    finally:
        server.shutdown()
        server.server_close()
