"""验证 P1F-03 审批策略应用服务的版本、授权、事务和错误映射。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import TracebackType
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.integration.domain.events import IntegrationEvent
from ai_platform_api.modules.workflow.application.approvals import (
    ApprovalApproverUnavailable,
    ApprovalPolicyConflict,
    ApprovalPolicyDenied,
    ApprovalPolicyService,
)
from ai_platform_api.modules.workflow.domain.approvals import (
    ApprovalApproverSource,
    ApprovalLevelDefinition,
    ApprovalMemberIdentity,
    ApprovalPolicy,
    ApprovalPolicyDefinition,
    ApprovalPolicyVersion,
    ApprovalSubject,
    ApprovalWorkspaceIdentity,
)
from ai_platform_backend.integration.domain import AuditRecord

WORKSPACE_ID = UUID("f3100000-0000-4000-8000-000000000001")
ACCOUNT_ID = UUID("f3100000-0000-4000-8000-000000000002")
APPROVER_ID = UUID("f3100000-0000-4000-8000-000000000003")
DEPARTMENT_ID = UUID("f3100000-0000-4000-8000-000000000004")
ROLE_ID = UUID("f3100000-0000-4000-8000-000000000005")
TRACE = TraceContext("a" * 32, "b" * 16)


class MemoryApprovalRepository:
    """保留策略身份和全部历史版本，模拟乐观锁而不模拟 SQL 约束。"""

    def __init__(self) -> None:
        self.policies: dict[UUID, ApprovalPolicy] = {}
        self.versions: dict[UUID, ApprovalPolicyVersion] = {}

    def list_policies(self, workspace_id: UUID) -> tuple[ApprovalPolicy, ...]:
        return tuple(
            policy for policy in self.policies.values() if policy.workspace_id == workspace_id
        )

    def get_policy(
        self,
        workspace_id: UUID,
        approval_policy_id: UUID,
        *,
        for_update: bool = False,
    ) -> ApprovalPolicy | None:
        del for_update
        policy = self.policies.get(approval_policy_id)
        return policy if policy is not None and policy.workspace_id == workspace_id else None

    def get_version(
        self,
        workspace_id: UUID,
        approval_policy_version_id: UUID,
    ) -> ApprovalPolicyVersion | None:
        version = self.versions.get(approval_policy_version_id)
        return version if version is not None and version.workspace_id == workspace_id else None

    def list_active_versions(self, workspace_id: UUID) -> tuple[ApprovalPolicyVersion, ...]:
        return tuple(
            self.versions[policy.current_version_id]
            for policy in self.policies.values()
            if policy.workspace_id == workspace_id and policy.status == "active"
        )

    def next_version_number(self, workspace_id: UUID, approval_policy_id: UUID) -> int:
        numbers = (
            version.version_number
            for version in self.versions.values()
            if version.workspace_id == workspace_id
            and version.approval_policy_id == approval_policy_id
        )
        return max(numbers, default=0) + 1

    def add_policy(self, policy: ApprovalPolicy, version: ApprovalPolicyVersion) -> None:
        self.policies[policy.approval_policy_id] = policy
        self.versions[version.approval_policy_version_id] = version

    def revise_policy(
        self,
        policy: ApprovalPolicy,
        version: ApprovalPolicyVersion,
        *,
        expected_version: int,
    ) -> bool:
        current = self.policies.get(policy.approval_policy_id)
        if current is None or current.version != expected_version:
            return False
        self.versions[version.approval_policy_version_id] = version
        self.policies[policy.approval_policy_id] = policy
        return True


class MemoryApprovalDirectory:
    """返回固定合成组织事实，并允许测试显式移除全部审批人。"""

    def __init__(self) -> None:
        self.approvers: tuple[UUID, ...] = (APPROVER_ID,)

    def workspace_identity(self, workspace_id: UUID) -> ApprovalWorkspaceIdentity | None:
        if workspace_id != WORKSPACE_ID:
            return None
        return ApprovalWorkspaceIdentity(WORKSPACE_ID, "enterprise", ACCOUNT_ID)

    def member_identity(
        self,
        workspace_id: UUID,
        account_id: UUID,
    ) -> ApprovalMemberIdentity | None:
        if workspace_id != WORKSPACE_ID or account_id != ACCOUNT_ID:
            return None
        return ApprovalMemberIdentity(ACCOUNT_ID, True, (DEPARTMENT_ID,), DEPARTMENT_ID)

    def resolve_source(
        self,
        workspace_id: UUID,
        source: ApprovalApproverSource,
        *,
        requester: ApprovalMemberIdentity,
        resource_department_ids: tuple[UUID, ...],
    ) -> tuple[UUID, ...]:
        del source, requester, resource_department_ids
        return self.approvers if workspace_id == WORKSPACE_ID else ()


@dataclass
class MemoryAuditWriter:
    records: list[AuditRecord]

    def add(self, record: AuditRecord) -> None:
        self.records.append(record)


@dataclass
class MemoryOutboxWriter:
    events: list[IntegrationEvent]

    def add(self, event: IntegrationEvent) -> None:
        self.events.append(event)


class MemoryApprovalUnitOfWork:
    """复用内存事实并记录提交次数，供应用服务验证原子写入意图。"""

    def __init__(self) -> None:
        self.approvals = MemoryApprovalRepository()
        self.directory = MemoryApprovalDirectory()
        self.audit = MemoryAuditWriter([])
        self.outbox = MemoryOutboxWriter([])
        self.commit_count = 0

    def __enter__(self) -> MemoryApprovalUnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    def commit(self) -> None:
        self.commit_count += 1


def context() -> RequestContext:
    """构造具有工作空间范围的可信浏览器上下文。"""

    return replace(
        RequestContext.trusted(
            actor_id=ACCOUNT_ID,
            user_id=ACCOUNT_ID,
            workspace_id=WORKSPACE_ID,
            trace=TRACE,
            authentication_method="browser_session",
        ),
        authorized_workspace=True,
    )


def definition(*, priority: int = 100) -> ApprovalPolicyDefinition:
    """构造只包含合成 ID 的单级企业审批策略。"""

    return ApprovalPolicyDefinition(
        "workflow.approval",
        "submit",
        priority,
        (DEPARTMENT_ID,),
        ("INTERNAL",),
        ("normal",),
        (),
        (
            ApprovalLevelDefinition(
                1,
                "any",
                (ApprovalApproverSource("roles", (ROLE_ID,)),),
            ),
        ),
    )


def subject() -> ApprovalSubject:
    """构造与默认策略完全匹配的合成审批主题。"""

    return ApprovalSubject(
        WORKSPACE_ID,
        ACCOUNT_ID,
        "workflow.approval",
        "submit",
        None,
        (DEPARTMENT_ID,),
        "INTERNAL",
        "normal",
        {},
    )


def test_create_and_revise_preserve_immutable_history_and_transactional_events() -> None:
    unit_of_work = MemoryApprovalUnitOfWork()
    service = ApprovalPolicyService(unit_of_work)

    policy, first = service.create(context(), name=" 合成合同审批 ", definition=definition())
    revised_policy, second = service.revise(
        context(),
        approval_policy_id=policy.approval_policy_id,
        expected_version=policy.version,
        definition=definition(priority=200),
    )

    assert policy.name == "合成合同审批"
    assert first.version_number == 1
    assert second.version_number == 2
    assert revised_policy.current_version_id == second.approval_policy_version_id
    assert unit_of_work.approvals.versions[first.approval_policy_version_id] == first
    assert unit_of_work.commit_count == 2
    assert [record.action for record in unit_of_work.audit.records] == [
        "approval.policy.created",
        "approval.policy.revised",
    ]
    assert len(unit_of_work.outbox.events) == 2

    with pytest.raises(ApprovalPolicyConflict):
        service.revise(
            context(),
            approval_policy_id=policy.approval_policy_id,
            expected_version=1,
            definition=definition(priority=300),
        )


def test_resource_scope_filters_list_and_blocks_untrusted_actor() -> None:
    unit_of_work = MemoryApprovalUnitOfWork()
    service = ApprovalPolicyService(unit_of_work)
    policy, _ = service.create(context(), name="合成范围审批", definition=definition())
    resource_context = replace(
        context(),
        authorized_workspace=False,
        authorized_resource_ids=frozenset({policy.approval_policy_id}),
    )

    assert service.list(resource_context, limit=10) == (policy,)
    assert (
        service.get(
            resource_context,
            approval_policy_id=policy.approval_policy_id,
        )[0]
        == policy
    )

    with pytest.raises(ApprovalPolicyDenied):
        service.list(replace(context(), authentication_method="api_key"), limit=10)


def test_preview_maps_empty_enterprise_approver_to_stable_application_error() -> None:
    unit_of_work = MemoryApprovalUnitOfWork()
    service = ApprovalPolicyService(unit_of_work)
    service.create(context(), name="合成审批人缺失", definition=definition())
    unit_of_work.directory.approvers = ()

    with pytest.raises(ApprovalApproverUnavailable) as captured:
        service.preview_chain(context(), subject=subject())

    assert captured.value.sequence_no == 1
