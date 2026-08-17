"""定义版本化隔离策略、迁移计划、路由和持久化端口。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter

IsolationLevel = Literal["L1", "L2", "L3", "L4"]
WorkspaceType = Literal["personal", "enterprise"]
WorkspaceStatus = Literal["active", "suspended", "archived"]
ComplianceStatus = Literal["not_required", "not_configured", "approved", "rejected"]
IsolationDecision = Literal["allowed", "denied", "not_configured"]
MigrationStatus = Literal[
    "planned",
    "approved",
    "executing",
    "verifying",
    "switch_ready",
    "completed",
    "rollback_required",
    "rolled_back",
    "cancelled",
]
ACTIVE_MIGRATION_STATUSES: tuple[MigrationStatus, ...] = (
    "planned",
    "approved",
    "executing",
    "verifying",
    "switch_ready",
    "rollback_required",
)


@dataclass(frozen=True)
class WorkspaceIsolationEntitlement:
    """向 Isolation 模块公开最小工作空间与套餐事实。"""

    workspace_id: UUID
    workspace_type: WorkspaceType
    workspace_status: WorkspaceStatus
    plan_code: str
    entitlement_version: int


@dataclass(frozen=True)
class IsolationComplianceAssessment:
    """表示受信合规策略对目标等级的审核状态与低敏摘要。"""

    status: ComplianceStatus
    policy_digest: str | None


@dataclass(frozen=True)
class IsolationPolicyEvaluation:
    """返回套餐上限、合规状态、决定和稳定原因码。"""

    maximum_eligible_level: IsolationLevel
    compliance_status: ComplianceStatus
    compliance_policy_digest: str | None
    decision: IsolationDecision
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class WorkspaceIsolationPolicyVersion:
    """冻结一次目标等级资格判定，历史版本不得改写。"""

    isolation_policy_id: UUID
    workspace_id: UUID
    policy_version: int
    requested_level: IsolationLevel
    current_level: IsolationLevel
    maximum_eligible_level: IsolationLevel
    plan_code: str
    entitlement_version: int
    compliance_status: ComplianceStatus
    compliance_policy_digest: str | None
    decision: IsolationDecision
    reason_codes: tuple[str, ...]
    decision_digest: str
    created_by_actor_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class WorkspaceIsolationMigrationPlan:
    """保存单一权威写入位置下的升级状态和路由要求。"""

    migration_plan_id: UUID
    workspace_id: UUID
    isolation_policy_id: UUID
    from_level: IsolationLevel
    target_level: IsolationLevel
    status: MigrationStatus
    route_requirement_digest: str
    created_by_actor_id: UUID
    created_at: datetime
    updated_by_actor_id: UUID
    updated_at: datetime
    version: int

    def transition(
        self,
        target_status: MigrationStatus,
        *,
        actor_id: UUID,
        occurred_at: datetime,
        allowed: dict[MigrationStatus, frozenset[MigrationStatus]],
    ) -> WorkspaceIsolationMigrationPlan:
        """按冻结状态机推进计划，终态和越级转换统一拒绝。"""

        if target_status not in allowed[self.status] or occurred_at < self.updated_at:
            raise InvalidIsolationMigrationTransitionError
        return replace(
            self,
            status=target_status,
            updated_by_actor_id=actor_id,
            updated_at=occurred_at,
            version=self.version + 1,
        )


@dataclass(frozen=True)
class WorkspaceIsolationRoute:
    """保存不含凭证的服务端数据平面路由键。"""

    route_id: UUID
    workspace_id: UUID
    route_version: int
    isolation_level: IsolationLevel
    database_route_key: str
    object_storage_route_key: str
    encryption_key_route_key: str
    search_namespace: str
    deployment_route_key: str
    migration_plan_id: UUID | None
    route_digest: str
    activated_by_actor_id: UUID
    activated_at: datetime


@dataclass(frozen=True)
class IsolationUpgradeResult:
    """组合资格决策与可选迁移计划，拒绝时不制造计划。"""

    policy: WorkspaceIsolationPolicyVersion
    migration_plan: WorkspaceIsolationMigrationPlan | None


class IsolationComplianceSource(Protocol):
    """由受信平台策略提供目标等级合规状态，浏览器不能直接提交结论。"""

    def assess(
        self,
        entitlement: WorkspaceIsolationEntitlement,
        target_level: IsolationLevel,
    ) -> IsolationComplianceAssessment: ...


class IsolationRepository(Protocol):
    """读取套餐事实并维护 Isolation 模块拥有的策略、计划和路由。"""

    def lock_workspace(self, workspace_id: UUID) -> None: ...

    def get_entitlement(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceIsolationEntitlement | None: ...

    def next_policy_version(self, workspace_id: UUID) -> int: ...

    def add_policy(self, policy: WorkspaceIsolationPolicyVersion) -> None: ...

    def get_policy(
        self,
        workspace_id: UUID,
        isolation_policy_id: UUID,
    ) -> WorkspaceIsolationPolicyVersion | None: ...

    def add_migration_plan(self, plan: WorkspaceIsolationMigrationPlan) -> None: ...

    def get_active_migration_plan(
        self,
        workspace_id: UUID,
    ) -> WorkspaceIsolationMigrationPlan | None: ...

    def get_migration_plan(
        self,
        workspace_id: UUID,
        migration_plan_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceIsolationMigrationPlan | None: ...

    def save_migration_plan(
        self,
        plan: WorkspaceIsolationMigrationPlan,
        *,
        expected_version: int,
    ) -> bool: ...

    def get_current_route(self, workspace_id: UUID) -> WorkspaceIsolationRoute | None: ...

    def next_route_version(self, workspace_id: UUID) -> int: ...

    def add_route(self, route: WorkspaceIsolationRoute) -> None: ...


class IsolationUnitOfWork(Protocol):
    """保证策略、迁移、路由、审计与事件在同一事务提交。"""

    @property
    def isolation(self) -> IsolationRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> IsolationUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class IsolationWriteConflictError(Exception):
    """隔离策略、迁移或路由写入发生并发冲突。"""


class InvalidIsolationMigrationTransitionError(Exception):
    """迁移计划状态转换不在冻结状态机内。"""
