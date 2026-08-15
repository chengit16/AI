"""验证 P3-08 Runtime 发布装载、缓存降级与损坏恢复。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from ai_platform_api.config import Settings
from ai_platform_api.modules.service_runtime.application.errors import (
    AgentRuntimeReleaseRequiredError,
    RuntimeServiceRouteUnavailableError,
)
from ai_platform_api.modules.service_runtime.application.loader import RuntimeReleaseLoader
from ai_platform_api.modules.service_runtime.domain.models import (
    RuntimeReleaseSnapshot,
    RuntimeSourceUnavailableError,
    route_digest,
)
from ai_platform_api.modules.service_runtime.infrastructure.valkey import (
    ValkeyRuntimeSnapshotCache,
)

WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000308")
SERVICE_ID = UUID("20000000-0000-4000-8000-000000000308")
ROUTE_ID = UUID("30000000-0000-4000-8000-000000000308")
AGENT_ID = UUID("40000000-0000-4000-8000-000000000308")
RELEASE_ID = UUID("50000000-0000-4000-8000-000000000308")
CONFIG_ID = UUID("60000000-0000-4000-8000-000000000308")
POLICY_ID = UUID("70000000-0000-4000-8000-000000000308")
NOW = datetime(2026, 8, 16, tzinfo=UTC)


class FakeCacheClient:
    """保存 Valkey Adapter 写入值和租期，不模拟网络行为。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    def get(self, key: str) -> object:
        return self.values.get(key)

    def set(self, key: str, value: str, *, ex: int) -> object:
        self.values[key] = value
        self.expirations[key] = ex
        return True

    def delete(self, *keys: str) -> object:
        deleted = 0
        for key in keys:
            deleted += int(self.values.pop(key, None) is not None)
        return deleted

    def close(self) -> None:
        return None


class FakeSource:
    """可注入发布查询故障，并记录 Runtime 是否发生不必要回源。"""

    def __init__(self, snapshot: RuntimeReleaseSnapshot | None) -> None:
        self.snapshot = snapshot
        self.available = True
        self.current_calls = 0
        self.bound_calls = 0

    def get_current(
        self,
        workspace_id: UUID,
        service_id: UUID,
    ) -> RuntimeReleaseSnapshot | None:
        assert workspace_id == WORKSPACE_ID and service_id == SERVICE_ID
        self.current_calls += 1
        if not self.available:
            raise RuntimeSourceUnavailableError
        return self.snapshot

    def get_bound(
        self,
        workspace_id: UUID,
        service_id: UUID,
        route_id: UUID,
        service_route_version: int,
        agent_release_id: UUID,
    ) -> RuntimeReleaseSnapshot | None:
        assert (
            workspace_id,
            service_id,
            route_id,
            service_route_version,
            agent_release_id,
        ) == (WORKSPACE_ID, SERVICE_ID, ROUTE_ID, 1, RELEASE_ID)
        self.bound_calls += 1
        if not self.available:
            raise RuntimeSourceUnavailableError
        return self.snapshot


def snapshot() -> RuntimeReleaseSnapshot:
    value = RuntimeReleaseSnapshot(
        workspace_id=WORKSPACE_ID,
        service_id=SERVICE_ID,
        service_key="synthetic-runtime",
        service_status="active",
        access_policy_version_id=POLICY_ID,
        route_id=ROUTE_ID,
        service_route_version=1,
        route_mode="active",
        primary_release_id=RELEASE_ID,
        canary_release_id=None,
        canary_percent=0,
        previous_route_id=None,
        route_hash="",
        agent_id=AGENT_ID,
        agent_status="active",
        agent_release_id=RELEASE_ID,
        release_kind="system",
        release_version=1,
        release_status="released",
        runtime_config_version_id=CONFIG_ID,
        config_hash="a" * 64,
        release_snapshot=None,
        release_snapshot_hash=None,
        released_at=NOW,
    )
    return replace(value, route_hash=route_digest(value))


def loader() -> tuple[RuntimeReleaseLoader, FakeSource, FakeCacheClient]:
    source = FakeSource(snapshot())
    client = FakeCacheClient()
    cache = ValkeyRuntimeSnapshotCache(
        client=client,
        current_ttl_seconds=300,
        bound_ttl_seconds=86_400,
    )
    return RuntimeReleaseLoader(source, cache), source, client


def test_current_route_refreshes_cache_and_survives_control_plane_query_failure() -> None:
    runtime, source, client = loader()

    first = runtime.resolve_current(WORKSPACE_ID, SERVICE_ID)
    source.available = False
    during_outage = runtime.resolve_current(WORKSPACE_ID, SERVICE_ID)

    assert first == during_outage == snapshot()
    assert source.current_calls == 2
    assert sorted(client.expirations.values()) == [300, 86_400]


