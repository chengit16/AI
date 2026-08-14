"""验证 P1F-03 多级审批定义、策略选择和确定性审批链边界。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import pytest
from ai_platform_api.modules.workflow.domain.approvals import (
    ApprovalApproverSource,
    ApprovalApproverUnavailableError,
    ApprovalFieldCondition,
    ApprovalLevelDefinition,
    ApprovalLevelMode,
    ApprovalMemberIdentity,
    ApprovalOrganizationDirectory,
    ApprovalPolicyConflictError,
    ApprovalPolicyDefinition,
    ApprovalPolicyNotMatchedError,
    ApprovalPolicyValidationError,
    ApprovalPolicyVersion,
    ApprovalSourceType,
    ApprovalSubject,
    ApprovalTimeoutAction,
    ApprovalWorkspaceIdentity,
    approval_definition_digest,
    resolve_approval_chain,
    select_approval_policy,
    validate_approval_definition,
)

WORKSPACE_ID = UUID("f3000000-0000-4000-8000-000000000001")
REQUESTER_ID = UUID("f3000000-0000-4000-8000-000000000002")
OWNER_ID = UUID("f3000000-0000-4000-8000-000000000003")
APPROVER_A_ID = UUID("f3000000-0000-4000-8000-000000000004")
APPROVER_B_ID = UUID("f3000000-0000-4000-8000-000000000005")
DEPARTMENT_ID = UUID("f3000000-0000-4000-8000-000000000006")
ROLE_ID = UUID("f3000000-0000-4000-8000-000000000007")
POLICY_ID = UUID("f3000000-0000-4000-8000-000000000008")
VERSION_ID = UUID("f3000000-0000-4000-8000-000000000009")
NOW = datetime(2026, 8, 15, 8, tzinfo=UTC)


class SyntheticApprovalDirectory(ApprovalOrganizationDirectory):
    """以固定合成组织事实隔离领域计算，避免单元测试依赖数据库查询。"""

    def __init__(
        self,
        *,
        workspace_type: Literal["personal", "enterprise"] = "enterprise",
        source_accounts: dict[str, tuple[UUID, ...]] | None = None,
        requester_active: bool = True,
    ) -> None:
        self.workspace_type = workspace_type
        self.source_accounts = source_accounts or {}
        self.requester_active = requester_active

    def workspace_identity(self, workspace_id: UUID) -> ApprovalWorkspaceIdentity | None:
        if workspace_id != WORKSPACE_ID:
            return None
        return ApprovalWorkspaceIdentity(workspace_id, self.workspace_type, OWNER_ID)

    def member_identity(
        self,
        workspace_id: UUID,
        account_id: UUID,
    ) -> ApprovalMemberIdentity | None:
        if workspace_id != WORKSPACE_ID or account_id != REQUESTER_ID:
            return None
        return ApprovalMemberIdentity(
            account_id,
            self.requester_active,
            (DEPARTMENT_ID,),
            DEPARTMENT_ID,
        )

    def resolve_source(
        self,
        workspace_id: UUID,
        source: ApprovalApproverSource,
        *,
        requester: ApprovalMemberIdentity,
        resource_department_ids: tuple[UUID, ...],
    ) -> tuple[UUID, ...]:
        assert workspace_id == WORKSPACE_ID
        assert requester.account_id == REQUESTER_ID
        assert resource_department_ids == (DEPARTMENT_ID,)
        return self.source_accounts.get(source.source_type, ())


def source(
    source_type: ApprovalSourceType = "roles",
    *,
    reference_ids: tuple[UUID, ...] = (ROLE_ID,),
    levels_up: int | None = None,
) -> ApprovalApproverSource:
    """构造使用合成标识的审批人来源。"""

    return ApprovalApproverSource(source_type, reference_ids, levels_up)


def level(
    sequence_no: int = 1,
    *,
    mode: ApprovalLevelMode = "any",
    sources: tuple[ApprovalApproverSource, ...] | None = None,
    timeout_action: ApprovalTimeoutAction = "wait",
    fallback_sources: tuple[ApprovalApproverSource, ...] = (),
) -> ApprovalLevelDefinition:
    """构造可按场景覆盖通过模式和超时动作的审批层级。"""

    return ApprovalLevelDefinition(
        sequence_no,
        mode,
        sources or (source(),),
        timeout_action=timeout_action,
        fallback_sources=fallback_sources,
    )


def definition(
    *,
    priority: int = 100,
    levels: tuple[ApprovalLevelDefinition, ...] | None = None,
    field_conditions: tuple[ApprovalFieldCondition, ...] = (),
    allow_self_approval: bool = False,
) -> ApprovalPolicyDefinition:
    """构造默认命中合同审批主题的版本化定义。"""

    return ApprovalPolicyDefinition(
        resource_type="workflow.approval",
        operation="submit",
        priority=priority,
        department_ids=(DEPARTMENT_ID,),
        security_levels=("CONFIDENTIAL",),
        risk_levels=("high",),
        field_conditions=field_conditions,
        levels=levels or (level(),),
        allow_self_approval=allow_self_approval,
    )


def version(
    value: ApprovalPolicyDefinition | None = None,
    *,
    policy_id: UUID = POLICY_ID,
    version_id: UUID = VERSION_ID,
) -> ApprovalPolicyVersion:
    """构造已冻结且摘要与定义一致的策略版本。"""

    policy_definition = value or definition()
    return ApprovalPolicyVersion(
        version_id,
        policy_id,
        WORKSPACE_ID,
        1,
        policy_definition,
        approval_definition_digest(policy_definition),
        OWNER_ID,
        NOW,
    )


def subject(*, amount: object = 1_500, fields: dict[str, object] | None = None) -> ApprovalSubject:
    """构造只含合成业务字段的企业审批主题。"""

    return ApprovalSubject(
        WORKSPACE_ID,
        REQUESTER_ID,
        "workflow.approval",
        "submit",
        None,
        (DEPARTMENT_ID,),
        "CONFIDENTIAL",
        "high",
        fields or {"amount": amount, "contract": {"region": "cn"}},
    )


def test_definition_accepts_five_serial_levels_and_any_all_modes() -> None:
    levels = tuple(
        level(sequence, mode="any" if sequence % 2 else "all") for sequence in range(1, 6)
    )
    value = definition(levels=levels)

    validate_approval_definition(value)
    assert approval_definition_digest(value) == approval_definition_digest(value)

    with pytest.raises(ApprovalPolicyValidationError):
        validate_approval_definition(definition(levels=(*levels, level(6))))
    with pytest.raises(ApprovalPolicyValidationError):
        validate_approval_definition(definition(levels=(level(1), level(3))))


def test_field_and_amount_conditions_select_highest_ranked_policy() -> None:
    generic = replace(
        definition(priority=50),
        department_ids=(),
        security_levels=(),
        risk_levels=(),
    )
    amount_specific = definition(
        priority=100,
        field_conditions=(
            ApprovalFieldCondition("amount", "gte", 1_000),
            ApprovalFieldCondition("contract.region", "in", ["cn", "sg"]),
        ),
    )

    selected = select_approval_policy(
        (
            version(generic),
            version(
                amount_specific,
                policy_id=UUID("f3000000-0000-4000-8000-000000000010"),
                version_id=UUID("f3000000-0000-4000-8000-000000000011"),
            ),
        ),
        subject(),
    )

    assert selected.definition == amount_specific
    with pytest.raises(ApprovalPolicyNotMatchedError):
        select_approval_policy((version(amount_specific),), subject(amount="1500"))


def test_equal_priority_and_specificity_conflict_is_not_broken_by_identifier() -> None:
    first = version(definition())
    second = version(
        definition(),
        policy_id=UUID("f3000000-0000-4000-8000-000000000012"),
        version_id=UUID("f3000000-0000-4000-8000-000000000013"),
    )

    with pytest.raises(ApprovalPolicyConflictError):
        select_approval_policy((first, second), subject())


def test_personal_workspace_always_returns_owner_confirmation() -> None:
    directory = SyntheticApprovalDirectory(workspace_type="personal")

    chain = resolve_approval_chain(subject(), (), directory)

    assert chain.personal_owner_confirmation is True
    assert chain.approval_policy_version_id is None
    assert chain.levels[0].mode == "all"
    assert chain.levels[0].approver_account_ids == (OWNER_ID,)


def test_enterprise_removes_self_and_returns_stable_sorted_chain() -> None:
    directory = SyntheticApprovalDirectory(
        source_accounts={"roles": (APPROVER_B_ID, REQUESTER_ID, APPROVER_A_ID)}
    )
    policy_version = version(definition(levels=(level(1, mode="all"),)))

    first = resolve_approval_chain(subject(), (policy_version,), directory)
    second = resolve_approval_chain(subject(), (policy_version,), directory)

    assert first == second
    assert first.chain_digest == second.chain_digest
    assert first.levels[0].approver_account_ids == (APPROVER_A_ID, APPROVER_B_ID)


@pytest.mark.parametrize(
    ("value", "expected_sequence"),
    [
        (definition(), 1),
        (
            definition(
                levels=(
                    level(
                        timeout_action="transfer",
                        fallback_sources=(source("accounts"),),
                    ),
                )
            ),
            1,
        ),
    ],
)
def test_missing_primary_or_fallback_approver_reports_level(
    value: ApprovalPolicyDefinition,
    expected_sequence: int,
) -> None:
    directory = SyntheticApprovalDirectory(source_accounts={"roles": (REQUESTER_ID,)})

    with pytest.raises(ApprovalApproverUnavailableError) as captured:
        resolve_approval_chain(subject(), (version(value),), directory)

    assert captured.value.sequence_no == expected_sequence


def test_timeout_escalation_requires_configured_fallback_source() -> None:
    value = definition(levels=(level(timeout_action="escalate"),))

    with pytest.raises(ApprovalPolicyValidationError):
        validate_approval_definition(value)


def test_contains_exists_and_invalid_expected_values_have_closed_semantics() -> None:
    conditions = (
        ApprovalFieldCondition("contract.region", "contains", "c"),
        ApprovalFieldCondition("contract.optional", "exists"),
    )
    value = definition(field_conditions=conditions)
    matching = subject(fields={"contract": {"region": "cn", "optional": None}})

    assert select_approval_policy((version(value),), matching).definition == value
    with pytest.raises(ApprovalPolicyValidationError):
        approval_definition_digest(
            definition(field_conditions=(ApprovalFieldCondition("amount", "eq", object()),))
        )
    with pytest.raises(ApprovalPolicyValidationError):
        validate_approval_definition(
            definition(field_conditions=(ApprovalFieldCondition("amount", "gte", "1000"),))
        )
    with pytest.raises(ApprovalPolicyValidationError):
        validate_approval_definition(
            definition(field_conditions=(ApprovalFieldCondition("region", "in", []),))
        )
