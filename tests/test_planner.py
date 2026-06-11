import json

import pytest

from brev_control_plane.planner import CpuFilter, PlanError, plan_fleet


def test_plan_fleet_records_workers_filters_and_safety_defaults():
    plan = plan_fleet(
        workers=3,
        cpu_filter=CpuFilter(min_vcpus=8, min_memory_gb=32, region="us-west"),
        name_prefix="batch",
    )

    assert plan["action"] == "plan"
    assert plan["workers"] == [
        {"index": 1, "name": "batch-001"},
        {"index": 2, "name": "batch-002"},
        {"index": 3, "name": "batch-003"},
    ]
    assert plan["filters"] == {
        "min_vcpus": 8,
        "min_memory_gb": 32,
        "region": "us-west",
    }
    assert plan["safety"] == {
        "creates_instances": False,
        "deletes_instances": False,
        "requires_confirmation": True,
    }
    json.dumps(plan)


@pytest.mark.parametrize("workers", [0, -1, True])
def test_plan_fleet_rejects_non_positive_worker_counts(workers):
    with pytest.raises(PlanError, match="workers must be a positive integer"):
        plan_fleet(workers=workers, cpu_filter=CpuFilter())


def test_plan_fleet_rejects_invalid_cpu_filters():
    with pytest.raises(PlanError, match="min_vcpus must be positive"):
        plan_fleet(workers=1, cpu_filter=CpuFilter(min_vcpus=0))

    with pytest.raises(PlanError, match="min_vcpus must be positive"):
        plan_fleet(workers=1, cpu_filter=CpuFilter(min_vcpus=True))

    with pytest.raises(PlanError, match="min_memory_gb must be positive"):
        plan_fleet(workers=1, cpu_filter=CpuFilter(min_memory_gb=-4))
