"""定义工作空间生命周期 HTTP 请求与响应。"""

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ai_platform_api.modules.lifecycle.application.compliance import (
    LegalHold,
    LegalHoldRelease,
    LifecycleComplianceProof,
    RegulatoryPolicyVersion,
)
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


class RegulatoryPolicyResponse(BaseModel):
    """返回受信配置源发布的低敏策略身份与外部状态。"""

    model_config = ConfigDict(extra="forbid")

    regulatory_policy_id: UUID
    policy_version: int
    jurisdiction_status: str
    jurisdiction_codes: tuple[str, ...]
    retention_period_days: dict[str, int]
    external_review_status: str
    external_review_digest: str | None
    policy_digest: str
    created_at: datetime

    @classmethod
    def from_domain(cls, value: RegulatoryPolicyVersion) -> Self:
        return cls(**{field: getattr(value, field) for field in cls.model_fields})


class LegalHoldBody(BaseModel):
    """只接收案件摘要和结构化原因，不接收案件或法规正文。"""

    model_config = ConfigDict(extra="forbid")

    case_reference_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")


class LegalHoldResponse(BaseModel):
    """返回工作空间级法律保留的只追加事实。"""

    model_config = ConfigDict(extra="forbid")

    legal_hold_id: UUID
    regulatory_policy_id: UUID
    scope_type: str
    scope_digest: str
    case_reference_digest: str
    reason_code: str
    activated_at: datetime

    @classmethod
    def from_domain(cls, value: LegalHold) -> Self:
        return cls(**{field: getattr(value, field) for field in cls.model_fields})


class LegalHoldReleaseBody(BaseModel):
    """要求解除证据摘要和结构化原因，解除正文不进入平台。"""

    model_config = ConfigDict(extra="forbid")

    release_evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")


class LegalHoldReleaseResponse(BaseModel):
    """返回独立的法律保留解除事实。"""

    model_config = ConfigDict(extra="forbid")

    release_id: UUID
    legal_hold_id: UUID
    reason_code: str
    release_evidence_digest: str
    released_at: datetime

    @classmethod
    def from_domain(cls, value: LegalHoldRelease) -> Self:
        return cls(**{field: getattr(value, field) for field in cls.model_fields})


class LifecycleComplianceProofResponse(BaseModel):
    """返回一次生命周期裁决的低敏、可复算证明。"""

    model_config = ConfigDict(extra="forbid")

    compliance_proof_id: UUID
    operation: str
    operation_id: UUID
    request_key_digest: str
    request_hash: str
    decision: str
    reason_codes: tuple[str, ...]
    regulatory_policy_id: UUID | None
    policy_digest: str | None
    external_review_status: str
    active_hold_count: int
    hold_set_digest: str
    proof_digest: str
    created_at: datetime

    @classmethod
    def from_domain(cls, value: LifecycleComplianceProof) -> Self:
        return cls(**{field: getattr(value, field) for field in cls.model_fields})
