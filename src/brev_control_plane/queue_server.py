from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

from .queue_protocol import QueueJob, QueueProtocolError, validate_queue_token
from .queue_store import QueueStore


class QueueHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        *,
        store: QueueStore,
        token: str,
    ) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.store = store
        self.token = token


def create_queue_server(
    host: str,
    port: int,
    *,
    store: QueueStore,
    token: str,
) -> QueueHTTPServer:
    store.initialize()
    return QueueHTTPServer((host, port), _QueueHandler, store=store, token=token)


class _QueueHandler(BaseHTTPRequestHandler):
    server: QueueHTTPServer

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle(self) -> None:
        try:
            if not self._authorized():
                self._write_json(401, {"ok": False, "error": "unauthorized"})
                return
            parsed = urlparse(self.path)
            if self.command == "GET":
                self._handle_get(parsed.path, parse_qs(parsed.query))
                return
            if self.command == "POST":
                self._handle_post(parsed.path, self._read_json())
                return
            self._write_json(405, {"ok": False, "error": "method not allowed"})
        except (QueueProtocolError, ValueError, KeyError, TypeError) as exc:
            self._write_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._write_json(500, {"ok": False, "error": str(exc)})

    def _handle_get(self, path: str, query: dict[str, list[str]]) -> None:
        if path == "/api/v1/status":
            self._write_json(200, {"ok": True, "status": self.server.store.status()})
            return
        if path == "/api/v1/jobs":
            experiment_values = query.get("experiment_id")
            experiment_id = experiment_values[0] if experiment_values else None
            id_values = query.get("id")
            job_id = id_values[0] if id_values else None
            self._write_json(
                200,
                {
                    "jobs": self.server.store.list_jobs(
                        experiment_id=experiment_id,
                        job_id=job_id,
                    ),
                    "ok": True,
                },
            )
            return
        self._write_json(404, {"ok": False, "error": "not found"})

    def _handle_post(self, path: str, payload: dict[str, Any]) -> None:
        if path == "/api/v1/jobs":
            job_id = self.server.store.submit_job(QueueJob.from_dict(payload))
            self._write_json(200, {"job_id": job_id, "ok": True})
            return
        if path == "/api/v1/leases":
            lease = self.server.store.lease_next(
                _required_string(payload, "worker_id"),
                lease_seconds=_required_positive_int(payload, "lease_seconds"),
            )
            self._write_json(
                200,
                {"lease": lease.to_dict() if lease is not None else None, "ok": True},
            )
            return
        if path == "/api/v1/heartbeat":
            ok = self.server.store.heartbeat(
                _required_string(payload, "job_id"),
                _required_string(payload, "lease_id"),
                lease_seconds=_required_positive_int(payload, "lease_seconds"),
            )
            self._write_json(200 if ok else 409, {"ok": ok} if ok else {"ok": False, "error": "lease not active"})
            return
        if path == "/api/v1/complete":
            ok = self.server.store.complete_job(
                _required_string(payload, "job_id"),
                _required_string(payload, "lease_id"),
                artifacts=_optional_artifacts(payload),
                returncode=_optional_int(payload, "returncode"),
                stdout=_optional_string(payload, "stdout"),
                stderr=_optional_string(payload, "stderr"),
            )
            self._write_json(200 if ok else 409, {"ok": ok} if ok else {"ok": False, "error": "lease not active"})
            return
        if path == "/api/v1/fail":
            ok = self.server.store.fail_job(
                _required_string(payload, "job_id"),
                _required_string(payload, "lease_id"),
                error=_required_string(payload, "error"),
                returncode=_optional_int(payload, "returncode"),
                stdout=_optional_string(payload, "stdout"),
                stderr=_optional_string(payload, "stderr"),
            )
            self._write_json(200 if ok else 409, {"ok": ok} if ok else {"ok": False, "error": "lease not active"})
            return
        self._write_json(404, {"ok": False, "error": "not found"})

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        supplied = header[len(prefix) :] if header.startswith(prefix) else None
        return validate_queue_token(self.server.token, supplied)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8") if raw else "{}")
        except json.JSONDecodeError as exc:
            raise QueueProtocolError(f"request is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise QueueProtocolError("request body must be a JSON object")
        return payload

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise QueueProtocolError(f"{key} must be a non-empty string")
    return value


def _required_positive_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise QueueProtocolError(f"{key} must be positive")
    return value


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise QueueProtocolError(f"{key} must be an integer")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise QueueProtocolError(f"{key} must be a string")
    return value


def _optional_artifacts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = payload.get("artifacts", [])
    if not isinstance(artifacts, list) or not all(isinstance(item, dict) for item in artifacts):
        raise QueueProtocolError("artifacts must be an array of objects")
    for artifact in artifacts:
        _required_string(artifact, "path")
        _required_string(artifact, "sha256")
        size_bytes = artifact.get("size_bytes")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
            raise QueueProtocolError("artifact size_bytes must be a non-negative integer")
    return artifacts
