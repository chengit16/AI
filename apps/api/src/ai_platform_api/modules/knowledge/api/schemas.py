from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateKnowledgeBaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    default_visibility: Literal["private", "workspace", "departments"] = "private"
    department_ids: list[UUID] = Field(default_factory=list, max_length=100)
    default_security_level: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"] = "INTERNAL"


class KnowledgeBaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: UUID
    workspace_id: UUID
    name: str
    description: str | None
    default_visibility: Literal["private", "workspace", "departments"]
    department_ids: list[UUID]
    default_security_level: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    status: Literal["active", "deleted"]
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    version: int


class KnowledgeBaseSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: UUID
    name: str
    description: str | None
    default_visibility: Literal["private", "workspace", "departments"]
    default_security_level: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    updated_at: datetime


class KnowledgeBaseListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[KnowledgeBaseSummaryResponse]


class DocumentSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: Literal["manual", "upload", "web", "data_source"]
    source_name: str = Field(min_length=1, max_length=255)
    original_object_key: str | None = Field(
        default=None,
        max_length=1024,
        deprecated=True,
        description="V1 兼容占位, 对象键只能由受控 multipart 上传接口生成",
    )
    source_path: str | None = Field(default=None, max_length=2048)
    source_url: str | None = Field(default=None, max_length=2048)
    external_source_id: str | None = Field(default=None, max_length=512)
    captured_at: datetime | None = None

    @model_validator(mode="after")
    def reject_untrusted_upload_locator(self) -> "DocumentSourceRequest":
        # 上传对象键只能由 multipart 上传用例生成，JSON 入口不能伪造对象存储事实。
        if self.source_kind == "upload" or self.original_object_key is not None:
            raise ValueError("upload 来源必须使用受控文件上传接口")
        return self


class CreateDocumentRequest(DocumentSourceRequest):
    title: str = Field(min_length=1, max_length=255)
    visibility: Literal["private", "workspace", "departments"] | None = None
    department_ids: list[UUID] | None = Field(default=None, max_length=100)
    security_level: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"] | None = None
    permission_labels: list[str] = Field(default_factory=list, max_length=100)


class CreateDocumentVersionRequest(DocumentSourceRequest):
    pass


class MarkDocumentVersionReadyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DocumentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    workspace_id: UUID
    knowledge_base_id: UUID
    title: str
    visibility: Literal["private", "workspace", "departments"]
    department_ids: list[UUID]
    security_level: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    permission_labels: list[str]
    status: Literal["active", "deleted"]
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    version: int


class DocumentVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_version_id: UUID
    workspace_id: UUID
    document_id: UUID
    version_number: int
    status: Literal["draft", "ready", "published", "superseded"]
    content_hash: str | None
    created_by_account_id: UUID
    created_at: datetime
    published_at: datetime | None
    record_version: int


class KnowledgeDocumentSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    title: str
    visibility: Literal["private", "workspace", "departments"]
    security_level: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    updated_at: datetime
    latest_version: DocumentVersionResponse
    source_id: UUID
    source_kind: Literal["manual", "upload", "web", "data_source"]
    source_name: str
    current_document_version_id: UUID | None


class KnowledgeDocumentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[KnowledgeDocumentSummaryResponse]


class IngestionJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingestion_job_id: UUID
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    source_id: UUID
    source_name: str
    source_media_type: str
    status: Literal["queued", "running", "retry_wait", "succeeded", "failed"]
    attempt_count: int
    max_attempts: int
    available_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failure_stage: Literal["source", "parse", "ocr", "artifact", "worker"] | None
    error_code: str | None
    error_message: str | None
    parsed_content_hash: str | None
    parser_name: str | None
    ocr_used: bool | None
    page_count: int | None
    block_count: int | None
    can_retry_manually: bool
    manual_retry_count: int
    last_retried_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IngestionJobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[IngestionJobResponse]


class DocumentSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: UUID
    source_kind: Literal["manual", "upload", "web", "data_source"]
    source_name: str
    captured_at: datetime | None
    created_at: datetime


class DocumentCreatedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: DocumentResponse
    document_version: DocumentVersionResponse
    source: DocumentSourceResponse


class DocumentVersionCreatedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_version: DocumentVersionResponse
    source: DocumentSourceResponse


class UploadMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: str
    size_bytes: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scan_status: Literal["clean"] = "clean"


class DocumentUploadResponse(DocumentCreatedResponse):
    upload: UploadMetadataResponse


class DocumentVersionUploadResponse(DocumentVersionCreatedResponse):
    upload: UploadMetadataResponse
