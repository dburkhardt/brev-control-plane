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
