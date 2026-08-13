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
    workspace_id: UUID
    open_api_enabled: bool
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class UsageCounter:
    workspace_id: UUID
    metric: UsageMetric
    period_key: str
    used_value: int
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class UsageRecord:
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
    allowed: bool
    enabled: bool

    @property
    def active(self) -> bool:
        return self.allowed and self.enabled


class EntitlementAccessReader(Protocol):
    def get_open_api_entitlement(self, workspace_id: UUID) -> OpenApiEntitlement | None: ...


class EntitlementWriteConflictError(Exception):
    """权益、功能开关或用量幂等记录发生并发冲突。"""


class EntitlementRepository(Protocol):
    def get_workspace(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> WorkspaceRecord | None: ...

    def get_membership(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceMembership | None: ...

    def get_entitlement(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> WorkspaceEntitlement | None: ...

    def get_feature_settings(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> WorkspaceFeatureSettings | None: ...

    def get_entitlement_version(self, workspace_id: UUID) -> int | None: ...

    def active_member_count(self, workspace_id: UUID) -> int: ...

    def list_usage_counters(self, workspace_id: UUID) -> tuple[UsageCounter, ...]: ...

    def get_usage_counter(
        self,
        workspace_id: UUID,
        metric: UsageMetric,
        period_key: str,
        *,
        for_update: bool = False,
    ) -> UsageCounter | None: ...

    def get_usage_record(self, workspace_id: UUID, idempotency_key: str) -> UsageRecord | None: ...

    def set_open_api_enabled(
        self,
        settings: WorkspaceFeatureSettings,
        *,
        expected_version: int,
    ) -> None: ...

    def save_usage_counter(
        self, counter: UsageCounter, *, expected_version: int | None
    ) -> None: ...

    def add_usage_record(self, record: UsageRecord) -> None: ...

    def bump_entitlement_version(self, workspace_id: UUID) -> int: ...


class EntitlementUnitOfWork(Protocol):
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
