import hashlib
import json
import sys
import threading
import urllib.request

from brev_control_plane.queue_protocol import QueueJob
from brev_control_plane.queue_server import create_queue_server
from brev_control_plane.queue_store import QueueStore
from brev_control_plane.worker import run_worker_once


def _server(tmp_path):
    store = QueueStore(tmp_path / "queue.sqlite3")
    server = create_queue_server("127.0.0.1", 0, store=store, token="secret-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, store, f"http://{server.server_address[0]}:{server.server_address[1]}"


def test_worker_leases_shell_job_hashes_outputs_and_reports_complete(tmp_path):
    server, store, base_url = _server(tmp_path)
    try:
        job_id = store.submit_job(
            QueueJob(
                command=(
                    f"{sys.executable} -c "
                    "\"from pathlib import Path; Path('out.txt').write_text('hello')\""
                ),
                experiment_id="exp-a",
                output_paths=["out.txt"],
            )
        )

        assert run_worker_once(
            server_url=base_url,
            token="secret-token",
            work_dir=tmp_path / "work",
            worker_id="worker-1",
        ) is True

        job = store.list_jobs()[0]
        assert job["id"] == job_id
        assert job["status"] == "completed"
        assert job["returncode"] == 0
        assert job["artifacts"] == [
            {
                "path": "out.txt",
                "sha256": hashlib.sha256(b"hello").hexdigest(),
                "size_bytes": 5,
            }
        ]
    finally:
        server.shutdown()
        server.server_close()


def test_worker_reports_failure_when_subprocess_times_out(tmp_path):
    server, store, base_url = _server(tmp_path)
    try:
        store.submit_job(
            QueueJob(
                command=f"{sys.executable} -c \"import time; time.sleep(5)\"",
                experiment_id="exp-a",
                max_runtime_seconds=1,
            )
        )

        assert run_worker_once(
            server_url=base_url,
            token="secret-token",
            work_dir=tmp_path / "work",
            worker_id="worker-1",
        ) is True

        job = store.list_jobs()[0]
        assert job["status"] == "failed"
        assert "timed out" in job["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_worker_reports_failure_when_requested_output_is_missing(tmp_path):
    server, store, base_url = _server(tmp_path)
    try:
        store.submit_job(
            QueueJob(
                command=f"{sys.executable} -c \"print('done')\"",
                experiment_id="exp-a",
                output_paths=["missing.txt"],
            )
        )

        assert run_worker_once(
            server_url=base_url,
            token="secret-token",
            work_dir=tmp_path / "work",
            worker_id="worker-1",
        ) is True

        job = store.list_jobs()[0]
        assert job["status"] == "failed"
        assert "requested output path was not produced" in job["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_worker_returns_false_when_no_lease_is_available(tmp_path):
    server, _, base_url = _server(tmp_path)
    try:
        assert run_worker_once(
            server_url=base_url,
            token="secret-token",
            work_dir=tmp_path / "work",
            worker_id="worker-1",
        ) is False
    finally:
        server.shutdown()
        server.server_close()