def test_bound_release_uses_immutable_cache_without_querying_control_plane() -> None:
    runtime, source, _ = loader()
    runtime.resolve_current(WORKSPACE_ID, SERVICE_ID)
    source.available = False

    bound = runtime.resolve_bound(WORKSPACE_ID, SERVICE_ID, ROUTE_ID, 1, RELEASE_ID)

    assert bound == snapshot()
    assert source.bound_calls == 0


def test_historical_bound_write_does_not_replace_current_route() -> None:
    _, _, client = loader()
    cache = ValkeyRuntimeSnapshotCache(client=client)
    current = snapshot()
    historical = replace(
        current,
        route_id=UUID("80000000-0000-4000-8000-000000000308"),
        service_route_version=2,
        agent_release_id=UUID("90000000-0000-4000-8000-000000000308"),
        primary_release_id=UUID("90000000-0000-4000-8000-000000000308"),
        route_hash="",
    )
    historical = replace(historical, route_hash=route_digest(historical))

    cache.put_current(current)
    cache.put_bound(historical)

    assert cache.get_current(WORKSPACE_ID, SERVICE_ID) == current
    assert (
        cache.get_bound(
            WORKSPACE_ID,
            SERVICE_ID,
            historical.route_id,
            historical.service_route_version,
            historical.agent_release_id,
        )
        == historical
    )


def test_corrupt_bound_cache_is_deleted_and_rebuilt_only_from_release_facts() -> None:
    runtime, source, client = loader()
    runtime.resolve_current(WORKSPACE_ID, SERVICE_ID)
    key = ValkeyRuntimeSnapshotCache.bound_key(WORKSPACE_ID, SERVICE_ID, ROUTE_ID, 1, RELEASE_ID)
    client.values[key] = "{}"

    recovered = runtime.resolve_bound(WORKSPACE_ID, SERVICE_ID, ROUTE_ID, 1, RELEASE_ID)

    assert recovered == snapshot()
    assert source.bound_calls == 1
    assert client.values[key] != "{}"


def test_misbound_valid_cache_envelope_is_deleted_and_rebuilt_from_source() -> None:
    runtime, source, client = loader()
    runtime.resolve_current(WORKSPACE_ID, SERVICE_ID)
    expected_key = ValkeyRuntimeSnapshotCache.bound_key(
        WORKSPACE_ID,
        SERVICE_ID,
        ROUTE_ID,
        1,
        RELEASE_ID,
    )
    wrong = replace(
        snapshot(),
        route_id=UUID("a0000000-0000-4000-8000-000000000308"),
    )
    cache = ValkeyRuntimeSnapshotCache(client=client)
    cache.put_bound(wrong)
    wrong_key = ValkeyRuntimeSnapshotCache.bound_key(
        wrong.workspace_id,
        wrong.service_id,
        wrong.route_id,
        wrong.service_route_version,
        wrong.agent_release_id,
    )
    client.values[expected_key] = client.values[wrong_key]

    recovered = runtime.resolve_bound(WORKSPACE_ID, SERVICE_ID, ROUTE_ID, 1, RELEASE_ID)

    assert recovered == snapshot()
    assert source.bound_calls == 1
    assert cache.get_bound(WORKSPACE_ID, SERVICE_ID, ROUTE_ID, 1, RELEASE_ID) == snapshot()


def test_invalid_or_unavailable_release_fails_closed_without_draft_fallback() -> None:
    runtime, source, client = loader()
    source.snapshot = replace(snapshot(), service_status="suspended")
    with pytest.raises(AgentRuntimeReleaseRequiredError):
        runtime.resolve_current(WORKSPACE_ID, SERVICE_ID)

    source.snapshot = snapshot()
    source.available = False
    client.values.clear()
    with pytest.raises(RuntimeServiceRouteUnavailableError):
        runtime.resolve_current(WORKSPACE_ID, SERVICE_ID)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"runtime_current_cache_ttl_seconds": 59}, "当前 Route 缓存租期"),
        ({"runtime_current_cache_ttl_seconds": 901}, "当前 Route 缓存租期"),
        ({"runtime_bound_cache_ttl_seconds": 3_599}, "精确绑定缓存租期"),
        ({"runtime_bound_cache_ttl_seconds": 604_801}, "精确绑定缓存租期"),
        ({"runtime_cache_timeout_seconds": 0.01}, "Runtime 缓存超时"),
        ({"runtime_cache_timeout_seconds": 5.01}, "Runtime 缓存超时"),
    ],
)
def test_runtime_cache_configuration_rejects_unbounded_values(
    overrides: dict[str, object],
    message: str,
) -> None:
    """缓存租期和网络等待必须有界，避免故障时无限陈旧或阻塞请求。"""

    with pytest.raises(ValueError, match=message):
        Settings.model_validate({"environment": "test", **overrides})
