from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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


class DocumentSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: Literal["manual", "upload", "web", "data_source"]
    source_name: str = Field(min_length=1, max_length=255)
    original_object_key: str | None = Field(default=None, max_length=1024)
    source_path: str | None = Field(default=None, max_length=2048)
    source_url: str | None = Field(default=None, max_length=2048)
    external_source_id: str | None = Field(default=None, max_length=512)
    captured_at: datetime | None = None


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
