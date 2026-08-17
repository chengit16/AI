"""实现套餐上限、合规资格和迁移状态机的确定性策略。"""

from __future__ import annotations

from ai_platform_api.modules.isolation.domain.models import (
    IsolationComplianceAssessment,
    IsolationDecision,
    IsolationLevel,
    IsolationPolicyEvaluation,
    MigrationStatus,
    WorkspaceIsolationEntitlement,
)

ISOLATION_LEVELS: tuple[IsolationLevel, ...] = ("L1", "L2", "L3", "L4")
PLAN_MAXIMUM_LEVELS: dict[str, IsolationLevel] = {
    "personal_local": "L1",
    "enterprise_simulated": "L2",
    "enterprise_isolated": "L3",
    "enterprise_private": "L4",
}
MIGRATION_TRANSITIONS: dict[MigrationStatus, frozenset[MigrationStatus]] = {
    "planned": frozenset({"approved", "cancelled"}),
    "approved": frozenset({"executing", "cancelled"}),
    "executing": frozenset({"verifying", "rollback_required"}),
    "verifying": frozenset({"switch_ready", "rollback_required"}),
    "switch_ready": frozenset({"completed", "rollback_required"}),
    "rollback_required": frozenset({"rolled_back"}),
    "completed": frozenset(),
    "rolled_back": frozenset(),
    "cancelled": frozenset(),
}
ISOLATION_REASON_CODES = frozenset(
    {
        "workspace_inactive",
        "personal_workspace_l1_only",
        "plan_not_supported",
        "plan_not_eligible",
        "compliance_policy_not_configured",
        "compliance_policy_rejected",
    }
)


def level_index(level: IsolationLevel) -> int:
    """返回隔离等级的单调序号，供资格与升级方向比较。"""

    return ISOLATION_LEVELS.index(level)


def evaluate_isolation_policy(
    entitlement: WorkspaceIsolationEntitlement,
    target_level: IsolationLevel,
    compliance: IsolationComplianceAssessment,
) -> IsolationPolicyEvaluation:
    """根据权威套餐和合规结果判定等级资格，未知条件默认拒绝。"""

    # 1. 先按空间状态、类型和套餐上限计算基础资格，未知套餐只保留 L1。
    maximum = PLAN_MAXIMUM_LEVELS.get(entitlement.plan_code, "L1")
    reasons: set[str] = set()
    decision: IsolationDecision = "allowed"
    if entitlement.workspace_status != "active":
        reasons.add("workspace_inactive")
        decision = "denied"
    if entitlement.plan_code not in PLAN_MAXIMUM_LEVELS:
        reasons.add("plan_not_supported")
        decision = "denied"
    if entitlement.workspace_type == "personal" and target_level != "L1":
        maximum = "L1"
        reasons.add("personal_workspace_l1_only")
        decision = "denied"
    elif level_index(target_level) > level_index(maximum):
        reasons.add("plan_not_eligible")
        decision = "denied"

    # 2. L3/L4 必须消费已配置且通过的合规策略，L1/L2 不借用外部审核抬高资格。
    if target_level in {"L3", "L4"}:
        if compliance.status == "not_configured":
            reasons.add("compliance_policy_not_configured")
            if decision == "allowed":
                decision = "not_configured"
        elif compliance.status != "approved":
            reasons.add("compliance_policy_rejected")
            decision = "denied"
    return IsolationPolicyEvaluation(
        maximum_eligible_level=maximum,
        compliance_status=compliance.status,
        compliance_policy_digest=compliance.policy_digest,
        decision=decision,
        reason_codes=tuple(sorted(reasons)),
    )
