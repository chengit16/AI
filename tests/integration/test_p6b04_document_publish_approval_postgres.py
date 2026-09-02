"""验证 P6B-04 企业文档发布审批、重新鉴权和原子发布边界。"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.application.field_registry import (
    load_field_policy_registry,
)
from ai_platform_api.modules.authorization.application.grants import (
    RolePermissionService,
)
from ai_platform_api.modules.authorization.application.policy import RbacPolicyDecisionPoint
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.infrastructure.sqlalchemy import (
    SqlAlchemyRolePermissionUnitOfWork,
    SqlAlchemyTransactionalPolicyGrantRepository,
)
from ai_platform_api.modules.enterprise_knowledge.application.service import (
    EnterpriseKnowledgeConflictError,
    EnterpriseKnowledgeDeniedError,
    EnterpriseKnowledgeService,
)
from ai_platform_api.modules.enterprise_knowledge.infrastructure.document_publish_approval import (
    SqlAlchemyDocumentPublishSubjectLifecycle,
)
from ai_platform_api.modules.enterprise_knowledge.infrastructure.sqlalchemy import (
    SqlAlchemyEnterpriseKnowledgeUnitOfWork,
)
from ai_platform_api.modules.identity.application.roles import RoleService
from ai_platform_api.modules.identity.infrastructure.roles_sqlalchemy import (
    SqlAlchemyRoleUnitOfWork,
)
from ai_platform_api.modules.knowledge.application.publishing import (
    publish_document_version_in_transaction,
)
from ai_platform_api.modules.knowledge.infrastructure.sqlalchemy import (
    SqlAlchemyKnowledgeRepository,
)
from ai_platform_api.modules.workflow.application.approval_runtime import (
    ApprovalInstanceDenied,
    ApprovalInstanceService,
)
from ai_platform_api.modules.workflow.application.approvals import ApprovalPolicyService
from ai_platform_api.modules.workflow.domain.approval_runtime import ApprovalRuntimeCommand
from ai_platform_api.modules.workflow.domain.approvals import (
    ApprovalApproverSource,
    ApprovalLevelDefinition,
    ApprovalPolicyDefinition,
)
from ai_platform_api.modules.workflow.infrastructure.approval_runtime_sqlalchemy import (
    RoutedApprovalSubjectLifecycle,
    SqlAlchemyApprovalRuntimeUnitOfWork,
)
from ai_platform_api.modules.workflow.infrastructure.approvals_sqlalchemy import (
    SqlAlchemyApprovalPolicyUnitOfWork,
)
from ai_platform_api.persistence.tables import (
    audit_records,
    document_publications,
    document_publish_requests,
    document_sources,
    document_versions,
    enterprise_categories,
    enterprise_category_documents,
    index_versions,
    ingestion_jobs,
    outbox_events,
)
from ai_platform_backend.indexing.persistence import document_index_publications
from sqlalchemy import func, insert, select, update

from tests.integration.test_p1d01_knowledge_postgres import (
    KnowledgeHarness,
    RegisteredAccount,
    context,
    join_enterprise,
    register,
)
from tests.integration.test_p1d01_knowledge_postgres import (
    knowledge_database as _knowledge_database,
)
from tests.integration.test_p6b01_enterprise_console_postgres import _create_document
from tests.integration.test_p6b03_enterprise_knowledge_postgres import _authorized

knowledge_database = _knowledge_database


def _services(
    harness: KnowledgeHarness,
) -> tuple[EnterpriseKnowledgeService, ApprovalPolicyService, ApprovalInstanceService]:
    """按生产组合根装配企业知识、正式 RBAC 和通用审批生命周期。"""

    registry = load_resource_registry(Path("contracts/authorization/resource-registry.v1.json"))
    field_registry = load_field_policy_registry(
        Path("contracts/authorization/field-policy-registry.v1.json")
    )
    policies = ApprovalPolicyService(SqlAlchemyApprovalPolicyUnitOfWork(harness.sessions))
    approvals = ApprovalInstanceService(
        SqlAlchemyApprovalRuntimeUnitOfWork(
            harness.sessions,
            lambda session: RoutedApprovalSubjectLifecycle(
                {
                    "document.publish": SqlAlchemyDocumentPublishSubjectLifecycle(
                        session,
                        SqlAlchemyKnowledgeRepository(session),
                        RbacPolicyDecisionPoint(
                            registry,
                            SqlAlchemyTransactionalPolicyGrantRepository(session),
                            field_registry,
                        ),
                        publish_document_version_in_transaction,
                    )
                }
            ),
        ),
        policies,
    )
    return (
        EnterpriseKnowledgeService(
            SqlAlchemyEnterpriseKnowledgeUnitOfWork(harness.sessions), approvals
        ),
        policies,
        approvals,
    )


def _add_ready_index(
    harness: KnowledgeHarness,
    *,
    workspace_id: UUID,
    document_id: UUID,
    document_version_id: UUID,
    content_hash: str,
) -> UUID:
    """把合成解析任务和索引收敛到 ready，未伪造发布指针。"""

    completed_at = datetime.now(UTC)
    index_version_id = uuid4()
    with harness.sessions.begin() as session:
        source_id = session.scalar(
            select(document_sources.c.source_id).where(
                document_sources.c.workspace_id == workspace_id,
                document_sources.c.document_version_id == document_version_id,
            )
        )
        job_id = session.scalar(
            select(ingestion_jobs.c.ingestion_job_id).where(
                ingestion_jobs.c.workspace_id == workspace_id,
                ingestion_jobs.c.document_version_id == document_version_id,
            )
        )
        assert isinstance(source_id, UUID)
        assert isinstance(job_id, UUID)
        session.execute(
            update(ingestion_jobs)
            .where(ingestion_jobs.c.ingestion_job_id == job_id)
            .values(
                status="succeeded",
                attempt_count=1,
                completed_at=completed_at,
                artifact_object_key=f"synthetic/p6b04/{document_version_id}.json",
                parsed_content_hash=content_hash,
                parser_name="synthetic-p6b04-parser-v1",
                ocr_used=False,
                page_count=1,
                block_count=1,
                updated_at=completed_at,
            )
        )
        session.execute(
            insert(index_versions).values(
                index_version_id=index_version_id,
                workspace_id=workspace_id,
                knowledge_base_id=session.scalar(
                    select(ingestion_jobs.c.knowledge_base_id).where(
                        ingestion_jobs.c.ingestion_job_id == job_id
                    )
                ),
                document_id=document_id,
                document_version_id=document_version_id,
                ingestion_job_id=job_id,
                source_id=source_id,
                build_no=1,
                artifact_object_key=f"synthetic/p6b04/{document_version_id}.json",
                source_content_hash=content_hash,
                parsed_content_hash=content_hash,
                chunker_version="synthetic-p6b04-chunker-v1",
                embedding_model_version="synthetic-p6b04-embedding-v1",
                tokenizer_version="synthetic-p6b04-tokenizer-v1",
                department_ids=[],
                visibility="workspace",
                security_level="PUBLIC",
                permission_labels=[],
                status="ready",
                processing_lane="indexing",
                attempt_count=1,
                embedding_attempt_count=1,
                indexing_attempt_count=1,
                max_attempts=3,
                available_at=completed_at,
                started_at=completed_at,
                completed_at=completed_at,
                chunk_count=1,
                staged_chunk_count=1,
                manual_recovery_count=0,
                created_at=completed_at,
                updated_at=completed_at,
            )
        )
    return index_version_id


def _prepare_request(
    harness: KnowledgeHarness,
    *,
    suffix: str,
    two_levels: bool = True,
) -> tuple[
    EnterpriseKnowledgeService,
    ApprovalInstanceService,
    UUID,
    RegisteredAccount,
    RegisteredAccount,
    RegisteredAccount,
    RequestContext,
    RequestContext,
    RequestContext,
    UUID,
    UUID,
    UUID,
]:
    """创建只含合成身份、治理分类、就绪版本和审批链的完整场景。"""

    owner = register(harness, identity=f"p6b04-{suffix}-owner")
    approver_a = register(harness, identity=f"p6b04-{suffix}-approver-a")
    workspace_id, owner_context, approver_a_context = join_enterprise(harness, owner, approver_a)
    approver_b = register(harness, identity=f"p6b04-{suffix}-approver-b")
    invitation = harness.enterprise.invite(
        owner_context, workspace_id=workspace_id, login_name=approver_b.login_name
    )
    harness.enterprise.accept_invitation(
        context(approver_b), invitation_id=invitation.invitation_id
    )
    approver_b_context = context(approver_b, workspace_id)
    service, policies, approvals = _services(harness)
    levels = [
        ApprovalLevelDefinition(
            1,
            "any",
            (ApprovalApproverSource("accounts", (approver_a.account_id,)),),
            reminder_after_minutes=1,
            timeout_after_minutes=2,
            timeout_action="reject",
        )
    ]
    if two_levels:
        levels.append(
            ApprovalLevelDefinition(
                2,
                "any",
                (ApprovalApproverSource("accounts", (approver_b.account_id,)),),
            )
        )
    policies.create(
        owner_context,
        name=f"合成 P6B-04 发布审批 {suffix}",
        definition=ApprovalPolicyDefinition(
            "document.publish",
            "publish",
            100,
            (),
            ("PUBLIC",),
            ("high",),
            (),
            tuple(levels),
            allow_self_approval=False,
        ),
    )
    knowledge_base = harness.knowledge.create_knowledge_base(
        owner_context, name=f"合成 P6B-04 知识库 {suffix}", default_visibility="workspace"
    )
    title = f"合成 P6B-04 文档 {suffix}"
    document_id, version_id = _create_document(
        harness,
        owner_context,
        knowledge_base.knowledge_base_id,
        title=title,
        security_level="PUBLIC",
        size_bytes=128,
        published=False,
    )
    content_hash = hashlib.sha256(title.encode()).hexdigest()
    harness.knowledge.mark_document_version_ready(
        owner_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        document_id=document_id,
        document_version_id=version_id,
        content_hash=content_hash,
    )
    index_id = _add_ready_index(
        harness,
        workspace_id=workspace_id,
        document_id=document_id,
        document_version_id=version_id,
        content_hash=content_hash,
    )
    service.create_category(
        _authorized(owner_context, "enterprise.category.create"),
        workspace_id=workspace_id,
        name=f"合成审批分类 {suffix}",
        description=None,
        parent_category_id=None,
        visibility="public",
        department_ids=(),
        document_ids=(document_id,),
        approval_required=True,
    )
    return (
        service,
        approvals,
        workspace_id,
        owner,
        approver_a,
        approver_b,
        owner_context,
        approver_a_context,
        approver_b_context,
        document_id,
        version_id,
        index_id,
    )


def _approval_context(context_value: RequestContext, approval_instance_id: UUID) -> RequestContext:
    """模拟中间件对当前审批实例形成的精确资源级许可。"""

    return replace(
        context_value,
        authorized_resource_ids=frozenset({approval_instance_id}),
    )


def _assert_no_publication_pointers(
    harness: KnowledgeHarness,
    *,
    workspace_id: UUID,
    document_id: UUID,
) -> None:
    """统一验证失败关闭场景没有产生文档或索引发布指针。"""

    with harness.sessions() as session:
        assert (
            session.scalar(
                select(document_publications.c.current_document_version_id).where(
                    document_publications.c.workspace_id == workspace_id,
                    document_publications.c.document_id == document_id,
                )
            )
            is None
        )
        assert (
            session.scalar(
                select(document_index_publications.c.index_version_id).where(
                    document_index_publications.c.workspace_id == workspace_id,
                    document_index_publications.c.document_id == document_id,
                )
            )
            is None
        )


def _grant_requester_publish_role(
    harness: KnowledgeHarness,
    *,
    workspace_id: UUID,
    owner_context: RequestContext,
    requester: RegisteredAccount,
) -> tuple[RequestContext, UUID]:
    """通过正式角色与权限服务授予合成申请人发布能力。"""

    invitation = harness.enterprise.invite(
        owner_context,
        workspace_id=workspace_id,
        login_name=requester.login_name,
    )
    harness.enterprise.accept_invitation(
        context(requester),
        invitation_id=invitation.invitation_id,
    )
    requester_context = context(requester, workspace_id)
    role_service = RoleService(SqlAlchemyRoleUnitOfWork(harness.sessions))
    registry = load_resource_registry(Path("contracts/authorization/resource-registry.v1.json"))
    field_registry = load_field_policy_registry(
        Path("contracts/authorization/field-policy-registry.v1.json")
    )
    permission_service = RolePermissionService(
        registry,
        SqlAlchemyRolePermissionUnitOfWork(harness.sessions),
        field_registry,
    )
    role = role_service.create(
        owner_context,
        workspace_id=workspace_id,
        role_key=f"p6b04_publisher_{requester.account_id.hex[:8]}",
        name=f"合成 P6B-04 发布申请人 {requester.account_id.hex[:8]}",
    )
    binding = role_service.bind(
        owner_context,
        workspace_id=workspace_id,
        role_id=role.role_id,
        scope_type="member",
        department_id=None,
        target_account_id=requester.account_id,
    )
    permission_service.replace(
        owner_context,
        workspace_id=workspace_id,
        role_id=role.role_id,
        entries=tuple(
            (
                permission_code,
                "workspace",
                frozenset(),
                frozenset(),
                "RESTRICTED",
                frozenset(),
            )
            for permission_code in (
                "enterprise.document.publish.request",
                "enterprise.document.publish.read",
                "knowledge.document.version.publish",
            )
        ),
    )
    return requester_context, binding.binding_id


def test_two_level_approval_switches_document_and_index_pointers_atomically(
    knowledge_database: KnowledgeHarness,
) -> None:
    """前一级通过不发布，最终批准才同时切换版本和索引指针。"""

    (
        service,
        approvals,
        workspace_id,
        _,
        approver_a,
        approver_b,
        owner_context,
        approver_a_context,
        approver_b_context,
        document_id,
        version_id,
        index_id,
    ) = _prepare_request(knowledge_database, suffix="approve")
    requested = service.request_document_publish(
        _authorized(owner_context, "enterprise.document.publish.request"),
        workspace_id=workspace_id,
        document_id=document_id,
        document_version_id=version_id,
        idempotency_key="synthetic-p6b04-approve",
    )
    replayed = service.request_document_publish(
        _authorized(owner_context, "enterprise.document.publish.request"),
        workspace_id=workspace_id,
        document_id=document_id,
        document_version_id=version_id,
        idempotency_key="synthetic-p6b04-approve",
    )
    assert replayed.publish_request_id == requested.publish_request_id

    instance_id = requested.approval.approval_instance_id
    with knowledge_database.sessions() as session:
        assert (
            session.scalar(
                select(document_publications.c.current_document_version_id).where(
                    document_publications.c.workspace_id == workspace_id,
                    document_publications.c.document_id == document_id,
                )
            )
            is None
        )
        assert (
            session.scalar(
                select(document_index_publications.c.index_version_id).where(
                    document_index_publications.c.workspace_id == workspace_id,
                    document_index_publications.c.document_id == document_id,
                )
            )
            is None
        )

    first = approvals.act(
        _approval_context(approver_a_context, instance_id),
        approval_instance_id=instance_id,
        command=ApprovalRuntimeCommand(
            "approve", approver_a.account_id, "synthetic-p6b04-approve-level-1"
        ),
    )
    assert first.state.instance.status == "pending"
    with knowledge_database.sessions() as session:
        assert (
            session.scalar(
                select(document_publications.c.current_document_version_id).where(
                    document_publications.c.document_id == document_id
                )
            )
            is None
        )

    second = approvals.act(
        _approval_context(approver_b_context, instance_id),
        approval_instance_id=instance_id,
        command=ApprovalRuntimeCommand(
            "approve", approver_b.account_id, "synthetic-p6b04-approve-level-2"
        ),
    )
    assert second.state.instance.status == "approved"
    final = service.get_document_publish_request(
        _authorized(owner_context, "enterprise.document.publish.read"),
        workspace_id=workspace_id,
        publish_request_id=requested.publish_request_id,
    )
    assert final.status == "published"
    with knowledge_database.sessions() as session:
        assert (
            session.scalar(
                select(document_publications.c.current_document_version_id).where(
                    document_publications.c.document_id == document_id
                )
            )
            == version_id
        )
        assert (
            session.scalar(
                select(document_index_publications.c.index_version_id).where(
                    document_index_publications.c.document_id == document_id
                )
            )
            == index_id
        )
        assert (
            session.scalar(
                select(document_versions.c.status).where(
                    document_versions.c.document_version_id == version_id
                )
            )
            == "published"
        )
        audit_count = session.scalar(
            select(func.count())
            .select_from(audit_records)
            .where(
                audit_records.c.workspace_id == workspace_id,
                audit_records.c.action == "enterprise.document.publish.published",
                audit_records.c.resource_id == requested.publish_request_id,
            )
        )
        outbox_count = session.scalar(
            select(func.count())
            .select_from(outbox_events)
            .where(
                outbox_events.c.workspace_id == workspace_id,
                outbox_events.c.event_type == "enterprise.document.publish.published",
                outbox_events.c.aggregate_id == requested.publish_request_id,
            )
        )
    assert audit_count == outbox_count == 1


def test_index_drift_fails_closed_and_keeps_old_pointers(
    knowledge_database: KnowledgeHarness,
) -> None:
    """批准前索引失效时保留批准事实，但发布请求失败且不产生指针。"""

    (
        service,
        approvals,
        workspace_id,
        _,
        approver,
        _,
        owner_context,
        approver_context,
        _,
        document_id,
        version_id,
        index_id,
    ) = _prepare_request(knowledge_database, suffix="index-drift", two_levels=False)
    requested = service.request_document_publish(
        _authorized(owner_context, "enterprise.document.publish.request"),
        workspace_id=workspace_id,
        document_id=document_id,
        document_version_id=version_id,
        idempotency_key="synthetic-p6b04-index-drift",
    )
    with knowledge_database.sessions.begin() as session:
        session.execute(
            update(index_versions)
            .where(index_versions.c.index_version_id == index_id)
            .values(
                status="failed",
                completed_at=datetime.now(UTC),
                failure_stage="index",
                error_code="INDEX_SYNTHETIC_P6B04_DRIFT",
                error_message="合成索引在审批期间失效",
                chunk_count=None,
                staged_chunk_count=None,
                updated_at=datetime.now(UTC),
            )
        )
    instance_id = requested.approval.approval_instance_id
    approved = approvals.act(
        _approval_context(approver_context, instance_id),
        approval_instance_id=instance_id,
        command=ApprovalRuntimeCommand(
            "approve", approver.account_id, "synthetic-p6b04-index-drift-approve"
        ),
    )
    assert approved.state.instance.status == "approved"
    final = service.get_document_publish_request(
        _authorized(approver_context, "enterprise.document.publish.read"),
        workspace_id=workspace_id,
        publish_request_id=requested.publish_request_id,
    )
    assert final.status == "publish_failed"
    assert final.failure_reason_code == "index_not_ready"
    with knowledge_database.sessions() as session:
        assert (
            session.scalar(
                select(document_publications.c.current_document_version_id).where(
                    document_publications.c.document_id == document_id
                )
            )
            is None
        )
        assert (
            session.scalar(
                select(document_index_publications.c.index_version_id).where(
                    document_index_publications.c.document_id == document_id
                )
            )
            is None
        )


@pytest.mark.parametrize(
    ("drift", "expected_reason"),
    (("governance", "governance_changed"), ("document", "document_inactive")),
)
def test_governance_and_document_drift_fail_closed_before_publication(
    knowledge_database: KnowledgeHarness,
    drift: str,
    expected_reason: str,
) -> None:
    """分类规则或文档生命周期变化后，最终批准不得切换任何发布指针。"""

    (
        service,
        approvals,
        workspace_id,
        _,
        approver,
        _,
        owner_context,
        approver_context,
        _,
        document_id,
        version_id,
        _,
    ) = _prepare_request(
        knowledge_database,
        suffix=f"{drift}-drift",
        two_levels=False,
    )
    requested = service.request_document_publish(
        _authorized(owner_context, "enterprise.document.publish.request"),
        workspace_id=workspace_id,
        document_id=document_id,
        document_version_id=version_id,
        idempotency_key=f"synthetic-p6b04-{drift}-drift",
    )

    if drift == "governance":
        with knowledge_database.sessions() as session:
            category_id = session.scalar(
                select(enterprise_category_documents.c.category_id).where(
                    enterprise_category_documents.c.workspace_id == workspace_id,
                    enterprise_category_documents.c.document_id == document_id,
                )
            )
            category_version = session.scalar(
                select(enterprise_categories.c.version).where(
                    enterprise_categories.c.workspace_id == workspace_id,
                    enterprise_categories.c.category_id == category_id,
                )
            )
        assert isinstance(category_id, UUID)
        assert isinstance(category_version, int)
        service.update_category(
            _authorized(owner_context, "enterprise.category.update"),
            workspace_id=workspace_id,
            category_id=category_id,
            expected_version=category_version,
            name="合成审批分类已变更",
            description=None,
            parent_category_id=None,
            visibility="public",
            department_ids=(),
            approval_required=False,
        )
    else:
        knowledge_base_id = knowledge_database.sessions().scalar(
            select(document_publish_requests.c.knowledge_base_id).where(
                document_publish_requests.c.publish_request_id == requested.publish_request_id
            )
        )
        assert isinstance(knowledge_base_id, UUID)
        knowledge_database.knowledge.delete_document(
            owner_context,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )

    instance_id = requested.approval.approval_instance_id
    result = approvals.act(
        _approval_context(approver_context, instance_id),
        approval_instance_id=instance_id,
        command=ApprovalRuntimeCommand(
            "approve",
            approver.account_id,
            f"synthetic-p6b04-{drift}-drift-approve",
        ),
    )
    assert result.state.instance.status == "approved"
    final = service.get_document_publish_request(
        _authorized(owner_context, "enterprise.document.publish.read"),
        workspace_id=workspace_id,
        publish_request_id=requested.publish_request_id,
    )
    assert final.status == "publish_failed"
    assert final.failure_reason_code == expected_reason
    with knowledge_database.sessions() as session:
        assert (
            session.scalar(
                select(document_publications.c.current_document_version_id).where(
                    document_publications.c.workspace_id == workspace_id,
                    document_publications.c.document_id == document_id,
                )
            )
            is None
        )
        assert (
            session.scalar(
                select(document_index_publications.c.index_version_id).where(
                    document_index_publications.c.workspace_id == workspace_id,
                    document_index_publications.c.document_id == document_id,
                )
            )
            is None
        )


def test_disabled_requester_fails_closed_before_publication(
    knowledge_database: KnowledgeHarness,
) -> None:
    """最终批准前申请人被停用时保留批准事实，但不得发布文档。"""

    (
        service,
        approvals,
        workspace_id,
        _,
        approver,
        _,
        owner_context,
        approver_context,
        _,
        document_id,
        version_id,
        _,
    ) = _prepare_request(knowledge_database, suffix="requester-disabled", two_levels=False)
    requester = register(knowledge_database, identity="p6b04-requester-disabled")
    requester_context, _ = _grant_requester_publish_role(
        knowledge_database,
        workspace_id=workspace_id,
        owner_context=owner_context,
        requester=requester,
    )
    requested = service.request_document_publish(
        _authorized(requester_context, "enterprise.document.publish.request"),
        workspace_id=workspace_id,
        document_id=document_id,
        document_version_id=version_id,
        idempotency_key="synthetic-p6b04-requester-disabled",
    )
    knowledge_database.enterprise.disable_member(
        owner_context,
        workspace_id=workspace_id,
        target_account_id=requester.account_id,
    )

    instance_id = requested.approval.approval_instance_id
    approved = approvals.act(
        _approval_context(approver_context, instance_id),
        approval_instance_id=instance_id,
        command=ApprovalRuntimeCommand(
            "approve", approver.account_id, "synthetic-p6b04-requester-disabled-approve"
        ),
    )
    assert approved.state.instance.status == "approved"
    final = service.get_document_publish_request(
        _authorized(approver_context, "enterprise.document.publish.read"),
        workspace_id=workspace_id,
        publish_request_id=requested.publish_request_id,
    )
    assert final.status == "publish_failed"
    assert final.failure_reason_code == "requester_inactive"
    _assert_no_publication_pointers(
        knowledge_database, workspace_id=workspace_id, document_id=document_id
    )


def test_revoked_publish_role_fails_closed_before_publication(
    knowledge_database: KnowledgeHarness,
) -> None:
    """审批期间撤销申请人的正式角色绑定后，最终批准不得切换指针。"""

    (
        service,
        approvals,
        workspace_id,
        _,
        approver,
        _,
        owner_context,
        approver_context,
        _,
        document_id,
        version_id,
        _,
    ) = _prepare_request(knowledge_database, suffix="permission-revoked", two_levels=False)
    requester = register(knowledge_database, identity="p6b04-permission-revoked")
    requester_context, binding_id = _grant_requester_publish_role(
        knowledge_database,
        workspace_id=workspace_id,
        owner_context=owner_context,
        requester=requester,
    )
    requested = service.request_document_publish(
        _authorized(requester_context, "enterprise.document.publish.request"),
        workspace_id=workspace_id,
        document_id=document_id,
        document_version_id=version_id,
        idempotency_key="synthetic-p6b04-permission-revoked",
    )
    RoleService(SqlAlchemyRoleUnitOfWork(knowledge_database.sessions)).revoke(
        owner_context,
        workspace_id=workspace_id,
        binding_id=binding_id,
    )

    instance_id = requested.approval.approval_instance_id
    approved = approvals.act(
        _approval_context(approver_context, instance_id),
        approval_instance_id=instance_id,
        command=ApprovalRuntimeCommand(
            "approve", approver.account_id, "synthetic-p6b04-permission-revoked-approve"
        ),
    )
    assert approved.state.instance.status == "approved"
    final = service.get_document_publish_request(
        _authorized(approver_context, "enterprise.document.publish.read"),
        workspace_id=workspace_id,
        publish_request_id=requested.publish_request_id,
    )
    assert final.status == "publish_failed"
    assert final.failure_reason_code == "permission_revoked"
    _assert_no_publication_pointers(
        knowledge_database, workspace_id=workspace_id, document_id=document_id
    )


def test_idempotency_conflict_participant_visibility_and_cross_workspace_denial(
    knowledge_database: KnowledgeHarness,
) -> None:
    """同键不同版本、非参与者读取和跨空间资源均稳定拒绝。"""

    (
        service,
        _,
        workspace_id,
        _,
        _,
        _,
        owner_context,
        _,
        _,
        document_id,
        version_id,
        _,
    ) = _prepare_request(knowledge_database, suffix="boundaries")
    requested = service.request_document_publish(
        _authorized(owner_context, "enterprise.document.publish.request"),
        workspace_id=workspace_id,
        document_id=document_id,
        document_version_id=version_id,
        idempotency_key="synthetic-p6b04-boundary",
    )
    other_version_id = uuid4()
    with pytest.raises(EnterpriseKnowledgeConflictError):
        service.request_document_publish(
            _authorized(owner_context, "enterprise.document.publish.request"),
            workspace_id=workspace_id,
            document_id=document_id,
            document_version_id=other_version_id,
            idempotency_key="synthetic-p6b04-boundary",
        )

    outsider = register(knowledge_database, identity="p6b04-boundary-outsider")
    outsider_context = context(outsider, workspace_id)
    with pytest.raises((EnterpriseKnowledgeDeniedError, ApprovalInstanceDenied)):
        service.get_document_publish_request(
            _authorized(outsider_context, "enterprise.document.publish.read"),
            workspace_id=workspace_id,
            publish_request_id=requested.publish_request_id,
        )
    with pytest.raises(EnterpriseKnowledgeDeniedError):
        service.request_document_publish(
            _authorized(owner_context, "enterprise.document.publish.request"),
            workspace_id=outsider.personal_workspace_id,
            document_id=document_id,
            document_version_id=version_id,
            idempotency_key="synthetic-p6b04-cross-workspace",
        )


def test_reject_withdraw_and_timeout_close_requests_without_publication(
    knowledge_database: KnowledgeHarness,
) -> None:
    """驳回、申请人撤回和超时拒绝均保持发布与索引指针为空。"""

    terminal_expectations: tuple[
        tuple[Literal["reject", "withdraw"], Literal["rejected", "withdrawn"]],
        ...,
    ] = (("reject", "rejected"), ("withdraw", "withdrawn"))
    for action, expected in terminal_expectations:
        (
            service,
            approvals,
            workspace_id,
            owner,
            approver,
            _,
            owner_context,
            approver_context,
            _,
            document_id,
            version_id,
            _,
        ) = _prepare_request(knowledge_database, suffix=action, two_levels=False)
        requested = service.request_document_publish(
            _authorized(owner_context, "enterprise.document.publish.request"),
            workspace_id=workspace_id,
            document_id=document_id,
            document_version_id=version_id,
            idempotency_key=f"synthetic-p6b04-{action}",
        )
        actor = owner if action == "withdraw" else approver
        actor_context = owner_context if action == "withdraw" else approver_context
        instance_id = requested.approval.approval_instance_id
        result = approvals.act(
            _approval_context(actor_context, instance_id),
            approval_instance_id=instance_id,
            command=ApprovalRuntimeCommand(
                action, actor.account_id, f"synthetic-p6b04-{action}-command"
            ),
        )
        assert result.state.instance.status == expected
        final = service.get_document_publish_request(
            _authorized(owner_context, "enterprise.document.publish.read"),
            workspace_id=workspace_id,
            publish_request_id=requested.publish_request_id,
        )
        assert final.status == expected

    (
        service,
        approvals,
        workspace_id,
        _,
        _,
        _,
        owner_context,
        _,
        _,
        document_id,
        version_id,
        _,
    ) = _prepare_request(knowledge_database, suffix="timeout", two_levels=False)
    requested = service.request_document_publish(
        _authorized(owner_context, "enterprise.document.publish.request"),
        workspace_id=workspace_id,
        document_id=document_id,
        document_version_id=version_id,
        idempotency_key="synthetic-p6b04-timeout",
    )
    due = approvals.process_due(
        owner_context, limit=10, now=datetime.now(UTC) + timedelta(minutes=3)
    )
    assert any(item.state.instance.status == "rejected" for item in due)
    final = service.get_document_publish_request(
        _authorized(owner_context, "enterprise.document.publish.read"),
        workspace_id=workspace_id,
        publish_request_id=requested.publish_request_id,
    )
    assert final.status == "expired"
    with knowledge_database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(document_publications)
                .where(document_publications.c.document_id == document_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(document_publish_requests)
                .where(
                    document_publish_requests.c.publish_request_id == requested.publish_request_id
                )
            )
            == 1
        )
