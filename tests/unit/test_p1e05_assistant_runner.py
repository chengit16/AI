"""验证 P1E-05 问答执行器的单次认领、冻结配置和事件终态。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.assistant.application.runner import AssistantRunExecutor
from ai_platform_api.modules.assistant.application.service import AssistantConversationService
from ai_platform_api.modules.assistant.domain.models import AssistantRun
from ai_platform_api.modules.model_gateway.application.context import (
    AuthorizedModelContextBuilder,
)
from ai_platform_api.modules.model_gateway.application.runtime_gateway import (
    RuntimeModelGatewayService,
)
from ai_platform_api.modules.model_gateway.domain.models import (
    ModelMessage,
    ModelRequest,
    ModelResult,
    TokenUsage,
)
from ai_platform_api.modules.model_gateway.domain.runtime import (
    AiRuntimeConfigVersion,
    RuntimeConfigurationReader,
)
from ai_platform_api.modules.retrieval.application.evidence import RetrievalEvidenceService
from ai_platform_api.modules.retrieval.application.planning import (
    BoundedRetrievalPlanningService,
)
from ai_platform_api.modules.retrieval.domain.evidence import EvidenceSetSnapshot
from ai_platform_api.modules.retrieval.domain.planning import RetrievalPlanSnapshot
from ai_platform_api.modules.service_runtime.application import RuntimeReleaseLoader
from ai_platform_api.modules.service_runtime.domain.models import RuntimeReleaseSnapshot
from ai_platform_api.modules.streaming.application.service import TransactionalStreamService

WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000505")
ACCOUNT_ID = UUID("20000000-0000-4000-8000-000000000505")
RUN_ID = UUID("30000000-0000-4000-8000-000000000505")
CONFIG_ID = UUID("40000000-0000-4000-8000-000000000505")
SERVICE_ID = UUID("50000000-0000-4000-8000-000000000505")
ROUTE_ID = UUID("60000000-0000-4000-8000-000000000505")
NOW = datetime(2026, 8, 15, tzinfo=UTC)


class FakeConversations:
    """以内存状态模拟 AssistantRun 认领和消息终态。"""

    def __init__(self, run: AssistantRun) -> None:
        self.run = run
        self.answer: str | None = None

    def claim_run(self, context: RequestContext, *, run_id: UUID) -> AssistantRun | None:
        assert context.workspace_id == self.run.workspace_id and run_id == self.run.run_id
        if self.run.status != "queued":
            return None
        self.run = replace(self.run, status="running")
        return self.run

    def complete_run(
        self,
        context: RequestContext,
        *,
        run_id: UUID,
        text: str,
    ) -> AssistantRun:
        assert context.workspace_id == self.run.workspace_id and run_id == self.run.run_id
        self.answer = text
        self.run = replace(self.run, status="completed", completed_at=NOW)
        return self.run

    def fail_run(
        self,
        context: RequestContext,
        *,
        run_id: UUID,
        error_code: str,
    ) -> AssistantRun:
        assert context.workspace_id == self.run.workspace_id and run_id == self.run.run_id
        self.run = replace(self.run, status="failed", completed_at=NOW, error_code=error_code)
        return self.run


class FakePlanning:
    """返回已冻结的合成检索计划。"""

    def __init__(self, plan: RetrievalPlanSnapshot) -> None:
        self.plan = plan

    def retrieve(self, context: RequestContext, run_id: UUID) -> RetrievalPlanSnapshot:
        assert context.workspace_id == WORKSPACE_ID and run_id == RUN_ID
        return self.plan


class FakeEvidence:
    """返回已通过授权复核的合成证据集。"""

    def __init__(self, evidence: EvidenceSetSnapshot) -> None:
        self.evidence = evidence

    def prepare(self, context: RequestContext, run_id: UUID) -> EvidenceSetSnapshot:
        assert context.workspace_id == WORKSPACE_ID and run_id == RUN_ID
        return self.evidence


class FakeConfigurations:
    """记录执行器读取的冻结配置标识。"""

    def __init__(self, configuration: AiRuntimeConfigVersion) -> None:
        self.configuration = configuration
        self.requested_ids: list[UUID] = []

    def get(self, runtime_config_version_id: UUID) -> AiRuntimeConfigVersion | None:
        self.requested_ids.append(runtime_config_version_id)
        return self.configuration if runtime_config_version_id == CONFIG_ID else None


class FakeRuntimeReleases:
    """证明执行器按 Run 冻结的服务 Route 装载唯一 Release。"""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID, UUID, int, UUID]] = []

    def resolve_bound(
        self,
        workspace_id: UUID,
        service_id: UUID,
        route_id: UUID,
        service_route_version: int,
        agent_release_id: UUID,
    ) -> RuntimeReleaseSnapshot:
        self.calls.append(
            (workspace_id, service_id, route_id, service_route_version, agent_release_id)
        )
        return cast(
            RuntimeReleaseSnapshot,
            SimpleNamespace(runtime_config_version_id=CONFIG_ID),
        )


class FakeModelRuntime:
    """记录模型调用显式携带的配置版本并返回确定性文本。"""

    def __init__(self) -> None:
        self.configuration_ids: list[UUID] = []
        self.requests: list[ModelRequest] = []

    def invoke(
        self,
        request: ModelRequest,
        *,
        runtime_config_version_id: UUID,
    ) -> ModelResult:
        self.requests.append(request)
        self.configuration_ids.append(runtime_config_version_id)
        return ModelResult(
            content="合成模型回答",
            finish_reason="stop",
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            usage=TokenUsage(12, 4),
            estimated_cost_microunits=16,
            currency="CNY",
            degraded=False,
            degradation_reason=None,
            attempts=(),
            runtime_config_version_id=runtime_config_version_id,
        )


class FakeModelContext:
    """模拟已完成字段投影和安全检查的模型消息构造器。"""

    def build_user_message(self, query: str) -> ModelMessage:
        return ModelMessage("user", query)

    def build_untrusted_evidence_message(
        self,
        *,
        resource_type: str,
        payload: dict[str, object],
        field_mask: frozenset[str],
    ) -> ModelMessage:
        assert resource_type == "chunk" and not field_mask
        return ModelMessage(
            "user", f"<untrusted_evidence>{payload['content']}</untrusted_evidence>"
        )


class FakeStreams:
    """记录恢复 Run、事件及最终快照，不模拟数据库内部实现。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []
        self.finished: tuple[str, dict[str, object] | None] | None = None
        self.started = 0

    def start_run(self, *args: object, now: datetime) -> object:
        del args, now
        self.started += 1
        return object()

    def append(
        self,
        run_id: UUID,
        event_type: str,
        trace_id: str,
        traceparent: str,
        payload: dict[str, object],
        *,
        now: datetime,
    ) -> object:
        del trace_id, traceparent, now
        assert run_id == RUN_ID
        self.events.append((event_type, payload))
        return object()

    def finish(
        self,
        run_id: UUID,
        status: str,
        final_payload: dict[str, object] | None,
        *,
        now: datetime,
    ) -> object:
        del now
        assert run_id == RUN_ID
        self.finished = (status, final_payload)
        return object()


