"""验证 P3-11 Agent 与服务控制台 HTTP、状态恢复和 PostgreSQL 闭环。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast
from uuid import UUID

import pytest
from ai_platform_api.modules.agent_control.api.routes import router as agent_router
from ai_platform_api.modules.agent_control.application.service import AgentControlService
from ai_platform_api.modules.agent_control.infrastructure.deterministic_evaluation import (
    LocalDeterministicEvaluationExecutor,
)
from ai_platform_api.modules.agent_control.infrastructure.sqlalchemy import (
    SqlAlchemyAgentControlUnitOfWork,
)
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.service_governance.api.routes import router as service_router
from ai_platform_api.modules.service_governance.application.service import (
    ServiceGovernanceService,
)
from ai_platform_api.modules.service_governance.domain.models import ServicePromotionEvidence
from ai_platform_api.modules.service_governance.infrastructure.sqlalchemy import (
    SqlAlchemyServiceGovernanceUnitOfWork,
)
from ai_platform_api.modules.workflow.domain.approval_runtime import ApprovalRuntimeCommand
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.integration.test_p304_agent_evaluation_postgres import (
    RegisteredAccount,
    context,
    ensure_runtime_configuration,
)
from tests.integration.test_p305_agent_approval_postgres import (
    ApprovalHarness,
    approval_harness,
    register,
)


@pytest.fixture(scope="module")
def console_database() -> Iterator[ApprovalHarness]:
    """为控制台 HTTP 全链路创建独立临时 Schema。"""

    yield from approval_harness()


def _agent_service(harness: ApprovalHarness) -> AgentControlService:
    """装配与本地应用相同的固定规则评估和通用审批运行时。"""

    return AgentControlService(
        SqlAlchemyAgentControlUnitOfWork(harness.sessions),
        LocalDeterministicEvaluationExecutor(),
        harness.approvals,
    )


class PassingPromotionGate:
    """让 P3-11 隔离验证控制台路由操作，真实运营规则由 P3-12 专项覆盖。"""

    def evaluate_promotion(self, **_: object) -> ServicePromotionEvidence:
        return ServicePromotionEvidence(True, "synthetic-p311", "a" * 64, ())


def _publish_release(
    client: TestClient,
    harness: ApprovalHarness,
    owner: RegisteredAccount,
    agent_id: str,
    revision: int,
    suffix: str,
) -> dict[str, object]:
    """通过真实 HTTP 推进候选、测试、审批和发布，并由通用审批接口完成决策。"""

    # 1. 浏览器只能冻结当前 revision，不能提交测试观测或审批结论。
    candidate_response = client.post(
        f"/api/v1/workspaces/{owner.workspace_id}/agents/{agent_id}/release-requests",
        headers={"Idempotency-Key": f"synthetic-p311-candidate-{suffix}"},
        json={"expected_revision": revision},
    )
    assert candidate_response.status_code == 201
    candidate = candidate_response.json()
    evaluation_response = client.post(
        f"/api/v1/workspaces/{owner.workspace_id}/agents/{agent_id}/release-requests/"
        f"{candidate['candidate_id']}/evaluations",
    )
    assert evaluation_response.status_code == 200
    assert evaluation_response.json()["status"] == "passed"
    assert len(evaluation_response.json()["checks"]) == 5

    # 2. Agent 入口只发起审批，实际人工动作继续复用通用审批运行时。
    approval_response = client.post(
        f"/api/v1/workspaces/{owner.workspace_id}/agents/{agent_id}/release-requests/"
        f"{candidate['candidate_id']}/approval",
        headers={"Idempotency-Key": f"synthetic-p311-approval-{suffix}"},
    )
    assert approval_response.status_code == 200
    approval = approval_response.json()
    approved = harness.approvals.act(
        context(owner),
        approval_instance_id=UUID(approval["approval_instance_id"]),
        command=ApprovalRuntimeCommand(
            "approve",
            owner.account_id,
            f"synthetic-p311-approve-{suffix}",
        ),
    )
    assert approved.state.instance.status == "approved"

    # 3. 发布接口只固化已通过测试与审批的候选，并返回不可变摘要。
    release_response = client.post(
        f"/api/v1/workspaces/{owner.workspace_id}/agents/{agent_id}/release-requests/"
        f"{candidate['candidate_id']}/publish",
        headers={"Idempotency-Key": f"synthetic-p311-publish-{suffix}"},
    )
    assert release_response.status_code == 200
    payload = release_response.json()
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def _update_draft(
    client: TestClient,
    owner: RegisteredAccount,
    agent_id: str,
    configuration: dict[str, object],
    revision: int,
) -> int:
    """按当前 revision 通过 HTTP 生成下一草稿修订。"""

    response = client.put(
        f"/api/v1/workspaces/{owner.workspace_id}/agents/{agent_id}/draft",
        headers={"Idempotency-Key": f"synthetic-p311-draft-{revision}"},
        json={"expected_revision": revision, "configuration": configuration},
    )
    assert response.status_code == 200
    assert response.json()["revision"] == revision + 1
    return int(response.json()["revision"])


def test_agent_and_service_console_http_release_flow(
    console_database: ApprovalHarness,
) -> None:
    """控制台可恢复完整流水，服务 Route 只追加且并发 generation 始终由服务端确认。"""

    # 长函数保留原因: 同一浏览器会话必须连续证明 Agent 发布证据可被服务灰度链安全消费。
    owner = register(console_database, "console-owner")
    owner_context = context(owner)
    agents = _agent_service(console_database)
    services = ServiceGovernanceService(
        SqlAlchemyServiceGovernanceUnitOfWork(console_database.sessions),
        promotion_gate=PassingPromotionGate(),
    )
    ensure_runtime_configuration(console_database.sessions, owner.account_id)
    application = FastAPI()
    application.state.agent_control_service = agents
    application.state.service_governance_service = services
    application.include_router(agent_router, prefix="/api/v1")
    application.include_router(service_router, prefix="/api/v1")
    application.dependency_overrides[trusted_request_context] = lambda: owner_context

    with TestClient(application) as client:
        # 1. 从 Agent 创建开始完成两次独立 Release，并验证刷新可恢复候选与发布历史。
        created = client.post(
            f"/api/v1/workspaces/{owner.workspace_id}/agents",
            headers={"Idempotency-Key": "synthetic-p311-agent-create"},
            json={
                "name": "P3-11 合成制度 Agent",
                "description": "仅用于控制台 HTTP 验收",
                "use_starter_configuration": True,
            },
        )
        assert created.status_code == 201
        agent_id = created.json()["agent"]["agent_id"]
        configuration = cast(dict[str, object], created.json()["draft"]["configuration"])
        assert set(configuration) == {
            "prompt_version_id",
            "runtime_config_version_id",
            "knowledge_scope_version_ids",
            "workflow_release_id",
            "read_only_tools",
            "output_schema_version_id",
            "safety_policy_version_id",
            "limits",
        }
        revision = _update_draft(client, owner, agent_id, configuration, 1)
        first_release = _publish_release(
            client,
            console_database,
            owner,
            agent_id,
            revision,
            "first",
        )
        revision = _update_draft(client, owner, agent_id, configuration, revision)
        second_release = _publish_release(
            client,
            console_database,
            owner,
            agent_id,
            revision,
            "second",
        )
        listed_agents = client.get(f"/api/v1/workspaces/{owner.workspace_id}/agents")
        listed_candidates = client.get(
            f"/api/v1/workspaces/{owner.workspace_id}/agents/{agent_id}/release-requests"
        )
        listed_releases = client.get(
            f"/api/v1/workspaces/{owner.workspace_id}/agents/{agent_id}/releases"
        )
        assert listed_agents.status_code == 200
        assert listed_candidates.status_code == 200
        assert listed_releases.status_code == 200
        assert len(listed_candidates.json()["items"]) == 2
        assert all(
            item["evaluation"]["status"] == "passed" for item in listed_candidates.json()["items"]
        )
        assert [item["version"] for item in listed_releases.json()["items"]] == [2, 1]

        # 2. 首个 Release 创建稳定服务；暂停与恢复只更新服务状态，不篡改当前 Route。
        created_service = client.post(
            f"/api/v1/workspaces/{owner.workspace_id}/services",
            headers={"Idempotency-Key": "synthetic-p311-service-create"},
            json={
                "name": "P3-11 合成制度服务",
                "release_id": first_release["release_id"],
                "service_type": "custom_knowledge_agent",
                "visibility": "workspace",
                "allowed_department_ids": [],
                "allowed_account_ids": [],
            },
        )
        assert created_service.status_code == 201
        deployment = created_service.json()
        service_id = deployment["service"]["service_id"]
        expected_version = int(deployment["service"]["version"])
        for target_status in ("suspended", "active"):
            updated = client.put(
                f"/api/v1/workspaces/{owner.workspace_id}/services/{service_id}",
                headers={"Idempotency-Key": f"synthetic-p311-service-{target_status}"},
                json={
                    "expected_version": expected_version,
                    "target_status": target_status,
                    "visibility": "workspace",
                    "allowed_department_ids": [],
                    "allowed_account_ids": [],
                },
            )
            assert updated.status_code == 200
            assert updated.json()["service"]["status"] == target_status
            expected_version = int(updated.json()["service"]["version"])

        # 3. 第二个 Release 依次进入灰度、晋级和回滚，每次只追加 Route 并推进 generation。
        canary = client.post(
            f"/api/v1/workspaces/{owner.workspace_id}/services/{service_id}/routes/canary",
            headers={"Idempotency-Key": "synthetic-p311-service-canary"},
            json={
                "release_id": second_release["release_id"],
                "canary_percent": 10,
                "expected_generation": 1,
            },
        )
        assert canary.status_code == 200
        assert canary.json()["route"]["route_mode"] == "canary"
        promoted = client.post(
            f"/api/v1/workspaces/{owner.workspace_id}/services/{service_id}/routes/promote",
            headers={"Idempotency-Key": "synthetic-p311-service-promote"},
            json={
                "release_id": second_release["release_id"],
                "expected_generation": 2,
            },
        )
        assert promoted.status_code == 200
        assert promoted.json()["route"]["primary_release_id"] == second_release["release_id"]
        rolled_back = client.post(
            f"/api/v1/workspaces/{owner.workspace_id}/services/{service_id}/routes/rollback",
            headers={"Idempotency-Key": "synthetic-p311-service-rollback"},
            json={"expected_generation": 3},
        )
        assert rolled_back.status_code == 200
        assert rolled_back.json()["route"]["route_mode"] == "rollback"
        listed_services = client.get(f"/api/v1/workspaces/{owner.workspace_id}/services")
        assert listed_services.status_code == 200
        assert listed_services.json()["items"][0]["publication"]["generation"] == 4
