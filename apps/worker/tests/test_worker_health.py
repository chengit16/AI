from ai_platform_worker.health import get_worker_health


def test_worker_health() -> None:
    health = get_worker_health()

    assert health.service == "ai-platform-worker"
    assert health.status == "ok"