def test_executor_uses_frozen_configuration_and_duplicate_schedule_does_not_generate() -> None:
    # 长函数保留原因: 该回归需要在一个场景中同时证明认领、冻结配置和 SSE 终态关系。
    # 1. 构造固定 Run、检索计划和通过安全边界的单条合成证据。
    run = AssistantRun(
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        conversation_id=uuid4(),
        user_message_id=uuid4(),
        assistant_message_id=uuid4(),
        service_id=SERVICE_ID,
        service_route_id=ROUTE_ID,
        service_route_version=1,
        agent_release_id=uuid4(),
        runtime_config_version_id=CONFIG_ID,
        requested_by_account_id=ACCOUNT_ID,
        requested_by_actor_id=ACCOUNT_ID,
        status="queued",
        idempotency_key="synthetic-idempotency",
        request_hash="a" * 64,
        trace_id="b" * 32,
        traceparent="00-" + "b" * 32 + "-" + "c" * 16 + "-01",
        created_at=NOW,
        updated_at=NOW,
        completed_at=None,
        error_code=None,
    )
    plan = cast(
        RetrievalPlanSnapshot,
        SimpleNamespace(
            variants=(SimpleNamespace(text="合成问题"),),
            candidates=(),
            maximum_security_level="RESTRICTED",
            field_mask=frozenset(),
        ),
    )
    item = SimpleNamespace(
        rank=1,
        document_id=uuid4(),
        document_version_id=uuid4(),
        chunk_id=uuid4(),
        content_hash="d" * 64,
        quote="合成引用",
        context_text="合成证据正文",
        source_position={"page": 1},
        document_title="合成制度",
        security_level="INTERNAL",
    )
    evidence = cast(
        EvidenceSetSnapshot,
        SimpleNamespace(status="sufficient", degradation_reason=None, items=(item,)),
    )
    configuration = cast(
        AiRuntimeConfigVersion,
        SimpleNamespace(
            runtime_config_version_id=CONFIG_ID,
            system_prompt_template="只依据授权证据回答。",
            policy=SimpleNamespace(max_prompt_characters=4_000, max_output_tokens=128),
        ),
    )
    # 2. 装配全部内存端口；调用次数由 Fake 记录，避免测试依赖真实供应商。
    conversations = FakeConversations(run)
    configurations = FakeConfigurations(configuration)
    runtime_releases = FakeRuntimeReleases()
    model_runtime = FakeModelRuntime()
    streams = FakeStreams()
    executor = AssistantRunExecutor(
        cast(AssistantConversationService, conversations),
        cast(BoundedRetrievalPlanningService, FakePlanning(plan)),
        cast(RetrievalEvidenceService, FakeEvidence(evidence)),
        cast(RuntimeReleaseLoader, runtime_releases),
        cast(RuntimeConfigurationReader, configurations),
        cast(RuntimeModelGatewayService, model_runtime),
        cast(AuthorizedModelContextBuilder, FakeModelContext()),
        cast(TransactionalStreamService, streams),
        delta_batch_characters=64,
    )
    context = RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TraceContext("b" * 32, "c" * 16),
        authentication_method="browser_session",
    )

    # 3. 重复调度只能命中状态短路，模型和流恢复事实均只能创建一次。
    first = executor.execute(context, RUN_ID)
    repeated = executor.execute(context, RUN_ID)

    assert first.claimed is True and first.status == "completed"
    assert repeated.claimed is False
    assert configurations.requested_ids == [CONFIG_ID]
    assert runtime_releases.calls == [
        (WORKSPACE_ID, SERVICE_ID, ROUTE_ID, 1, conversations.run.agent_release_id)
    ]
    assert model_runtime.configuration_ids == [CONFIG_ID]
    assert model_runtime.requests[0].security_level == "INTERNAL"
    assert conversations.answer == "合成模型回答"
    assert streams.started == 1
    assert [event_type for event_type, _ in streams.events] == [
        "tool.status",
        "tool.status",
        "message.delta",
        "message.citation",
        "message.completed",
    ]
    assert streams.finished is not None and streams.finished[0] == "completed"
