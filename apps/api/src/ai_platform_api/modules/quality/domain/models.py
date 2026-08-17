"""定义不保存业务正文的版本化质量样本与数据集端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

QualitySourceType = Literal["run_failure", "user_feedback", "human_correction"]
QualitySampleOperation = Literal["upsert", "deleted"]
QualitySecurityLevel = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]


@dataclass(frozen=True)
class QualitySampleCapture:
    """承载经过授权投影的瞬时正文；Service 计算摘要后不得持久化正文。"""

    source_type: QualitySourceType
    source_workspace_id: UUID
    source_id: UUID
    source_version: int
    resource_id: UUID
    signal_code: str
    reason_codes: tuple[str, ...]
    input_text: str | None
    output_text: str | None
    feedback_text: str | None
    correction_text: str | None
    source_security_level: QualitySecurityLevel


@dataclass(frozen=True)
class QualitySampleDeletion:
    """描述上游来源删除事件；删除版本不再接收任何正文。"""

    source_type: QualitySourceType
    source_workspace_id: UUID
    source_id: UUID
    source_version: int
    resource_id: UUID
    reason_code: str
    source_security_level: QualitySecurityLevel


@dataclass(frozen=True)
class QualitySampleVersion:
    """保存来源、内容摘要和完整授权投影，不包含可还原的业务正文。"""

    sample_version_id: UUID
    logical_sample_id: UUID
    workspace_id: UUID
    source_type: QualitySourceType
    source_id: UUID
    source_version: int
    resource_id: UUID
    operation: QualitySampleOperation
    signal_code: str
    reason_codes: tuple[str, ...]
    source_digest: str
    input_digest: str | None
    output_digest: str | None
    feedback_digest: str | None
    correction_digest: str | None
    supersedes_sample_version_id: UUID | None
    authorized_permission_code: str
    policy_decision_id: UUID
    policy_version: int
    workspace_scope: bool
    department_scope_ids: tuple[UUID, ...]
    account_scope_ids: tuple[UUID, ...]
    resource_scope_ids: tuple[UUID, ...]
    field_mask: tuple[str, ...]
    maximum_security_level: QualitySecurityLevel
    created_by_actor_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class QualityDatasetVersion:
    """表示工作空间质量样本当前集合的一次不可变快照。"""

    dataset_version_id: UUID
    workspace_id: UUID
    version_number: int
    previous_dataset_version_id: UUID | None
    trigger_sample_version_id: UUID
    sample_count: int
    dataset_digest: str
    created_by_actor_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class QualityDatasetSnapshot:
    """组合数据集版本、活动成员和触发变更，供公开 Service 查询。"""

    dataset: QualityDatasetVersion
    samples: tuple[QualitySampleVersion, ...]
    sample: QualitySampleVersion


class QualitySampleRepository(Protocol):
    """按工作空间维护不可变样本、数据集快照和成员集合。"""

    def lock_workspace(self, workspace_id: UUID) -> None: ...

    def get_by_source_version(
        self,
        workspace_id: UUID,
        source_type: QualitySourceType,
        source_id: UUID,
        source_version: int,
    ) -> QualitySampleVersion | None: ...

    def get_latest_source(
        self,
        workspace_id: UUID,
        source_type: QualitySourceType,
        source_id: UUID,
    ) -> QualitySampleVersion | None: ...

    def get_latest_dataset(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> QualityDatasetVersion | None: ...

    def get_snapshot(
        self,
        workspace_id: UUID,
        dataset_version_id: UUID,
    ) -> QualityDatasetSnapshot | None: ...

    def get_snapshot_by_trigger(
        self,
        workspace_id: UUID,
        sample_version_id: UUID,
    ) -> QualityDatasetSnapshot | None: ...

    def add_sample(self, sample: QualitySampleVersion) -> None: ...

    def add_dataset(
        self,
        dataset: QualityDatasetVersion,
        samples: tuple[QualitySampleVersion, ...],
    ) -> None: ...


class QualityUnitOfWork(Protocol):
    """保证样本版本、数据集和成员集合在同一事务提交。"""

    @property
    def quality(self) -> QualitySampleRepository: ...

    def __enter__(self) -> QualityUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
