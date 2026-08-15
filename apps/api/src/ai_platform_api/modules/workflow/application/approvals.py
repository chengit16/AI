"""编排审批策略不可变版本、授权范围和确定性审批链预计算。"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.integration.domain.events import IntegrationEvent
from ai_platform_api.modules.workflow.domain.approvals import (
    ApprovalApproverSource,
    ApprovalApproverUnavailableError,
    ApprovalChain,
    ApprovalFieldCondition,
    ApprovalLevelDefinition,
    ApprovalPolicy,
    ApprovalPolicyConflictError,
    ApprovalPolicyDefinition,
    ApprovalPolicyNotMatchedError,
    ApprovalPolicyUnitOfWork,
    ApprovalPolicyValidationError,
    ApprovalPolicyVersion,
    ApprovalPolicyWriteConflictError,
    ApprovalRiskLevel,
    ApprovalSubject,
    approval_definition_digest,
    approval_definition_document,
    resolve_approval_chain,
)

_POLICY_NAME_PATTERN = re.compile(r"^\S(?:.{0,118}\S)?$", re.DOTALL)

__all__ = [
    "ApprovalApproverSource",
    "ApprovalApproverUnavailable",
    "ApprovalChain",
    "ApprovalFieldCondition",
    "ApprovalLevelDefinition",
    "ApprovalPolicy",
    "ApprovalPolicyConflict",
    "ApprovalPolicyDefinition",
    "ApprovalPolicyDenied",
    "ApprovalPolicyNotFound",
    "ApprovalPolicyNotMatched",
    "ApprovalPolicyService",
    "ApprovalPolicyValidation",
    "ApprovalPolicyVersion",
    "ApprovalRiskLevel",
    "ApprovalSubject",
    "SecurityLevel",
    "approval_definition_document",
]


class ApprovalPolicyDenied(PlatformError):
    """表示可信上下文没有审批策略资源范围。"""

    error_code = "POLICY_DENIED"


class ApprovalPolicyNotFound(PlatformError):
    """表示审批策略或当前版本在工作空间内不存在。"""

    error_code = "RESOURCE_NOT_FOUND"


class ApprovalPolicyValidation(PlatformError):
    """表示审批策略名称、条件、层级或预计算主题无效。"""

    error_code = "APPROVAL_POLICY_INVALID"


class ApprovalPolicyConflict(PlatformError):
    """表示名称、乐观锁或同等策略匹配发生冲突。"""

    error_code = "APPROVAL_POLICY_CONFLICT"


class ApprovalPolicyNotMatched(PlatformError):
    """表示企业审批主题没有命中活动策略，默认失败关闭。"""

    error_code = "APPROVAL_POLICY_NOT_MATCHED"


class ApprovalApproverUnavailable(PlatformError):
    """表示指定审批层级无法解析出满足约束的活动审批人。"""

    error_code = "APPROVAL_APPROVER_UNAVAILABLE"

    def __init__(self, sequence_no: int) -> None:
        self.sequence_no = sequence_no
        super().__init__(self.error_code)


class ApprovalPolicyService:
    """维护审批策略当前指针，并在同一事务快照中预计算审批链。"""

    def __init__(self, unit_of_work: ApprovalPolicyUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def create(
        self,
        context: RequestContext,
        *,
        name: str,
        definition: ApprovalPolicyDefinition,
    ) -> tuple[ApprovalPolicy, ApprovalPolicyVersion]:
        """创建策略身份和首个不可变版本，定义正文不进入审计或 Outbox。"""

        # 1. 在事务外规范名称并校验完整定义，避免无效规则进入版本表或占用唯一名称。
        account_id = _browser_account(context)
        normalized_name = _normalize_name(name)
        digest = _definition_digest(definition)
        now = datetime.now(UTC)
        policy_id = uuid4()
        version_id = uuid4()
        policy = ApprovalPolicy(
            policy_id,
            context.workspace_id,
            normalized_name,
            "active",
            version_id,
            account_id,
            now,
            now,
            1,
        )
        version = ApprovalPolicyVersion(
            version_id,
            policy_id,
            context.workspace_id,
            1,
            definition,
            digest,
            account_id,
            now,
        )
        # 2. 身份、首版本、审计与 Outbox 必须原子提交，延迟外键在提交点验证指针。
        try:
            with self._unit_of_work as unit_of_work:
                unit_of_work.approvals.add_policy(policy, version)
                _record_change(unit_of_work, context, policy, version, "created", now)
                unit_of_work.commit()
        except ApprovalPolicyWriteConflictError as error:
            raise ApprovalPolicyConflict from error
        return policy, version

    def list(
        self,
        context: RequestContext,
        *,
        limit: int,
    ) -> tuple[ApprovalPolicy, ...]:
        """列出当前授权范围内的策略身份，不把不可变定义正文批量展开。"""

        _browser_account(context)
        if not 1 <= limit <= 200:
            raise ApprovalPolicyValidation
        with self._unit_of_work as unit_of_work:
            policies = unit_of_work.approvals.list_policies(context.workspace_id)[:limit]
        if context.authorized_workspace:
            return policies
        return tuple(
            policy
            for policy in policies
            if policy.approval_policy_id in context.authorized_resource_ids
        )

    def get(
        self,
        context: RequestContext,
        *,
        approval_policy_id: UUID,
    ) -> tuple[ApprovalPolicy, ApprovalPolicyVersion]:
        """读取策略身份及当前不可变版本，历史定义不会被当前指针覆盖。"""

        _browser_account(context)
        _require_scope(context, approval_policy_id)
        with self._unit_of_work as unit_of_work:
            policy = _require_policy(
                unit_of_work,
                context.workspace_id,
                approval_policy_id,
            )
            version = unit_of_work.approvals.get_version(
                context.workspace_id,
                policy.current_version_id,
            )
            if version is None:
                raise ApprovalPolicyNotFound
            return policy, version

    def revise(
        self,
        context: RequestContext,
        *,
        approval_policy_id: UUID,
        expected_version: int,
        definition: ApprovalPolicyDefinition,
    ) -> tuple[ApprovalPolicy, ApprovalPolicyVersion]:
        """以乐观锁新增不可变版本并原子切换当前指针，旧版本保持可追溯。"""

        # 1. 事务前校验可信账号、资源范围和定义上限，避免持锁后处理无效 JSON。
        account_id = _browser_account(context)
        _require_scope(context, approval_policy_id)
        if expected_version < 1:
            raise ApprovalPolicyValidation
        digest = _definition_digest(definition)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                # 2. 锁定身份后分配版本号，并与指针、审计和 Outbox 在同一事务提交。
                current = _require_policy(
                    unit_of_work,
                    context.workspace_id,
                    approval_policy_id,
                    for_update=True,
                )
                if current.status != "active" or current.version != expected_version:
                    raise ApprovalPolicyConflict
                version = ApprovalPolicyVersion(
                    uuid4(),
                    current.approval_policy_id,
                    current.workspace_id,
                    unit_of_work.approvals.next_version_number(
                        current.workspace_id,
                        current.approval_policy_id,
                    ),
                    definition,
                    digest,
                    account_id,
                    now,
                )
                updated = replace(
                    current,
                    current_version_id=version.approval_policy_version_id,
                    updated_at=now,
                    version=current.version + 1,
                )
                if not unit_of_work.approvals.revise_policy(
                    updated,
                    version,
                    expected_version=current.version,
                ):
                    raise ApprovalPolicyConflict
                _record_change(unit_of_work, context, updated, version, "revised", now)
                unit_of_work.commit()
                return updated, version
        except ApprovalPolicyWriteConflictError as error:
            raise ApprovalPolicyConflict from error

    def preview_chain(
        self,
        context: RequestContext,
        *,
        subject: ApprovalSubject,
    ) -> ApprovalChain:
        """在一个数据库快照中选择策略并解析审批人，不创建 P1F-04 审批实例。"""

        account_id = _browser_account(context)
        if (
            subject.workspace_id != context.workspace_id
            or subject.requester_account_id != account_id
        ):
            raise ApprovalPolicyDenied
        try:
            with self._unit_of_work as unit_of_work:
                versions = unit_of_work.approvals.list_active_versions(context.workspace_id)
                return resolve_approval_chain(subject, versions, unit_of_work.directory)
        except ApprovalPolicyValidationError as error:
            raise ApprovalPolicyValidation from error
        except ApprovalPolicyConflictError as error:
            raise ApprovalPolicyConflict from error
        except ApprovalPolicyNotMatchedError as error:
            raise ApprovalPolicyNotMatched from error
        except ApprovalApproverUnavailableError as error:
            raise ApprovalApproverUnavailable(error.sequence_no) from error


def _browser_account(context: RequestContext) -> UUID:
    if context.authentication_method != "browser_session" or context.user_id is None:
        raise ApprovalPolicyDenied
    if context.user_id != context.actor_id:
        raise ApprovalPolicyDenied
    return context.user_id


def _require_scope(context: RequestContext, approval_policy_id: UUID) -> None:
    if (
        not context.authorized_workspace
        and approval_policy_id not in context.authorized_resource_ids
    ):
        raise ApprovalPolicyDenied


def _normalize_name(value: str) -> str:
    normalized = value.strip()
    if not _POLICY_NAME_PATTERN.fullmatch(normalized):
        raise ApprovalPolicyValidation
    return normalized


def _definition_digest(definition: ApprovalPolicyDefinition) -> str:
    try:
        return approval_definition_digest(definition)
    except ApprovalPolicyValidationError as error:
        raise ApprovalPolicyValidation from error


def _require_policy(
    unit_of_work: ApprovalPolicyUnitOfWork,
    workspace_id: UUID,
    approval_policy_id: UUID,
    *,
    for_update: bool = False,
) -> ApprovalPolicy:
    policy = unit_of_work.approvals.get_policy(
        workspace_id,
        approval_policy_id,
        for_update=for_update,
    )
    if policy is None:
        raise ApprovalPolicyNotFound
    return policy


def _record_change(
    unit_of_work: ApprovalPolicyUnitOfWork,
    context: RequestContext,
    policy: ApprovalPolicy,
    version: ApprovalPolicyVersion,
    change: str,
    occurred_at: datetime,
) -> None:
    """只记录版本号与摘要，条件值和审批人引用不进入审计或集成事件。"""

    action = f"approval.policy.{change}"
    attributes: dict[str, object] = {
        "approval_policy_version_id": str(version.approval_policy_version_id),
        "version_number": version.version_number,
        "definition_digest": version.definition_digest,
    }
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type="approval_policy",
            resource_id=policy.approval_policy_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes=attributes,
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=action,
            workspace_id=context.workspace_id,
            aggregate_id=policy.approval_policy_id,
            aggregate_version=policy.version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=attributes,
        )
    )
