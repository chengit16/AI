"""以 Valkey 保存可重建策略版本见证，并拒绝陈旧或异常缓存。"""

from __future__ import annotations

from typing import Protocol, cast
from uuid import UUID

from redis import Redis
from redis.exceptions import RedisError

from ai_platform_api.modules.authorization.domain.policy import (
    PolicyVersionCacheStatus,
    PolicyVersionUnavailableError,
)

_VERIFY_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then
  redis.call('SET', KEYS[1], ARGV[1])
  return {'bootstrapped', ARGV[1]}
end
local observed = tonumber(current)
local source = tonumber(ARGV[1])
if not observed then
  return {'corrupt', current}
end
if observed == source then
  return {'fresh', current}
end
if observed < source then
  redis.call('SET', KEYS[1], ARGV[1])
  return {'stale_rejected', current}
end
return {'future_rejected', current}
"""


class PolicyVersionClient(Protocol):
    """约束版本见证所需的最小 Valkey 客户端能力。"""

    def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object: ...

    def close(self) -> None: ...


class ValkeyPolicyVersionGate:
    """原子比较数据库策略版本；不一致请求先拒绝，旧见证同时修复。"""

    def __init__(
        self,
        url: str | None = None,
        *,
        client: PolicyVersionClient | None = None,
        timeout_seconds: float = 0.5,
    ) -> None:
        if client is None and url is None:
            raise ValueError("策略版本见证必须配置 Valkey URL 或客户端")
        self._client = client or cast(
            PolicyVersionClient,
            Redis.from_url(
                str(url),
                decode_responses=True,
                socket_connect_timeout=timeout_seconds,
                socket_timeout=timeout_seconds,
            ),
        )

    def verify(self, workspace_id: UUID, source_version: int) -> PolicyVersionCacheStatus:
        """比较缓存与数据库版本；旧缓存修复后仍拒绝当前请求，禁止沿用旧上下文。"""

        if source_version < 1:
            raise PolicyVersionUnavailableError("invalid_source_version", source_version)
        try:
            raw_result = self._client.eval(
                _VERIFY_SCRIPT,
                1,
                self._key(workspace_id),
                source_version,
            )
        except RedisError as error:
            raise PolicyVersionUnavailableError("cache_unavailable", source_version) from error
        if not isinstance(raw_result, (list, tuple)) or not raw_result:
            raise PolicyVersionUnavailableError("cache_protocol_invalid", source_version)
        status = raw_result[0]
        if isinstance(status, bytes):
            status = status.decode("utf-8", errors="strict")
        if status in {"fresh", "bootstrapped"}:
            return cast("PolicyVersionCacheStatus", status)
        if status in {"stale_rejected", "future_rejected", "corrupt"}:
            raise PolicyVersionUnavailableError(str(status), source_version)
        raise PolicyVersionUnavailableError("cache_protocol_invalid", source_version)

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _key(workspace_id: UUID) -> str:
        return f"authorization-policy-version:v1:{workspace_id}"
