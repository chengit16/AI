"""验证 P2-09 策略版本见证、失败关闭和授权表面观测。"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.application.policy import RbacPolicyDecisionPoint
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.domain.grants import PolicySubject, RolePermissionGrant
from ai_platform_api.modules.authorization.domain.policy import (
    AuthorizationSurface,
    PolicyRequest,
    PolicyVersionCacheStatus,
    PolicyVersionUnavailableError,
    ResourceReference,
)
from ai_platform_api.modules.authorization.infrastructure.policy_version import (
    ValkeyPolicyVersionGate,
)
from redis.exceptions import RedisError

ROOT = Path(__file__).parents[1]
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000209")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000209")
ROLE_ID = UUID("70000000-0000-4000-8000-000000000209")
TRACE = TraceContext.continue_from("00-9123456789abcdef0123456789abcdef-9123456789abcdef-01")


class FakeVersionClient:
    """返回预设 Lua 结果，隔离单元测试与真实 Valkey。"""

    def __init__(self, result: object) -> None:
        self.result = result
        self.closed = False

    def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object:
        assert "stale_rejected" in script
        assert numkeys == 1
        assert keys_and_args == (f"authorization-policy-version:v1:{WORKSPACE_ID}", 9)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self) -> None:
        self.closed = True


class GrantReader:
    """提供固定角色版本与工作空间授权，测试只关注版本门禁。"""

    def resolve_subject(self, context: RequestContext) -> PolicySubject:
        assert context.workspace_id == WORKSPACE_ID
        return PolicySubject(ACCOUNT_ID, UUID(int=1), 9, frozenset({ROLE_ID}))

    def list_role_grants(
        self,
        workspace_id: UUID,
        role_ids: frozenset[UUID],
    ) -> tuple[RolePermissionGrant, ...]:
        assert workspace_id == WORKSPACE_ID and role_ids == frozenset({ROLE_ID})
        return (
            RolePermissionGrant(
                WORKSPACE_ID,
                ROLE_ID,
                "workspace.member.read",
                "workspace",
            ),
        )

    def expand_department_tree(
        self,
        workspace_id: UUID,
        department_ids: frozenset[UUID],
    ) -> frozenset[UUID]:
        assert workspace_id == WORKSPACE_ID
        return department_ids


class RejectingVersionGate:
    """模拟失效通知丢失后仍保留旧策略版本的缓存。"""

    def verify(self, workspace_id: UUID, source_version: int) -> PolicyVersionCacheStatus:
        assert workspace_id == WORKSPACE_ID and source_version == 9
        raise PolicyVersionUnavailableError("stale_rejected", source_version)

    def close(self) -> None:
        pass


def _context() -> RequestContext:
    return RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TRACE,
        authentication_method="browser_session",
    )


def _request(surface: AuthorizationSurface = "api") -> PolicyRequest:
    return PolicyRequest(
        context=_context(),
        permission_code="workspace.member.read",
        resource=ResourceReference("workspace_member", WORKSPACE_ID, WORKSPACE_ID),
        surface=surface,
    )


@pytest.mark.parametrize("status", ["fresh", "bootstrapped"])
def test_version_gate_accepts_only_current_or_new_witness(
    status: PolicyVersionCacheStatus,
) -> None:
    client = FakeVersionClient([status, "9"])
    gate = ValkeyPolicyVersionGate(client=client)

    assert gate.verify(WORKSPACE_ID, 9) == status
    gate.close()
    assert client.closed is True


@pytest.mark.parametrize(
    "result,reason",
    [
        (["stale_rejected", "8"], "stale_rejected"),
        (["future_rejected", "10"], "future_rejected"),
        (["corrupt", "invalid"], "corrupt"),
        (RedisError("synthetic unavailable"), "cache_unavailable"),
    ],
)
def test_version_gate_rejects_stale_future_corrupt_and_unavailable_cache(
    result: object,
    reason: str,
) -> None:
    gate = ValkeyPolicyVersionGate(client=FakeVersionClient(result))

    with pytest.raises(PolicyVersionUnavailableError) as captured:
        gate.verify(WORKSPACE_ID, 9)

    assert captured.value.reason_code == reason
    assert captured.value.source_version == 9


@pytest.mark.parametrize("surface", ["menu", "api", "retrieval", "field_projection"])
def test_all_authorization_surfaces_fail_closed_on_stale_policy_witness(
    surface: AuthorizationSurface,
) -> None:
    policy = RbacPolicyDecisionPoint(
        load_resource_registry(ROOT / "contracts" / "authorization" / "resource-registry.v1.json"),
        GrantReader(),
        version_gate=RejectingVersionGate(),
    )

    decision = policy.decide(_request(surface))

    assert decision.allowed is False
    assert decision.reason == "policy_unavailable"
    assert decision.policy_version == 9
