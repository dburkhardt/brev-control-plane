from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
from typing import Any, Callable, Protocol


class CommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


class BrevCommandError(RuntimeError):
    """Raised when the Brev CLI fails or returns malformed output."""


@dataclass
class BrevClient:
    binary: str = "brev"
    runner: Callable[[list[str]], CommandResult] | None = None

    def list_instances(self) -> list[dict[str, Any]]:
        return self._run_json(["ls", "--json"])

    def search_cpu(self) -> list[dict[str, Any]]:
        return self._run_json(["search", "cpu", "--json"])

    def _run_json(self, args: list[str]) -> list[dict[str, Any]]:
        result = self._run(args)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise BrevCommandError(f"invalid JSON from brev: {exc}") from exc

        if not isinstance(payload, list):
            raise BrevCommandError("expected brev to return a JSON array")
        if not all(isinstance(item, dict) for item in payload):
            raise BrevCommandError("expected brev JSON array entries to be objects")
        return payload

    def _run(self, args: list[str]) -> CommandResult:
        argv = [self.binary, *args]
        if self.runner is None:
            try:
                result = subprocess.run(
                    argv,
                    capture_output=True,
                    check=False,
                    text=True,
                )
            except FileNotFoundError as exc:
                raise BrevCommandError(f"brev binary not found: {self.binary}") from exc
        else:
            result = self.runner(argv)

        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no output"
            raise BrevCommandError(f"brev command failed: {' '.join(argv)}: {detail}")
        return result
