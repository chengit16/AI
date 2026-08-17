"""定义 L3 隔离迁移资源、验证检查点、恢复事实和受信执行端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter

from ai_platform_api.modules.isolation.domain.models import (
    WorkspaceIsolationMigrationPlan,
    WorkspaceIsolationRoute,
)

L3CheckpointType = Literal[
    "source_snapshot",
    "database_copy",
    "object_copy",
    "derived_index_rebuild",
    "backup_restore",
    "deletion_propagation",
]
L3CheckpointStatus = Literal["passed", "failed"]
L3RecoveryStatus = Literal["passed", "failed"]
L3_CHECKPOINT_TYPES: tuple[L3CheckpointType, ...] = (
    "source_snapshot",
    "database_copy",
    "object_copy",
    "derived_index_rebuild",
    "backup_restore",
    "deletion_propagation",
)


@dataclass(frozen=True)
class L3MigrationCommand:
    """向受信执行器提供服务端生成的无凭证目标资源身份。"""

    workspace_id: UUID
    migration_plan_id: UUID
    database_route_key: str
    object_storage_route_key: str
    encryption_key_route_key: str
    search_namespace: str
    source_is_authoritative: bool
    target_writes_enabled: bool


@dataclass(frozen=True)
class L3CheckpointEvidence:
    """表示执行器对一个冻结迁移步骤给出的低敏摘要证据。"""

    checkpoint_type: L3CheckpointType
    status: L3CheckpointStatus
    source_digest: str
    target_digest: str
    evidence_digest: str
    item_count: int


@dataclass(frozen=True)
class L3ExecutionEvidence:
    """汇总独立数据库、Bucket、密钥与六类验证结果。"""

    workspace_id: UUID
    migration_plan_id: UUID
    database_identity_digest: str
    object_storage_identity_digest: str
    encryption_key_fingerprint: str
    source_key_fingerprint: str
    source_is_authoritative: bool
    target_writes_enabled: bool
    checkpoints: tuple[L3CheckpointEvidence, ...]


@dataclass(frozen=True)
class L3RecoveryEvidence:
    """证明失败迁移回收后源端仍是唯一权威写入位置。"""

    workspace_id: UUID
    migration_plan_id: UUID
    status: L3RecoveryStatus
    source_is_authoritative: bool
    target_writes_enabled: bool
    cleanup_digest: str
    evidence_digest: str


@dataclass(frozen=True)
class L3IsolationResourceProfile:
    """保存 L3 目标资源的无凭证路由键、指纹和配置摘要。"""

    resource_profile_id: UUID
    workspace_id: UUID
    migration_plan_id: UUID
    database_route_key: str
    object_storage_route_key: str
    encryption_key_route_key: str
    search_namespace: str
    database_identity_digest: str
    object_storage_identity_digest: str
    encryption_key_fingerprint: str
    configuration_digest: str
    created_by_actor_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class L3IsolationMigrationCheckpoint:
    """冻结迁移计划的一项验证结果，失败事实同样不可改写。"""

    checkpoint_id: UUID
    resource_profile_id: UUID
    workspace_id: UUID
    migration_plan_id: UUID
    checkpoint_type: L3CheckpointType
    position: int
    status: L3CheckpointStatus
    source_digest: str
    target_digest: str
    evidence_digest: str
    item_count: int
    checked_at: datetime


@dataclass(frozen=True)
class L3IsolationRecoveryRecord:
    """记录一次可重试恢复尝试，不以覆盖历史隐藏失败。"""

    recovery_record_id: UUID
    workspace_id: UUID
    migration_plan_id: UUID
    attempt_no: int
    status: L3RecoveryStatus
    source_is_authoritative: bool
    target_writes_enabled: bool
    cleanup_digest: str
    evidence_digest: str
    recovered_by_actor_id: UUID
    recovered_at: datetime


@dataclass(frozen=True)
class L3MigrationVerification:
    """返回迁移计划、资源档案和按冻结顺序排列的检查点。"""

    migration_plan: WorkspaceIsolationMigrationPlan
    resource_profile: L3IsolationResourceProfile | None
    checkpoints: tuple[L3IsolationMigrationCheckpoint, ...]


class L3MigrationExecutor(Protocol):
    """在数据库事务外执行可重放的物理复制与目标清理。"""

    def execute(self, command: L3MigrationCommand) -> L3ExecutionEvidence: ...

    def rollback(self, command: L3MigrationCommand) -> L3RecoveryEvidence: ...


class L3IsolationRepository(Protocol):
    """提供 L3 编排所需的计划、证据和路由持久化能力。"""

    def lock_workspace(self, workspace_id: UUID) -> None: ...

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

    def add_l3_resource_profile(self, profile: L3IsolationResourceProfile) -> None: ...

    def get_l3_resource_profile(
        self,
        workspace_id: UUID,
        migration_plan_id: UUID,
    ) -> L3IsolationResourceProfile | None: ...

    def add_l3_checkpoints(
        self,
        checkpoints: tuple[L3IsolationMigrationCheckpoint, ...],
    ) -> None: ...

    def list_l3_checkpoints(
        self,
        workspace_id: UUID,
        migration_plan_id: UUID,
    ) -> tuple[L3IsolationMigrationCheckpoint, ...]: ...

    def next_l3_recovery_attempt(
        self,
        workspace_id: UUID,
        migration_plan_id: UUID,
    ) -> int: ...

    def add_l3_recovery_record(self, record: L3IsolationRecoveryRecord) -> None: ...


class L3IsolationUnitOfWork(Protocol):
    """保证 L3 证据、计划、路由、审计与事件在短事务内提交。"""

    @property
    def isolation(self) -> L3IsolationRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> L3IsolationUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
