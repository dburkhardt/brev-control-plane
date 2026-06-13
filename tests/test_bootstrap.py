from brev_control_plane.bootstrap import render_worker_bootstrap


def test_bootstrap_renderer_launches_worker_with_token_env_reference_only():
    script = render_worker_bootstrap(
        repo_url="https://example.invalid/org/repo.git",
        server_url="https://queue.example.invalid",
        token_env_name="QUEUE_TOKEN",
        worker_name="worker-001",
    )

    assert "git clone https://example.invalid/org/repo.git" in script
    assert "python3 -m pip install -e" in script
    assert "--server-url https://queue.example.invalid" in script
    assert "--token-env QUEUE_TOKEN" in script
    assert "--worker-id worker-001" in script
    assert "QUEUE_TOKEN" in script
    assert ': "${QUEUE_TOKEN:?required}"' in script
    assert "secret-token" not in script
