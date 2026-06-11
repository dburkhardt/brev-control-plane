from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class PlanError(ValueError):
    """Raised when a fleet plan request is invalid."""


@dataclass(frozen=True)
class CpuFilter:
    min_vcpus: int | None = None
    min_memory_gb: int | None = None
    region: str | None = None

    def validate(self) -> None:
        if self.min_vcpus is not None and not _is_positive_int(self.min_vcpus):
            raise PlanError("min_vcpus must be positive")
        if self.min_memory_gb is not None and not _is_positive_int(
            self.min_memory_gb
        ):
            raise PlanError("min_memory_gb must be positive")
        if self.region is not None and not self.region.strip():
            raise PlanError("region must be non-empty when provided")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        payload: dict[str, Any] = {}
        if self.min_vcpus is not None:
            payload["min_vcpus"] = self.min_vcpus
        if self.min_memory_gb is not None:
            payload["min_memory_gb"] = self.min_memory_gb
        if self.region is not None:
            payload["region"] = self.region
        return payload


def plan_fleet(
    *,
    workers: int,
    cpu_filter: CpuFilter,
    name_prefix: str = "worker",
) -> dict[str, Any]:
    if not _is_positive_int(workers):
        raise PlanError("workers must be a positive integer")
    if not name_prefix.strip():
        raise PlanError("name_prefix must be non-empty")

    return {
        "action": "plan",
        "workers": [
            {"index": index, "name": f"{name_prefix}-{index:03d}"}
            for index in range(1, workers + 1)
        ],
        "filters": cpu_filter.to_dict(),
        "safety": {
            "creates_instances": False,
            "deletes_instances": False,
            "requires_confirmation": True,
        },
    }


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
