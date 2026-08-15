"""定义工作空间生命周期 HTTP 请求与响应。"""

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ai_platform_api.modules.lifecycle.application.service import (
    DeletionCertificate,
    LifecycleExport,
    LifecyclePurge,
    RetentionRun,
)


class LifecycleExportResponse(BaseModel):
    """返回导出包位置和双层完整性摘要。"""

    model_config = ConfigDict(extra="forbid")

    export_id: UUID
    status: str
    object_key: str | None
    bundle_size_bytes: int | None
    bundle_sha256: str | None
    object_manifest_sha256: str | None
    table_count: int | None
    object_count: int | None
    created_at: datetime
    completed_at: datetime | None
    error_code: str | None

    @classmethod
    def from_domain(cls, value: LifecycleExport) -> Self:
        return cls(**{field: getattr(value, field) for field in cls.model_fields})


class LifecyclePurgeBody(BaseModel):
    """要求用户复述空间名称并提供结构化删除原因。"""

    model_config = ConfigDict(extra="forbid")

    confirmed_workspace_name: str = Field(min_length=1, max_length=120)
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")


class DeletionCertificateResponse(BaseModel):
    """返回不含原业务正文的删除证明。"""

    model_config = ConfigDict(extra="forbid")

    certificate_id: UUID
    registry_version: int
    deleted_table_counts: dict[str, int]
    deleted_object_count: int
    deleted_cache_key_count: int
    result_sha256: str
    completed_at: datetime

    @classmethod
    def from_domain(cls, value: DeletionCertificate) -> Self:
        return cls(**{field: getattr(value, field) for field in cls.model_fields})


class LifecyclePurgeResponse(BaseModel):
    """返回跨存储完成状态及其不可变证明。"""

    model_config = ConfigDict(extra="forbid")

    purge_request_id: UUID
    status: str
    database_cleared: bool
    objects_cleared: bool
    cache_cleared: bool
    completed_at: datetime | None
    certificate: DeletionCertificateResponse

    @classmethod
    def from_domain(cls, purge: LifecyclePurge, certificate: DeletionCertificate) -> Self:
        return cls(
            purge_request_id=purge.purge_request_id,
            status=purge.status,
            database_cleared=purge.database_cleared,
            objects_cleared=purge.objects_cleared,
            cache_cleared=purge.cache_cleared,
            completed_at=purge.completed_at,
            certificate=DeletionCertificateResponse.from_domain(certificate),
        )


class RetentionRunResponse(BaseModel):
    """返回本次保留期边界、删除计数和摘要。"""

    model_config = ConfigDict(extra="forbid")

    retention_run_id: UUID
    status: str
    cutoffs: dict[str, str]
    deleted_table_counts: dict[str, int]
    result_sha256: str | None
    created_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_domain(cls, value: RetentionRun) -> Self:
        return cls(**{field: getattr(value, field) for field in cls.model_fields})
