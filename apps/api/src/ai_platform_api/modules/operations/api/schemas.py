"""定义受控运营工作台稳定 HTTP Schema。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ai_platform_api.modules.operations.contracts import (
    IndexMaintenanceRequest,
    IndexMaintenanceRun,
    LifecycleOperation,
    OperationsIngestionJob,
    OperationsOverview,
)

ControlTowerStatus = Literal["passed", "blocked", "not_run", "not_configured", "failed"]
ControlTowerSectionKey = Literal["quality", "cost", "isolation", "compliance", "private_instance"]


class _ControlTowerFactView(Protocol):
    """定义 API 只读投影所需的字段，避免 Schema 反向依赖领域实现。"""

    @property
    def label(self) -> str: ...

    @property
    def value(self) -> str: ...

    @property
    def status(self) -> ControlTowerStatus: ...


class _ControlTowerSectionView(Protocol):
    """描述应用层传递给 API 的分区投影字段。"""

    @property
    def key(self) -> ControlTowerSectionKey: ...

    @property
    def title(self) -> str: ...

    @property
    def status(self) -> ControlTowerStatus: ...

    @property
    def summary(self) -> str: ...

    @property
    def reason_codes(self) -> Sequence[str]: ...

    @property
    def facts(self) -> Sequence[_ControlTowerFactView]: ...


class _DangerousOperationNoticeView(Protocol):
    """描述危险操作提示的低敏投影字段。"""

    @property
    def operation(self) -> str: ...

    @property
    def permission_code(self) -> str: ...

    @property
    def confirmation_required(self) -> bool: ...

    @property
    def backend_reauthorization(self) -> bool: ...


class _ControlTowerSourceContractView(Protocol):
    """描述来源契约版本状态，禁止 API 读取其领域实现。"""

    @property
    def contract_id(self) -> str: ...

    @property
    def version(self) -> int: ...

    @property
    def status(self) -> Literal["frozen", "not_configured", "not_run"]: ...


class _OperationsControlTowerSnapshotView(Protocol):
    """描述控制台快照的应用层只读投影。"""

    @property
    def workspace_id(self) -> UUID: ...

    @property
    def snapshot_version(self) -> int: ...

    @property
    def generated_at(self) -> datetime: ...

    @property
    def sections(self) -> Sequence[_ControlTowerSectionView]: ...

    @property
    def dangerous_operations(self) -> Sequence[_DangerousOperationNoticeView]: ...

    @property
    def source_contracts(self) -> Sequence[_ControlTowerSourceContractView]: ...


class StatusCountResponse(BaseModel):
    """返回一个固定状态和对应数量。"""

    status: str
    count: int


class OperationsOverviewResponse(BaseModel):
    """返回不含标识、正文和凭据的运营指标快照。"""

    checked_at: datetime
    ingestion: list[StatusCountResponse]
    index_versions: list[StatusCountResponse]
    index_requests: list[StatusCountResponse]
    outbox: list[StatusCountResponse]
    lifecycle_active_count: int

    @classmethod
    def from_domain(cls, overview: OperationsOverview) -> Self:
        return cls(
            checked_at=overview.checked_at,
            ingestion=[StatusCountResponse(**item.__dict__) for item in overview.ingestion],
            index_versions=[
                StatusCountResponse(**item.__dict__) for item in overview.index_versions
            ],
            index_requests=[
                StatusCountResponse(**item.__dict__) for item in overview.index_requests
            ],
            outbox=[StatusCountResponse(**item.__dict__) for item in overview.outbox],
            lifecycle_active_count=overview.lifecycle_active_count,
        )


class OperationsIngestionJobResponse(BaseModel):
    """返回入库任务的安全运营摘要。"""

    ingestion_job_id: UUID
    knowledge_base_id: UUID
    document_id: UUID
    source_name: str
    processing_lane: Literal["parsing", "ocr"]
    status: str
    attempt_count: int
    max_attempts: int
    manual_retry_count: int
    error_code: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, job: OperationsIngestionJob) -> Self:
        return cls(**job.__dict__)


class OperationsIngestionJobListResponse(BaseModel):
    """返回当前工作空间最近的入库任务。"""

    items: list[OperationsIngestionJobResponse]


class IndexMaintenanceCommandBody(BaseModel):
    """接收索引维护命令的结构化原因和显式确认文本。"""

    model_config = ConfigDict(extra="forbid")

    reason_code: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]{2,63}$",
    )
    confirmation: str = Field(min_length=8, max_length=64)


class IndexMaintenanceRequestResponse(BaseModel):
    """返回人工维护请求状态，不暴露内部租约和请求摘要。"""

    maintenance_request_id: UUID
    workspace_id: UUID
    command: Literal["inspection", "full_rebuild", "cleanup"]
    reason_code: str
    status: Literal["pending", "running", "retry_wait", "completed", "dead_letter"]
    attempt_count: int
    last_error_code: str | None
    requested_by_actor_id: UUID
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_domain(cls, request: IndexMaintenanceRequest) -> Self:
        return cls(
            maintenance_request_id=request.maintenance_request_id,
            workspace_id=request.workspace_id,
            command=request.command,
            reason_code=request.reason_code,
            status=request.status,
            attempt_count=request.attempt_count,
            last_error_code=request.last_error_code,
            requested_by_actor_id=request.requested_by_actor_id,
            created_at=request.created_at,
            updated_at=request.updated_at,
            completed_at=request.completed_at,
        )


class IndexMaintenanceRequestListResponse(BaseModel):
    """返回当前工作空间最近的人工维护请求。"""

    items: list[IndexMaintenanceRequestResponse]


class IndexMaintenanceRunResponse(BaseModel):
    """返回一次索引维护完成证据和计数。"""

    maintenance_run_id: UUID
    run_kind: Literal["inspection", "full_rebuild", "cleanup"]
    requested_by_actor_id: UUID | None
    started_at: datetime
    completed_at: datetime
    scanned_document_count: int
    inconsistency_count: int
    repaired_count: int
    rebuild_queued_count: int
    cleaned_chunk_count: int
    result_digest: str

    @classmethod
    def from_domain(cls, run: IndexMaintenanceRun) -> Self:
        return cls(**run.__dict__)


class IndexMaintenanceRunListResponse(BaseModel):
    """返回当前工作空间最近的索引维护运行。"""

    items: list[IndexMaintenanceRunResponse]


class LifecycleOperationResponse(BaseModel):
    """返回一种生命周期操作的统一历史摘要。"""

    operation_id: UUID
    operation_kind: Literal["export", "purge", "retention"]
    status: str
    created_at: datetime
    completed_at: datetime | None
    error_code: str | None
    result_count: int | None

    @classmethod
    def from_domain(cls, operation: LifecycleOperation) -> Self:
        return cls(**operation.__dict__)


class LifecycleOperationListResponse(BaseModel):
    """返回导出、清除和保留期执行的统一历史列表。"""

    items: list[LifecycleOperationResponse]


class ControlTowerFactResponse(BaseModel):
    """返回控制台中的单个低敏状态字段。"""

    label: str
    value: str
    status: ControlTowerStatus

    @classmethod
    def from_domain(cls, fact: _ControlTowerFactView) -> Self:
        return cls(label=fact.label, value=fact.value, status=fact.status)


class ControlTowerSectionResponse(BaseModel):
    """返回一个质量治理分区及其阻断原因。"""

    key: ControlTowerSectionKey
    title: str
    status: ControlTowerStatus
    summary: str
    reason_codes: list[str]
    facts: list[ControlTowerFactResponse]

    @classmethod
    def from_domain(cls, section: _ControlTowerSectionView) -> Self:
        return cls(
            key=section.key,
            title=section.title,
            status=section.status,
            summary=section.summary,
            reason_codes=list(section.reason_codes),
            facts=[ControlTowerFactResponse.from_domain(fact) for fact in section.facts],
        )


class DangerousOperationNoticeResponse(BaseModel):
    """返回危险动作的二次确认和后端重授权要求。"""

    operation: str
    permission_code: str
    confirmation_required: Literal[True]
    backend_reauthorization: Literal[True]

    @classmethod
    def from_domain(cls, notice: _DangerousOperationNoticeView) -> Self:
        return cls(
            operation=notice.operation,
            permission_code=notice.permission_code,
            confirmation_required=True,
            backend_reauthorization=True,
        )


class ControlTowerSourceContractResponse(BaseModel):
    """返回控制台读取的版本化来源契约状态。"""

    contract_id: str
    version: int
    status: Literal["frozen", "not_configured", "not_run"]


class OperationsControlTowerResponse(BaseModel):
    """统一质量、成本、隔离、法规与私有实例控制台快照。"""

    workspace_id: UUID
    snapshot_version: int
    generated_at: datetime
    sections: list[ControlTowerSectionResponse]
    dangerous_operations: list[DangerousOperationNoticeResponse]
    source_contracts: list[ControlTowerSourceContractResponse]

    @classmethod
    def from_domain(cls, snapshot: _OperationsControlTowerSnapshotView) -> Self:
        return cls(
            workspace_id=snapshot.workspace_id,
            snapshot_version=snapshot.snapshot_version,
            generated_at=snapshot.generated_at,
            sections=[ControlTowerSectionResponse.from_domain(item) for item in snapshot.sections],
            dangerous_operations=[
                DangerousOperationNoticeResponse.from_domain(item)
                for item in snapshot.dangerous_operations
            ],
            source_contracts=[
                ControlTowerSourceContractResponse(
                    contract_id=item.contract_id,
                    version=item.version,
                    status=item.status,
                )
                for item in snapshot.source_contracts
            ],
        )
