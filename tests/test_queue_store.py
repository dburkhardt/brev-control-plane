from datetime import datetime, timedelta, timezone

from brev_control_plane.queue_protocol import QueueJob
from brev_control_plane.queue_store import QueueStore


def test_queue_store_submits_leases_heartbeats_and_completes_jobs(tmp_path):
    store = QueueStore(tmp_path / "queue.sqlite3")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job = QueueJob(
        command="echo hi",
        experiment_id="exp-a",
        output_paths=["out.txt"],
        max_attempts=2,
    )

    job_id = store.submit_job(job, now=now)
    lease = store.lease_next("worker-1", lease_seconds=30, now=now)

    assert lease is not None
    assert lease.job_id == job_id
    assert lease.job == job
    assert lease.attempt == 1
    assert lease.expires_at == now + timedelta(seconds=30)

    assert store.heartbeat(
        job_id,
        lease.lease_id,
        lease_seconds=45,
        now=now + timedelta(seconds=5),
    ) is True

    completed = store.complete_job(
        job_id,
        lease.lease_id,
        artifacts=[
            {
                "path": "out.txt",
                "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
                "size_bytes": 5,
            }
        ],
        returncode=0,
        stdout="hello\n",
        stderr="",
        now=now + timedelta(seconds=10),
    )

    assert completed is True
    assert store.status()["counts"] == {"completed": 1}
    listed = store.list_jobs(experiment_id="exp-a")
    assert len(listed) == 1
    assert listed[0]["id"] == job_id
    assert listed[0]["status"] == "completed"
    assert listed[0]["worker_id"] == "worker-1"
    assert listed[0]["completed_at"] == (now + timedelta(seconds=10)).isoformat()
    assert listed[0]["artifacts"] == [
        {
            "path": "out.txt",
            "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
            "size_bytes": 5,
        }
    ]


def test_queue_store_requeues_expired_leases_until_attempts_are_exhausted(tmp_path):
    store = QueueStore(tmp_path / "queue.sqlite3")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job_id = store.submit_job(
        QueueJob(command="echo retry", experiment_id="exp-a", max_attempts=2),
        now=now,
    )

    first = store.lease_next("worker-1", lease_seconds=10, now=now)
    assert first is not None
    assert store.requeue_expired(now=now + timedelta(seconds=11)) == {
        "failed": 0,
        "requeued": 1,
    }

    second = store.lease_next(
        "worker-2",
        lease_seconds=10,
        now=now + timedelta(seconds=12),
    )
    assert second is not None
    assert second.job_id == job_id
    assert second.attempt == 2

    assert store.requeue_expired(now=now + timedelta(seconds=23)) == {
        "failed": 1,
        "requeued": 0,
    }
    assert store.lease_next("worker-3", lease_seconds=10, now=now + timedelta(seconds=24)) is None
    job = store.list_jobs()[0]
    assert job["status"] == "failed"
    assert job["worker_id"] == "worker-2"
    assert job["completed_at"] == (now + timedelta(seconds=23)).isoformat()


def test_queue_store_fail_job_records_terminal_error(tmp_path):
    store = QueueStore(tmp_path / "queue.sqlite3")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job_id = store.submit_job(QueueJob(command="false", experiment_id="exp-a"), now=now)
    lease = store.lease_next("worker-1", lease_seconds=30, now=now)

    assert lease is not None
    assert store.fail_job(
        job_id,
        lease.lease_id,
        error="command failed",
        returncode=1,
        stdout="",
        stderr="boom",
        now=now + timedelta(seconds=1),
    ) is True

    job = store.list_jobs()[0]
    assert job["status"] == "failed"
    assert job["worker_id"] == "worker-1"
    assert job["completed_at"] == (now + timedelta(seconds=1)).isoformat()
    assert job["error"] == "command failed"
    assert job["returncode"] == 1
