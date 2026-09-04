"""验证 P6B-06 企业大脑会话、报告和统计的 PostgreSQL 闭环。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from ai_platform_api.app.errors import ErrorCatalog, register_error_handlers
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.config import Settings, get_settings
from ai_platform_api.modules.assistant.api.enterprise_brain_routes import (
    enterprise_brain_executor,
    enterprise_brain_sources,
)
from ai_platform_api.modules.assistant.api.enterprise_brain_routes import (
    router as enterprise_brain_router,
)
from ai_platform_api.modules.assistant.application.enterprise_brain import (
    EnterpriseBrainConversationService,
)
from ai_platform_api.modules.assistant.application.errors import (
    AssistantDeniedError,
    AssistantIdempotencyConflictError,
    AssistantNotFoundError,
)
from ai_platform_api.modules.assistant.application.runner import AssistantRunExecutor
from ai_platform_api.modules.assistant.application.service import AssistantConversationService
from ai_platform_api.modules.assistant.application.sources import (
    AssistantSource,
    AssistantSourceService,
)
from ai_platform_api.modules.assistant.infrastructure.sqlalchemy import (
    SqlAlchemyAssistantUnitOfWork,
)
from ai_platform_api.modules.authorization.domain.policy import (
    PolicyDecision,
    PolicyDecisionPoint,
    PolicyRequest,
    ResourceScope,
)
from ai_platform_api.modules.enterprise_knowledge.application.service import (
    EnterpriseKnowledgeService,
)
from ai_platform_api.modules.enterprise_knowledge.infrastructure.sqlalchemy import (
    SqlAlchemyEnterpriseKnowledgeRepository,
    SqlAlchemyEnterpriseKnowledgeUnitOfWork,
)
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.service_governance.infrastructure.sqlalchemy import (
    SqlAlchemyServiceRepository,
)
from ai_platform_api.modules.streaming.application.service import TransactionalStreamService
from ai_platform_api.modules.streaming.domain.models import StreamPolicy
from ai_platform_api.modules.streaming.infrastructure.sqlalchemy import (
    SqlAlchemyStreamUnitOfWork,
)
from ai_platform_api.persistence.tables import (
    assistant_runs,
    conversations,
    messages,
    team_knowledge_domain_bases,
    team_knowledge_domains,
    workspace_memberships,
)
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, func, select, text

from tests.integration.test_p1d01_knowledge_postgres import (
    KnowledgeHarness,
    context,
    join_enterprise,
    register,
)
from tests.integration.test_p1d01_knowledge_postgres import (
    knowledge_database as _knowledge_database,
)
from tests.integration.test_p1e01_assistant_postgres import publish_runtime_config
from tests.integration.test_p6a03_document_download_postgres import (
    _current_menu_snapshot,
    _seed_pre_0073_workspace,
)
from tests.integration.test_p6b01_enterprise_console_postgres import _create_document
from tests.integration.test_p6b02_team_management_postgres import (
    _create_pre_0077_enterprise,
)
from tests.integration.test_p6b03_enterprise_knowledge_postgres import _authorized
from tests.integration.test_p403_tool_task_state_postgres import (
    migration_database as _migration_database,
)

knowledge_database = _knowledge_database
migration_database = _migration_database
ERROR_CATALOG_PATH = "contracts/errors/catalog.v1.json"
_ENTERPRISE_BRAIN_PERMISSIONS = (
    "enterprise.brain.access",
    "enterprise.brain.read",
    "enterprise.brain.conversation.create",
    "enterprise.brain.message.create",
    "enterprise.brain.conversation.archive",
    "enterprise.brain.run.cancel",
    "enterprise.brain.source.read",
    "enterprise.brain.feedback.manage",
    "enterprise.brain.report.create",
    "enterprise.brain.report.read",
)
_ENTERPRISE_BRAIN_API_IDS = tuple(
    f"81000000-0000-4000-8000-000000000{suffix}" for suffix in range(207, 223)
)
_ENTERPRISE_BRAIN_MENU_ID = "82000000-0000-4000-8000-000000000282"


class AllowRetrievalPolicy:
    """只允许合成测试的企业文档读取，返回完整可追溯 PDP 证据。"""

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            decision_id=UUID("90000000-0000-4000-8000-000000000981"),
            decision="allow",
            permission_code=request.permission_code,
            workspace_id=request.context.workspace_id,
            resource_scope=ResourceScope(workspace=True),
            field_mask=frozenset(),
            policy_version=41,
            cache_ttl_seconds=30,
            reason="synthetic_allow",
            maximum_security_level="PUBLIC",
        )


class EmptyRetrievalPolicy:
    """返回允许但不含任何资源范围的合成 PDP 决策。"""

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            decision_id=UUID("90000000-0000-4000-8000-000000000982"),
            decision="allow",
            permission_code=request.permission_code,
            workspace_id=request.context.workspace_id,
            resource_scope=ResourceScope(),
            field_mask=frozenset(),
            policy_version=42,
            cache_ttl_seconds=30,
            reason="synthetic_empty_scope",
            maximum_security_level="PUBLIC",
        )


class RecordingExecutor:
    """记录 HTTP 后台调度，不在路由测试中调用模型或真实供应商。"""

    def __init__(self) -> None:
        self.run_ids: list[UUID] = []

    def execute(self, context: RequestContext, run_id: UUID) -> None:
        del context
        self.run_ids.append(run_id)


class SyntheticSourceService:
    """返回固定低敏合成来源，验证企业大脑来源 HTTP 契约映射。"""

    def __init__(self, document_id: UUID) -> None:
        self._document_id = document_id

    def list_sources(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        message_id: UUID,
    ) -> tuple[AssistantSource, ...]:
        del context, conversation_id, message_id
        return (
            AssistantSource(
                rank=1,
                document_id=self._document_id,
                document_version_id=uuid4(),
                chunk_id=uuid4(),
                content_hash="a" * 64,
                quote="仅用于 P6B-06 HTTP 验收的合成引用",
                source_position={"page": 1},
                document_title="合成企业制度",
                source_kind="manual",
                source_name="合成制度来源",
                conflict_detected=False,
            ),
        )


class SyntheticCurrentEvidenceCitationCounter:
    """固定返回一条当前授权 Evidence，隔离验证报告不会解析模型正文。"""

    def count_current_citations(
        self,
        context: RequestContext,
        *,
        run_id: UUID,
    ) -> int:
        del context, run_id
        return 1


def _membership_id(harness: KnowledgeHarness, workspace_id: UUID, account_id: UUID) -> UUID:
    with harness.engine.connect() as connection:
        value = connection.scalar(
            select(workspace_memberships.c.membership_id).where(
                workspace_memberships.c.workspace_id == workspace_id,
                workspace_memberships.c.account_id == account_id,
            )
        )
    assert isinstance(value, UUID)
    return value


def _service(
    harness: KnowledgeHarness,
    policy: PolicyDecisionPoint | None = None,
) -> EnterpriseBrainConversationService:
    """按组合根装配企业大脑，企业知识域只通过窄查询端口注入。"""

    unit_of_work = SqlAlchemyAssistantUnitOfWork(
        harness.sessions,
        SqlAlchemyServiceRepository,
        enterprise_knowledge_scope_factory=SqlAlchemyEnterpriseKnowledgeRepository,
    )
    return EnterpriseBrainConversationService(
        unit_of_work,
        policy or AllowRetrievalPolicy(),
        SyntheticCurrentEvidenceCitationCounter(),
    )


def _ordinary_service(harness: KnowledgeHarness) -> AssistantConversationService:
    """装配普通助手，用于验证两类会话与统计互不混入。"""

    return AssistantConversationService(
        SqlAlchemyAssistantUnitOfWork(harness.sessions, SqlAlchemyServiceRepository)
    )


def _http_app(
    harness: KnowledgeHarness,
    context_value: RequestContext,
    service: EnterpriseBrainConversationService,
    streams: TransactionalStreamService,
    executor: RecordingExecutor,
    sources: SyntheticSourceService,
) -> FastAPI:
    """装配只使用合成数据的企业大脑 HTTP 边界。"""

    application = FastAPI()
    application.state.enterprise_brain_conversation_service = service
    application.state.streaming_service = streams
    application.include_router(enterprise_brain_router, prefix="/api/v1")
    register_error_handlers(application, ErrorCatalog.load(ERROR_CATALOG_PATH))
    application.dependency_overrides[trusted_request_context] = lambda: context_value
    application.dependency_overrides[enterprise_brain_executor] = lambda: cast(
        AssistantRunExecutor, executor
    )
    application.dependency_overrides[enterprise_brain_sources] = lambda: cast(
        AssistantSourceService, sources
    )
    application.dependency_overrides[get_settings] = lambda: cast(
        Settings,
        SimpleNamespace(stream_heartbeat_seconds=15, stream_poll_interval_ms=50),
    )
    return application


def _domain(
    harness: KnowledgeHarness,
    owner_context: RequestContext,
    member_context: RequestContext,
    workspace_id: UUID,
) -> UUID:
    """创建带双成员和单知识库的合成知识域。"""

    base = harness.knowledge.create_knowledge_base(
        owner_context,
        name="合成 P6B-06 企业大脑知识库",
        default_visibility="workspace",
    )
    _create_document(
        harness,
        owner_context,
        base.knowledge_base_id,
        title="合成企业制度",
        security_level="PUBLIC",
        size_bytes=256,
        published=False,
    )
    knowledge = EnterpriseKnowledgeService(
        SqlAlchemyEnterpriseKnowledgeUnitOfWork(harness.sessions)
    )
    domain = knowledge.create_domain(
        _authorized(owner_context, "enterprise.domain.create"),
        workspace_id=workspace_id,
        name="合成企业大脑知识域",
        description="P6B-06 合成验收",
        member_ids=(
            _membership_id(harness, workspace_id, owner_context.actor_id),
            _membership_id(harness, workspace_id, member_context.actor_id),
        ),
        department_ids=(),
        knowledge_base_ids=(base.knowledge_base_id,),
        rag_mode="balanced",
        top_k=8,
        minimum_score=0.2,
    )
    return domain.domain_id


def test_report_idempotency_and_cross_account_isolation(
    knowledge_database: KnowledgeHarness,
) -> None:
    """报告只能从本人已完成企业回答生成，幂等键不得绑定不同事实。"""

    owner = register(knowledge_database, identity="p6b06-report-owner")
    member = register(knowledge_database, identity="p6b06-report-member")
    workspace_id, owner_context, member_context = join_enterprise(knowledge_database, owner, member)
    publish_runtime_config(cast(Any, knowledge_database), owner.account_id, version=6)
    domain_id = _domain(knowledge_database, owner_context, member_context, workspace_id)
    service = _service(knowledge_database)

    conversation = service.create_conversation(
        owner_context, knowledge_domain_id=domain_id, title="合成企业问答"
    )
    submission = service.create_user_message(
        owner_context,
        conversation_id=conversation.conversation_id,
        texts=("请总结制度重点",),
        idempotency_key="synthetic-p6b06-message-1",
    )
    running = service.claim_run(owner_context, run_id=submission.run.run_id)
    assert running is not None and running.assistant_message_id is not None
    completed = service.complete_run(
        owner_context,
        run_id=running.run_id,
        text="制度重点应按流程执行。",
    )
    assert completed.status == "completed"
    assert completed.knowledge_domain_id == domain_id
    assistant_message_id = completed.assistant_message_id
    assert assistant_message_id is not None

    report = service.create_report(
        owner_context,
        message_id=assistant_message_id,
        template="briefing",
        title="制度简报",
        idempotency_key="synthetic-p6b06-report-1",
    )
    assert report.citation_count == 1
    assert report.content_sha256 == hashlib.sha256(report.content.encode("utf-8")).hexdigest()
    replay = service.create_report(
        owner_context,
        message_id=assistant_message_id,
        template="briefing",
        title="制度简报",
        idempotency_key="synthetic-p6b06-report-1",
    )
    assert replay.report_id == report.report_id
    with pytest.raises(AssistantIdempotencyConflictError):
        service.create_report(
            owner_context,
            message_id=assistant_message_id,
            template="comparison",
            title="制度差异",
            idempotency_key="synthetic-p6b06-report-1",
        )
    with pytest.raises(AssistantNotFoundError):
        service.get_report(member_context, report_id=report.report_id)

    with knowledge_database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(conversations.c.conversation_kind).where(
                    conversations.c.conversation_id == conversation.conversation_id,
                    conversations.c.workspace_id == workspace_id,
                )
            )
            == "enterprise_brain"
        )
        frozen_domain = connection.execute(
            select(assistant_runs.c.knowledge_domain_id).where(
                assistant_runs.c.run_id == completed.run_id,
                assistant_runs.c.workspace_id == workspace_id,
            )
        ).scalar_one()
    assert frozen_domain == domain_id


def test_overview_only_counts_enterprise_brain_runs_and_rejects_personal_space(
    knowledge_database: KnowledgeHarness,
) -> None:
    """overview 只聚合企业大脑 Run，个人空间没有企业入口。"""

    owner = register(knowledge_database, identity="p6b06-overview-owner")
    member = register(knowledge_database, identity="p6b06-overview-member")
    workspace_id, owner_context, member_context = join_enterprise(knowledge_database, owner, member)
    publish_runtime_config(cast(Any, knowledge_database), owner.account_id, version=7)
    domain_id = _domain(knowledge_database, owner_context, member_context, workspace_id)
    service = _service(knowledge_database)
    ordinary = _ordinary_service(knowledge_database)
    private_conversation = ordinary.create_conversation(owner_context, title="普通助手统计隔离")
    private_submission = ordinary.create_user_message(
        owner_context,
        conversation_id=private_conversation.conversation_id,
        texts=("普通助手问题",),
        attachment_ids=(),
        idempotency_key="synthetic-p6b06-private-message",
    )
    private_running = ordinary.claim_run(owner_context, run_id=private_submission.run.run_id)
    assert private_running is not None
    ordinary.fail_run(
        owner_context,
        run_id=private_running.run_id,
        error_code="SYNTHETIC_PRIVATE",
    )
    conversation = service.create_conversation(
        owner_context, knowledge_domain_id=domain_id, title="统计问答"
    )
    submission = service.create_user_message(
        owner_context,
        conversation_id=conversation.conversation_id,
        texts=("统计问题",),
        idempotency_key="synthetic-p6b06-message-2",
    )
    running = service.claim_run(owner_context, run_id=submission.run.run_id)
    assert running is not None
    service.fail_run(owner_context, run_id=running.run_id, error_code="SYNTHETIC_FAIL")
    overview = service.get_overview(owner_context)
    assert overview.workspace_id == workspace_id
    assert overview.run_count_30d == 1
    assert overview.failed_run_count_30d == 1
    assert domain_id in overview.knowledge_domain_ids
    assert [
        item.conversation_id for item in service.list_conversations(owner_context, limit=10)
    ] == [conversation.conversation_id]
    assert [
        item.conversation_id for item in ordinary.list_conversations(owner_context, limit=10)
    ] == [private_conversation.conversation_id]
    with pytest.raises(AssistantNotFoundError):
        service.list_messages(
            owner_context,
            conversation_id=private_conversation.conversation_id,
            limit=10,
        )
    with pytest.raises(AssistantNotFoundError):
        ordinary.list_messages(
            owner_context,
            conversation_id=conversation.conversation_id,
            limit=10,
        )

    with pytest.raises(AssistantNotFoundError):
        service.get_overview(context(owner))


def test_empty_pdp_api_key_and_cross_workspace_fail_without_writes(
    knowledge_database: KnowledgeHarness,
) -> None:
    """空 PDP、API Key 和跨空间知识域均失败关闭且不留下会话或 Run。"""

    owner = register(knowledge_database, identity="p6b06-denied-owner")
    member = register(knowledge_database, identity="p6b06-denied-member")
    workspace_id, owner_context, member_context = join_enterprise(knowledge_database, owner, member)
    publish_runtime_config(cast(Any, knowledge_database), owner.account_id, version=4)
    domain_id = _domain(knowledge_database, owner_context, member_context, workspace_id)
    empty_service = _service(knowledge_database, EmptyRetrievalPolicy())
    service = _service(knowledge_database)

    with knowledge_database.engine.connect() as connection:
        conversation_count = connection.scalar(select(func.count()).select_from(conversations))
        run_count = connection.scalar(select(func.count()).select_from(assistant_runs))

    with pytest.raises(AssistantNotFoundError):
        empty_service.create_conversation(
            member_context,
            knowledge_domain_id=domain_id,
            title="空范围不得创建",
        )
    with pytest.raises(AssistantDeniedError):
        service.create_conversation(
            replace(member_context, authentication_method="api_key"),
            knowledge_domain_id=domain_id,
            title="API Key 不开放企业入口",
        )

    other_workspace = knowledge_database.enterprise.create(
        context(owner),
        name="合成 P6B-06 跨空间企业",
    )
    with pytest.raises(AssistantNotFoundError):
        service.create_conversation(
            context(owner, other_workspace.workspace_id),
            knowledge_domain_id=domain_id,
            title="跨空间知识域不得创建",
        )

    with knowledge_database.engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(conversations)) == conversation_count
        )
        assert connection.scalar(select(func.count()).select_from(assistant_runs)) == run_count


def test_domain_revocation_blocks_new_run_but_keeps_historical_report(
    knowledge_database: KnowledgeHarness,
) -> None:
    """撤出知识域后不得继续提问，既有 Run 与不可变报告仍保持可追溯。"""

    owner = register(knowledge_database, identity="p6b06-revoke-owner")
    member = register(knowledge_database, identity="p6b06-revoke-member")
    workspace_id, owner_context, member_context = join_enterprise(knowledge_database, owner, member)
    publish_runtime_config(cast(Any, knowledge_database), owner.account_id, version=5)
    domain_id = _domain(knowledge_database, owner_context, member_context, workspace_id)
    service = _service(knowledge_database)
    conversation = service.create_conversation(
        member_context,
        knowledge_domain_id=domain_id,
        title="撤权传播验收",
    )
    submission = service.create_user_message(
        member_context,
        conversation_id=conversation.conversation_id,
        texts=("撤权前的问题",),
        idempotency_key="synthetic-p6b06-before-revoke",
    )
    running = service.claim_run(member_context, run_id=submission.run.run_id)
    assert running is not None and running.assistant_message_id is not None
    completed = service.complete_run(
        member_context,
        run_id=running.run_id,
        text="撤权前可读取的合成答案。[1]",
    )
    assert completed.assistant_message_id is not None
    report = service.create_report(
        member_context,
        message_id=completed.assistant_message_id,
        template="risk_review",
        title="撤权前风险报告",
        idempotency_key="synthetic-p6b06-revoke-report",
    )

    with knowledge_database.engine.connect() as connection:
        version = connection.scalar(
            select(team_knowledge_domains.c.version).where(
                team_knowledge_domains.c.workspace_id == workspace_id,
                team_knowledge_domains.c.domain_id == domain_id,
            )
        )
        base_ids = tuple(
            connection.scalars(
                select(team_knowledge_domain_bases.c.knowledge_base_id).where(
                    team_knowledge_domain_bases.c.workspace_id == workspace_id,
                    team_knowledge_domain_bases.c.domain_id == domain_id,
                )
            )
        )
        message_count = connection.scalar(select(func.count()).select_from(messages))
        run_count = connection.scalar(select(func.count()).select_from(assistant_runs))
    assert isinstance(version, int)

    knowledge = EnterpriseKnowledgeService(
        SqlAlchemyEnterpriseKnowledgeUnitOfWork(knowledge_database.sessions)
    )
    knowledge.replace_domain_scope(
        _authorized(owner_context, "enterprise.domain.scope"),
        workspace_id=workspace_id,
        domain_id=domain_id,
        expected_version=version,
        member_ids=(_membership_id(knowledge_database, workspace_id, owner.account_id),),
        department_ids=(),
        knowledge_base_ids=base_ids,
    )

    with pytest.raises(AssistantNotFoundError):
        service.create_user_message(
            member_context,
            conversation_id=conversation.conversation_id,
            texts=("撤权后不得继续提问",),
            idempotency_key="synthetic-p6b06-after-revoke",
        )
    assert service.get_report(member_context, report_id=report.report_id).content == report.content
    assert (
        service.get_run_for_stream(
            member_context,
            conversation_id=conversation.conversation_id,
            run_id=completed.run_id,
        ).run_id
        == completed.run_id
    )

    with knowledge_database.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(messages)) == message_count
        assert connection.scalar(select(func.count()).select_from(assistant_runs)) == run_count


def test_http_completed_flow_sse_feedback_report_download_and_archive(
    knowledge_database: KnowledgeHarness,
) -> None:
    """真实 HTTP 路由共享同一企业会话、Run、流、反馈与报告事实。"""

    owner = register(knowledge_database, identity="p6b06-http-owner")
    member = register(knowledge_database, identity="p6b06-http-member")
    workspace_id, owner_context, member_context = join_enterprise(knowledge_database, owner, member)
    publish_runtime_config(cast(Any, knowledge_database), owner.account_id, version=3)
    domain_id = _domain(knowledge_database, owner_context, member_context, workspace_id)
    service = _service(knowledge_database)
    streams = TransactionalStreamService(
        SqlAlchemyStreamUnitOfWork(knowledge_database.sessions),
        StreamPolicy(),
    )
    executor = RecordingExecutor()
    source_document_id = uuid4()
    application = _http_app(
        knowledge_database,
        owner_context,
        service,
        streams,
        executor,
        SyntheticSourceService(source_document_id),
    )
    prefix = f"/api/v1/workspaces/{workspace_id}/enterprise-brain"

    with TestClient(application) as client:
        created = client.post(
            f"{prefix}/conversations",
            json={"knowledge_domain_id": str(domain_id), "title": "合成 HTTP 企业问答"},
        )
        assert created.status_code == 201
        conversation_id = UUID(created.json()["conversation_id"])

        queued = client.post(
            f"{prefix}/conversations/{conversation_id}/messages",
            headers={"Idempotency-Key": "synthetic-p6b06-http-message"},
            json={"parts": [{"type": "text", "text": "请总结合成制度"}]},
        )
        assert queued.status_code == 201
        run_id = UUID(queued.json()["run"]["run_id"])
        assert queued.json()["run"]["knowledge_domain_id"] == str(domain_id)
        assert queued.json()["run"]["knowledge_domain_policy_version"] == 1
        assert executor.run_ids == [run_id]
        assert client.get(f"{prefix}/conversations").json()["items"][0]["conversation_id"] == str(
            conversation_id
        )
        assert client.get(f"{prefix}/conversations/{conversation_id}/runs").json()["items"][0][
            "run_id"
        ] == str(run_id)

        running = service.claim_run(owner_context, run_id=run_id)
        assert running is not None and running.assistant_message_id is not None
        now = datetime.now(UTC)
        streams.start_run(
            workspace_id,
            conversation_id,
            running.assistant_message_id,
            run_id,
            now=now,
        )
        first_event = streams.append(
            run_id,
            "message.delta",
            running.trace_id,
            running.traceparent,
            {"delta": "合成制度要点"},
            now=now,
        )
        completed = service.complete_run(
            owner_context, run_id=run_id, text="合成制度要点应按授权流程执行。"
        )
        completed_event = streams.append(
            run_id,
            "message.completed",
            running.trace_id,
            running.traceparent,
            {"text": "合成制度要点应按授权流程执行。"},
            now=now,
        )
        streams.finish(
            run_id,
            "completed",
            {"status": "completed", "text": "合成制度要点应按授权流程执行。"},
            now=now,
        )

        event_path = f"{prefix}/conversations/{conversation_id}/runs/{run_id}/events"
        full_stream = client.get(event_path)
        resumed_stream = client.get(
            event_path, headers={"Last-Event-ID": str(first_event.event_id)}
        )
        acknowledged_stream = client.get(
            event_path, headers={"Last-Event-ID": str(completed_event.event_id)}
        )
        assert full_stream.status_code == 200
        assert f"id: {first_event.event_id}" in full_stream.text
        assert f"id: {first_event.event_id}" not in resumed_stream.text
        assert f"id: {completed_event.event_id}" in resumed_stream.text
        assert "event: message.snapshot" in acknowledged_stream.text

        message_id = completed.assistant_message_id
        assert message_id is not None
        sources = client.get(
            f"{prefix}/conversations/{conversation_id}/messages/{message_id}/sources"
        )
        assert sources.status_code == 200
        assert sources.json()["items"][0]["document_id"] == str(source_document_id)
        feedback_path = f"{prefix}/conversations/{conversation_id}/messages/{message_id}/feedback"
        assert client.get(feedback_path).json()["item"] is None
        feedback = client.put(
            feedback_path,
            json={"rating": "helpful", "issue_codes": [], "comment": None},
        )
        assert feedback.status_code == 200
        assert client.get(feedback_path).json()["item"]["version"] == 1

        report = client.post(
            f"{prefix}/reports",
            headers={"Idempotency-Key": "synthetic-p6b06-http-report"},
            json={"message_id": str(message_id), "template": "briefing", "title": "合成制度简报"},
        )
        assert report.status_code == 201
        report_id = UUID(report.json()["report_id"])
        assert report.json()["citation_count"] == 1
        assert client.get(f"{prefix}/reports").json()["items"][0]["report_id"] == str(report_id)
        assert client.get(f"{prefix}/reports/{report_id}").status_code == 200
        downloaded = client.get(f"{prefix}/reports/{report_id}/download")
        assert downloaded.status_code == 200
        assert downloaded.headers["content-type"].startswith("text/markdown")
        assert downloaded.headers["content-disposition"] == (
            f'attachment; filename="{report_id}.md"'
        )
        assert "合成制度要点" in downloaded.text

        archived = client.post(f"{prefix}/conversations/{conversation_id}/archive")
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"
        overview = client.get(prefix)
        assert overview.status_code == 200
        assert overview.json()["archived_conversation_count"] == 1
        assert overview.json()["completed_run_count_30d"] == 1


def test_http_cancel_rejects_attachments_and_cross_workspace_path(
    knowledge_database: KnowledgeHarness,
) -> None:
    """企业大脑不接收附件，取消形成终态，路径空间不一致失败关闭。"""

    owner = register(knowledge_database, identity="p6b06-http-cancel-owner")
    member = register(knowledge_database, identity="p6b06-http-cancel-member")
    workspace_id, owner_context, member_context = join_enterprise(knowledge_database, owner, member)
    domain_id = _domain(knowledge_database, owner_context, member_context, workspace_id)
    service = _service(knowledge_database)
    streams = TransactionalStreamService(
        SqlAlchemyStreamUnitOfWork(knowledge_database.sessions), StreamPolicy()
    )
    application = _http_app(
        knowledge_database,
        owner_context,
        service,
        streams,
        RecordingExecutor(),
        SyntheticSourceService(uuid4()),
    )
    prefix = f"/api/v1/workspaces/{workspace_id}/enterprise-brain"

    with TestClient(application) as client:
        created = client.post(
            f"{prefix}/conversations",
            json={"knowledge_domain_id": str(domain_id), "title": "合成取消问答"},
        )
        conversation_id = UUID(created.json()["conversation_id"])
        rejected = client.post(
            f"{prefix}/conversations/{conversation_id}/messages",
            headers={"Idempotency-Key": "synthetic-p6b06-http-attach"},
            json={
                "parts": [{"type": "text", "text": "不得接受附件"}],
                "attachment_ids": [str(uuid4())],
            },
        )
        assert rejected.status_code == 403
        queued = client.post(
            f"{prefix}/conversations/{conversation_id}/messages",
            headers={"Idempotency-Key": "synthetic-p6b06-http-cancel"},
            json={"parts": [{"type": "text", "text": "取消合成问答"}]},
        )
        run_id = UUID(queued.json()["run"]["run_id"])
        cancelled = client.post(f"{prefix}/conversations/{conversation_id}/runs/{run_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["error_code"] == "RUN_CANCELLED"
        stream = client.get(f"{prefix}/conversations/{conversation_id}/runs/{run_id}/events")
        assert stream.status_code == 200
        assert "RUN_CANCELLED" in stream.text
        assert client.post(f"{prefix}/conversations/{conversation_id}/archive").status_code == 200

        wrong_workspace = client.get(
            f"/api/v1/workspaces/{owner.personal_workspace_id}/enterprise-brain"
        )
        assert wrong_workspace.status_code == 403
        assert wrong_workspace.json()["code"] == "POLICY_DENIED"


def test_revision_0081_empty_schema_roundtrip(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    """空 Schema 往返必须精确维护知识域列、报告表和 16 项接口绑定。"""

    config, connection, schema, _ = migration_database
    command.upgrade(config, "20260903_0080")
    command.upgrade(config, "20260903_0081")
    connection.commit()
    assert _revision(connection, schema) == "20260903_0081"
    assert _enterprise_brain_columns(connection, schema) == {
        ("assistant_runs", "knowledge_domain_id"),
        ("assistant_runs", "knowledge_domain_policy_version"),
        ("conversations", "knowledge_domain_id"),
        ("conversations", "knowledge_domain_policy_version"),
    }
    assert _table_exists(connection, schema, "enterprise_brain_reports")
    assert _registered_enterprise_brain_binding_count(connection, schema) == 16

    command.downgrade(config, "20260903_0080")
    connection.commit()
    assert _revision(connection, schema) == "20260903_0080"
    assert _enterprise_brain_columns(connection, schema) == set()
    assert not _table_exists(connection, schema, "enterprise_brain_reports")
    assert _registered_enterprise_brain_binding_count(connection, schema) == 0

    command.upgrade(config, "20260903_0081")
    connection.commit()
    assert _revision(connection, schema) == "20260903_0081"
    assert _table_exists(connection, schema, "enterprise_brain_reports")


def test_revision_0081_enterprise_backfill_isolation_and_downgrade_guards(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    """只升级企业系统角色与菜单，并保护业务、自定义授权和后续发布。"""

    config, connection, schema, database_url = migration_database
    personal = _seed_pre_0073_workspace(migration_database)
    command.upgrade(config, "20260903_0080")
    connection.commit()
    enterprise_workspace_id = _create_pre_0077_enterprise(
        database_url, schema, personal.account_id, personal.workspace_id
    )
    connection.execute(
        text(
            f'DELETE FROM "{schema}".role_permission_grants '
            "WHERE permission_code = ANY(CAST(:permission_codes AS varchar[]))"
        ),
        {"permission_codes": list(_ENTERPRISE_BRAIN_PERMISSIONS)},
    )
    connection.commit()
    enterprise_release_id, enterprise_snapshot = _seed_enterprise_menu_publication(
        connection,
        schema,
        enterprise_workspace_id,
        personal.account_id,
        _current_menu_snapshot(connection, schema, personal.workspace_id)[1],
    )
    personal_release_id, personal_snapshot = _current_menu_snapshot(
        connection, schema, personal.workspace_id
    )

    command.upgrade(config, "20260903_0081")
    connection.commit()
    assert _system_permission_count(connection, schema, enterprise_workspace_id) == 20
    assert _system_permission_count(connection, schema, personal.workspace_id) == 0
    assert _current_menu_snapshot(connection, schema, personal.workspace_id) == (
        personal_release_id,
        personal_snapshot,
    )
    upgraded_release_id, upgraded_snapshot = _current_menu_snapshot(
        connection, schema, enterprise_workspace_id
    )
    assert upgraded_release_id != enterprise_release_id
    assert upgraded_snapshot["registry_version"] == 37
    assert {str(item["menu_id"]) for item in upgraded_snapshot["menus"]} >= {
        _ENTERPRISE_BRAIN_MENU_ID
    }

    command.downgrade(config, "20260903_0080")
    connection.commit()
    assert _current_menu_snapshot(connection, schema, enterprise_workspace_id) == (
        enterprise_release_id,
        enterprise_snapshot,
    )
    assert _current_menu_snapshot(connection, schema, personal.workspace_id) == (
        personal_release_id,
        personal_snapshot,
    )
    assert _system_permission_count(connection, schema, enterprise_workspace_id) == 0

    command.upgrade(config, "20260903_0081")
    connection.commit()
    domain_id = uuid4()
    conversation_id = uuid4()
    now = datetime.now(UTC)
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".team_knowledge_domains (
                domain_id, workspace_id, name, description, current_rag_policy_version,
                status, created_by_account_id, created_at, updated_at, version
            ) VALUES (
                :domain_id, :workspace_id, '合成 P6B-06 降级保护知识域', NULL, 1,
                'active', :account_id, :occurred_at, :occurred_at, 1
            )
            """
        ),
        {
            "domain_id": domain_id,
            "workspace_id": enterprise_workspace_id,
            "account_id": personal.account_id,
            "occurred_at": now,
        },
    )
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".conversations (
                conversation_id, workspace_id, created_by_account_id, conversation_kind,
                title, status, scope_mode, knowledge_base_ids, tag_ids, knowledge_domain_id,
                knowledge_domain_policy_version, created_at, updated_at, version
            ) VALUES (
                :conversation_id, :workspace_id, :account_id, 'enterprise_brain',
                '合成 P6B-06 降级保护会话', 'active', 'workspace', ARRAY[]::uuid[],
                ARRAY[]::uuid[], :domain_id, 1, :occurred_at, :occurred_at, 1
            )
            """
        ),
        {
            "conversation_id": conversation_id,
            "workspace_id": enterprise_workspace_id,
            "account_id": personal.account_id,
            "domain_id": domain_id,
            "occurred_at": now,
        },
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="存在企业大脑会话"):
        command.downgrade(config, "20260903_0080")
    connection.rollback()
    connection.execute(
        text(f'DELETE FROM "{schema}".conversations WHERE conversation_id = :id'),
        {"id": conversation_id},
    )
    connection.execute(
        text(f'DELETE FROM "{schema}".team_knowledge_domains WHERE domain_id = :id'),
        {"id": domain_id},
    )
    connection.commit()

    custom_role_id = uuid4()
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".roles (
                role_id, workspace_id, role_key, name, status, system_managed,
                created_at, updated_at, version
            ) VALUES (
                :role_id, :workspace_id, 'synthetic_p6b06_reviewer',
                '合成企业大脑审阅角色', 'active', false, :occurred_at, :occurred_at, 1
            )
            """
        ),
        {
            "role_id": custom_role_id,
            "workspace_id": enterprise_workspace_id,
            "occurred_at": now,
        },
    )
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type, department_ids,
                resource_ids, maximum_security_level, field_mask
            ) VALUES (
                :workspace_id, :role_id, 'enterprise.brain.read', 'workspace',
                ARRAY[]::uuid[], ARRAY[]::uuid[], 'RESTRICTED', ARRAY[]::varchar[]
            )
            """
        ),
        {"workspace_id": enterprise_workspace_id, "role_id": custom_role_id},
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="存在自定义企业大脑授权"):
        command.downgrade(config, "20260903_0080")
    connection.rollback()
    connection.execute(
        text(f'DELETE FROM "{schema}".roles WHERE role_id = :role_id'),
        {"role_id": custom_role_id},
    )
    connection.commit()

    _append_followup_menu_release(connection, schema, enterprise_workspace_id, personal.account_id)
    with pytest.raises(RuntimeError, match="当前菜单发布已在 P6B-06 后变化"):
        command.downgrade(config, "20260903_0080")
    connection.rollback()
    assert _revision(connection, schema) == "20260903_0081"


def _revision(connection: Connection, schema: str) -> str:
    return str(connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')))


def _enterprise_brain_columns(connection: Connection, schema: str) -> set[tuple[str, str]]:
    rows = connection.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = :schema "
            "AND table_name = ANY(CAST(:table_names AS varchar[])) "
            "AND column_name = ANY(CAST(:column_names AS varchar[]))"
        ),
        {
            "schema": schema,
            "table_names": ["assistant_runs", "conversations"],
            "column_names": ["knowledge_domain_id", "knowledge_domain_policy_version"],
        },
    )
    return {(str(row.table_name), str(row.column_name)) for row in rows}


def _table_exists(connection: Connection, schema: str, table_name: str) -> bool:
    return bool(
        connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name = :table_name)"
            ),
            {"schema": schema, "table_name": table_name},
        )
    )


def _registered_enterprise_brain_binding_count(connection: Connection, schema: str) -> int:
    return int(
        connection.scalar(
            text(
                f'SELECT count(*) FROM "{schema}".registered_menu_api_bindings '
                "WHERE api_resource_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": list(_ENTERPRISE_BRAIN_API_IDS)},
        )
        or 0
    )


def _system_permission_count(connection: Connection, schema: str, workspace_id: UUID) -> int:
    return int(
        connection.scalar(
            text(
                f'SELECT count(*) FROM "{schema}".role_permission_grants AS grants '
                f'JOIN "{schema}".roles AS roles '
                "ON roles.workspace_id = grants.workspace_id AND roles.role_id = grants.role_id "
                "WHERE grants.workspace_id = :workspace_id "
                "AND roles.role_key IN ('workspace_owner', 'workspace_member') "
                "AND roles.system_managed = true "
                "AND grants.permission_code = ANY(CAST(:permission_codes AS varchar[]))"
            ),
            {
                "workspace_id": workspace_id,
                "permission_codes": list(_ENTERPRISE_BRAIN_PERMISSIONS),
            },
        )
        or 0
    )


def _seed_enterprise_menu_publication(
    connection: Connection,
    schema: str,
    workspace_id: UUID,
    account_id: UUID,
    snapshot: Mapping[str, Any],
) -> tuple[UUID, dict[str, Any]]:
    """为存量企业建立 Revision 0080 发布，供 0081 精确升级与恢复。"""

    release_id = uuid4()
    copied_snapshot = json.loads(json.dumps(snapshot, ensure_ascii=False))
    digest = hashlib.sha256(
        json.dumps(
            copied_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    occurred_at = datetime.now(UTC)
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".menu_releases (
                release_id, workspace_id, release_number, release_kind, source_release_id,
                status, snapshot, snapshot_digest, validation_errors, rejection_reason,
                created_by_account_id, decided_by_account_id, created_at, validated_at,
                decided_at, published_at, version
            ) VALUES (
                :release_id, :workspace_id, 1, 'standard', NULL, 'published',
                CAST(:snapshot AS jsonb), :snapshot_digest, ARRAY[]::varchar[], NULL,
                :account_id, :account_id, :occurred_at, :occurred_at, :occurred_at,
                :occurred_at, 15
            )
            """
        ),
        {
            "release_id": release_id,
            "workspace_id": workspace_id,
            "snapshot": json.dumps(copied_snapshot, ensure_ascii=False, separators=(",", ":")),
            "snapshot_digest": digest,
            "account_id": account_id,
            "occurred_at": occurred_at,
        },
    )
    connection.execute(
        text(
            f'INSERT INTO "{schema}".workspace_menu_publications '
            "(workspace_id, current_release_id, published_at) "
            "VALUES (:workspace_id, :release_id, :occurred_at)"
        ),
        {
            "workspace_id": workspace_id,
            "release_id": release_id,
            "occurred_at": occurred_at,
        },
    )
    connection.commit()
    return release_id, copied_snapshot


