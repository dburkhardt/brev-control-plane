from brev_control_plane.state import StateStore


def test_state_store_persists_instances_and_events(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.initialize()

    store.upsert_instances(
        [
            {"id": "inst-1", "name": "worker-a", "status": "running"},
            {"id": "inst-2", "name": "worker-b", "status": "stopped"},
        ]
    )
    store.record_event("inventory.refresh", {"count": 2})

    assert store.list_instances() == [
        {"id": "inst-1", "name": "worker-a", "status": "running"},
        {"id": "inst-2", "name": "worker-b", "status": "stopped"},
    ]
    events = store.list_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "inventory.refresh"
    assert events[0]["payload"] == {"count": 2}


def test_state_store_records_live_event_with_instance_name(tmp_path):
    store = StateStore(tmp_path / "state.db")

    event_id = store.record_live_event(
        "fleet.exec.completed",
        instance_name="smoke-001",
        payload={"command": "uptime", "output": "ok"},
    )

    events = store.list_events()
    assert event_id == 1
    assert events[0]["event_type"] == "fleet.exec.completed"
    assert events[0]["payload"] == {
        "instance_name": "smoke-001",
        "command": "uptime",
        "output": "ok",
    }
