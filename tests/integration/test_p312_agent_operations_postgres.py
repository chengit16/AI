"""验证 P3-12 运营聚合在真实 PostgreSQL 中保持服务和 Release 隔离。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from ai_platform_api.modules.agent_operations.application.service import AgentOperationsService
from ai_platform_api.modules.agent_operations.infrastructure.sqlalchemy import (
    SqlAlchemyAgentOperationsUnitOfWork,
)
from ai_platform_api.modules.service_governance.application.service import (
    ServiceGovernanceService,
)
from ai_platform_api.modules.service_governance.infrastructure.sqlalchemy import (
    SqlAlchemyServiceGovernanceUnitOfWork,
)
from ai_platform_api.persistence.tables import model_invocations
from sqlalchemy import insert

from tests.integration.test_p1e01_assistant_postgres import (
    AssistantHarness,
    context,
    publish_runtime_config,
    register,
)
from tests.integration.test_p308_runtime_isolation_postgres import runtime_harness


@pytest.fixture(scope="module")
def operations_database() -> Iterator[AssistantHarness]:
    """使用独立 Schema 生成两版系统 Release 与真实服务 Run。"""

    yield from runtime_harness()


def test_postgres_aggregates_runs_invocations_feedback_and_previous_release(
    operations_database: AssistantHarness,
) -> None:
    """成本按 Trace 归属 Run，反馈按 Run 归属 Release，私有正文不进入报告。"""

    # 长函数保留原因: 两版发布、运行、模型调用和反馈必须连续落在同一真实 Schema。
    harness = operations_database
    owner = register(harness, "agent-operations")
    owner_context = context(owner)

    # 1. 第一版完成一次服务 Run，并记录一个人工反馈作为历史对比样本。
    publish_runtime_config(harness, owner.account_id, version=1)
    first_conversation = harness.assistant.create_conversation(
        owner_context,
        title="合成运营第一版",
    )
    first_submission = harness.assistant.create_user_message(
        owner_context,
        conversation_id=first_conversation.conversation_id,
        texts=("合成运营第一版问题",),
        idempotency_key="synthetic-p312-first",
    )
    first_claimed = harness.assistant.claim_run(
        owner_context,
        run_id=first_submission.run.run_id,
    )
    assert first_claimed is not None and first_claimed.assistant_message_id is not None
    harness.assistant.complete_run(
        owner_context,
        run_id=first_claimed.run_id,
        text="合成运营第一版答案",
    )
    harness.assistant.submit_feedback(
        owner_context,
        conversation_id=first_conversation.conversation_id,
        message_id=first_claimed.assistant_message_id,
        rating="helpful",
        issue_codes=(),
        comment=None,
    )

    # 2. 第二版 Run 绑定当前 Route，并写入同 Trace 的降级模型计量事实。
    publish_runtime_config(harness, owner.account_id, version=2)
    second_conversation = harness.assistant.create_conversation(
        owner_context,
        title="合成运营第二版",
    )
    second_submission = harness.assistant.create_user_message(
        owner_context,
        conversation_id=second_conversation.conversation_id,
        texts=("合成运营第二版问题",),
        idempotency_key="synthetic-p312-second",
    )
    second_claimed = harness.assistant.claim_run(
        owner_context,
        run_id=second_submission.run.run_id,
    )
    assert second_claimed is not None
    harness.assistant.complete_run(
        owner_context,
        run_id=second_claimed.run_id,
        text="合成运营第二版答案",
    )
    now = datetime.now(UTC)
    with harness.sessions.begin() as session:
        session.execute(
            insert(model_invocations).values(
                invocation_id=uuid4(),
                workspace_id=owner.workspace_id,
                runtime_config_version_id=second_claimed.runtime_config_version_id,
                task_type="service_invocation",
                security_level="INTERNAL",
                status="degraded",
                trace_id=second_claimed.trace_id,
                traceparent=second_claimed.traceparent,
                external_data_allowed=False,
                requested_max_output_tokens=128,
                selected_route_id=None,
                selected_provider_id=None,
                selected_model_id=None,
                input_tokens=20,
                output_tokens=10,
                estimated_cost_microunits=2_500,
                currency="CNY",
                finish_reason="stop",
                degradation_reason="SYNTHETIC_FALLBACK",
                error_code=None,
                started_at=now,
                completed_at=now,
            )
        )

    # 3. 当前正式 Route 与上一版本在一个脱敏报告中返回，指标不会跨空间或跨版本混算。
    management = replace(owner_context, authorized_workspace=True)
    deployment = ServiceGovernanceService(
        SqlAlchemyServiceGovernanceUnitOfWork(harness.sessions)
    ).list_services(management)[0]
    operations_context = replace(
        owner_context,
        authorized_workspace=True,
        authorized_permission_code="agent.operations.read",
    )
    report = AgentOperationsService(
        SqlAlchemyAgentOperationsUnitOfWork(harness.sessions)
    ).get_report(
        operations_context,
        workspace_id=owner.workspace_id,
        service_id=deployment.service.service_id,
        now=now,
    )

    assert report.primary.release_id == second_claimed.agent_release_id
    assert report.primary.run_count == 1
    assert report.primary.degradation_rate_bps == 10_000
    assert report.primary.total_cost_microunits == 2_500
    assert report.comparison is not None
    assert report.comparison.role == "previous"
    assert report.comparison.release_id == first_claimed.agent_release_id
    assert report.comparison.feedback_count == 1
    assert report.comparison.helpful_rate_bps == 10_000
