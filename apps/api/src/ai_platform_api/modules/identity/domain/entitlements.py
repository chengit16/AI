"""定义套餐权益、功能开关、额度和幂等用量领域规则。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter

from ai_platform_api.modules.identity.domain.enterprise import WorkspaceMembership, WorkspaceRecord

UsageMetric = Literal[
    "storage_bytes",
    "knowledge_bases",
    "published_agents",
    "questions_monthly",
]


@dataclass(frozen=True)
class WorkspaceEntitlement:
    """描述工作空间套餐允许的功能与各类硬额度上限。"""

    workspace_id: UUID
    plan_code: str
    max_storage_bytes: int
    max_members: int
    max_knowledge_bases: int
    max_published_agents: int
    max_monthly_questions: int
    open_api_allowed: bool
    public_publish_allowed: bool
    created_at: datetime
    updated_at: datetime
    version: int

    def limit_for(self, metric: UsageMetric) -> int:
        return {
            "storage_bytes": self.max_storage_bytes,
            "knowledge_bases": self.max_knowledge_bases,
            "published_agents": self.max_published_agents,
            "questions_monthly": self.max_monthly_questions,
        }[metric]


@dataclass(frozen=True)
class WorkspaceFeatureSettings:
    """记录工作空间管理员在套餐允许范围内启用的功能。"""

    workspace_id: UUID
    open_api_enabled: bool
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class UsageCounter:
    """记录工作空间在指定计费周期内已消耗的某项额度。"""

    workspace_id: UUID
    metric: UsageMetric
    period_key: str
    used_value: int
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class UsageRecord:
    """以幂等键记录一次额度变动及变动后的确定结果。"""

    usage_record_id: UUID
    workspace_id: UUID
    metric: UsageMetric
    period_key: str
    idempotency_key: str
    delta_value: int
    resulting_value: int
    occurred_at: datetime


@dataclass(frozen=True)
class QuotaSnapshot:
    """提供某项额度当前用量、上限和剩余量的只读快照。"""

    metric: Literal[
        "members",
        "storage_bytes",
        "knowledge_bases",
        "published_agents",
        "questions_monthly",
    ]
    period_key: str
    used_value: int
    limit_value: int

    @property
    def remaining_value(self) -> int:
        return max(self.limit_value - self.used_value, 0)


@dataclass(frozen=True)
class EntitlementSnapshot:
    """聚合空间状态、套餐功能开关和全部额度的对外事实。"""

    workspace_id: UUID
    workspace_status: Literal["active", "suspended", "archived"]
    plan_code: str
    entitlement_version: int
    open_api_allowed: bool
    open_api_enabled: bool
    public_publish_allowed: bool
    quotas: tuple[QuotaSnapshot, ...]


@dataclass(frozen=True)
class OpenApiEntitlement:
    """区分套餐是否允许 OpenAPI 与空间是否实际启用。"""

    allowed: bool
    enabled: bool

    @property
    def active(self) -> bool:
        return self.allowed and self.enabled


class EntitlementAccessReader(Protocol):
    """向其他业务模块暴露最小化的 OpenAPI 权益查询端口。"""

    def get_open_api_entitlement(self, workspace_id: UUID) -> OpenApiEntitlement | None: ...


class EntitlementWriteConflictError(Exception):
    """权益、功能开关或用量幂等记录发生并发冲突。"""


class UsageRepository(Protocol):
    """供业务模块在自身事务内原子维护套餐用量的最小公开接口。"""

    def get_workspace(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> WorkspaceRecord | None: ...

    def get_entitlement(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> WorkspaceEntitlement | None: ...

    def get_usage_record(self, workspace_id: UUID, idempotency_key: str) -> UsageRecord | None: ...

    def get_usage_counter(
        self,
        workspace_id: UUID,
        metric: UsageMetric,
        period_key: str,
        *,
        for_update: bool = False,
    ) -> UsageCounter | None: ...

    def save_usage_counter(
        self, counter: UsageCounter, *, expected_version: int | None
    ) -> None: ...

    def add_usage_record(self, record: UsageRecord) -> None: ...


class EntitlementRepository(UsageRepository, Protocol):
    """在空间隔离范围内管理权益、功能设置和用量版本。"""

    def get_membership(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceMembership | None: ...

    def get_feature_settings(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> WorkspaceFeatureSettings | None: ...

    def get_entitlement_version(self, workspace_id: UUID) -> int | None: ...

    def active_member_count(self, workspace_id: UUID) -> int: ...

    def list_usage_counters(self, workspace_id: UUID) -> tuple[UsageCounter, ...]: ...

    def set_open_api_enabled(
        self,
        settings: WorkspaceFeatureSettings,
        *,
        expected_version: int,
    ) -> None: ...

    def bump_entitlement_version(self, workspace_id: UUID) -> int: ...


class EntitlementUnitOfWork(Protocol):
    """保证权益变更、审计和 Outbox 事件原子提交。"""

    @property
    def entitlements(self) -> EntitlementRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> EntitlementUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


def default_entitlement(
    *,
    workspace_id: UUID,
    workspace_type: Literal["personal", "enterprise"],
    occurred_at: datetime,
) -> tuple[WorkspaceEntitlement, WorkspaceFeatureSettings]:
    """本地套餐只固定功能验收额度，不代表商业定价或物理容量承诺。"""

    if workspace_type == "personal":
        entitlement = WorkspaceEntitlement(
            workspace_id,
            "personal_local",
            5 * 1024**3,
            1,
            5,
            3,
            2_000,
            False,
            False,
            occurred_at,
            occurred_at,
            1,
        )
    else:
        entitlement = WorkspaceEntitlement(
            workspace_id,
            "enterprise_simulated",
            100 * 1024**3,
            100,
            50,
            20,
            20_000,
            True,
            False,
            occurred_at,
            occurred_at,
            1,
        )
    return entitlement, WorkspaceFeatureSettings(workspace_id, False, occurred_at, 1)
