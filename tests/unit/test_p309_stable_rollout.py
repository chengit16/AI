"""验证 P3-09 固定百分位分桶和 Runtime current 缓存主动失效。"""

from uuid import UUID

import pytest
from ai_platform_api.modules.service_runtime.domain.models import route_assignment_bucket
from ai_platform_api.modules.service_runtime.infrastructure.valkey import (
    ValkeyRuntimeSnapshotCache,
)

SERVICE_ID = UUID("10000000-0000-4000-8000-000000000309")
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000309")


class FakeCacheClient:
    """仅记录缓存键，验证失效不依赖生产环境 KEYS 或 SCAN。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted: tuple[str, ...] = ()

    def get(self, key: str) -> object:
        return self.values.get(key)

    def set(self, key: str, value: str, *, ex: int) -> object:
        del ex
        self.values[key] = value
        return True

    def delete(self, *keys: str) -> object:
        self.deleted = keys
        for key in keys:
            self.values.pop(key, None)
        return len(keys)

    def close(self) -> None:
        return None


def test_assignment_bucket_is_stable_trimmed_and_well_distributed() -> None:
    """同一业务键重复解析不漂移，首尾空白不制造新主体，样本覆盖绝大多数百分位。"""

    expected = route_assignment_bucket(SERVICE_ID, "synthetic-account-309")
    assert route_assignment_bucket(SERVICE_ID, " synthetic-account-309 ") == expected
    assert {
        route_assignment_bucket(SERVICE_ID, f"synthetic-{index}") for index in range(2_000)
    } == set(range(100))
    with pytest.raises(ValueError):
        route_assignment_bucket(SERVICE_ID, "   ")
    with pytest.raises(ValueError):
        route_assignment_bucket(SERVICE_ID, "synthetic\naccount")


def test_current_cache_invalidation_deletes_all_buckets_and_legacy_key() -> None:
    """发布提交后固定删除 100 个百分位键及 P3-08 旧键，不触碰不可变 bound 键。"""

    client = FakeCacheClient()
    cache = ValkeyRuntimeSnapshotCache(client=client)
    legacy = cache.legacy_current_key(WORKSPACE_ID, SERVICE_ID)
    current_keys = {cache.current_key(WORKSPACE_ID, SERVICE_ID, bucket) for bucket in range(100)}
    bound_key = "runtime-bound:v1:synthetic-preserved"
    client.values = {key: "synthetic" for key in {legacy, *current_keys, bound_key}}

    cache.delete_current(WORKSPACE_ID, SERVICE_ID)

    assert set(client.deleted) == {legacy, *current_keys}
    assert client.values == {bound_key: "synthetic"}