def _append_followup_menu_release(
    connection: Connection, schema: str, workspace_id: UUID, account_id: UUID
) -> None:
    """追加 0081 后企业发布，验证降级不会猜测覆盖后续管理员事实。"""

    current_release_id, snapshot = _current_menu_snapshot(connection, schema, workspace_id)
    release_id = uuid4()
    release_number = int(
        connection.scalar(
            text(
                f'SELECT COALESCE(MAX(release_number), 0) + 1 FROM "{schema}".menu_releases '
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace_id},
        )
        or 1
    )
    occurred_at = datetime.now(UTC)
    encoded_snapshot = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".menu_releases (
                release_id, workspace_id, release_number, release_kind, source_release_id,
                status, snapshot, snapshot_digest, validation_errors, rejection_reason,
                created_by_account_id, decided_by_account_id, created_at, validated_at,
                decided_at, published_at, version
            ) VALUES (
                :release_id, :workspace_id, :release_number, 'standard', :source_release_id,
                'published', CAST(:snapshot AS jsonb), :snapshot_digest, ARRAY[]::varchar[], NULL,
                :account_id, :account_id, :occurred_at, :occurred_at, :occurred_at,
                :occurred_at, 16
            )
            """
        ),
        {
            "release_id": release_id,
            "workspace_id": workspace_id,
            "release_number": release_number,
            "source_release_id": current_release_id,
            "snapshot": encoded_snapshot,
            "snapshot_digest": digest,
            "account_id": account_id,
            "occurred_at": occurred_at,
        },
    )
    connection.execute(
        text(
            f'UPDATE "{schema}".workspace_menu_publications '
            "SET current_release_id = :release_id, published_at = :occurred_at "
            "WHERE workspace_id = :workspace_id"
        ),
        {
            "release_id": release_id,
            "occurred_at": occurred_at,
            "workspace_id": workspace_id,
        },
    )
    connection.commit()
