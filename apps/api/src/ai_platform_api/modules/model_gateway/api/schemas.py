from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr

Capability = Literal["generation", "streaming", "tools", "structured_output"]
SecurityLevel = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]


class CreateModelProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_key: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    display_name: str = Field(min_length=1, max_length=120)
    adapter_kind: Literal["openai_compatible"] = "openai_compatible"
    base_url: str = Field(min_length=1, max_length=2048)
    probe_model_id: str = Field(min_length=1, max_length=255)
    location: Literal["external", "private"] = "external"
    declared_capabilities: list[Capability] = Field(min_length=1, max_length=4)
    api_key: SecretStr = Field(min_length=1, max_length=4096)


class RotateModelProviderCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr = Field(min_length=1, max_length=4096)


class ReviewModelProviderDataPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    max_security_level: SecurityLevel = "PUBLIC"
    retention_days: int | None = Field(default=None, ge=0, le=3650)
    training_usage_allowed: bool = False
    policy_url: HttpUrl | None = None
    policy_version: str | None = Field(default=None, min_length=1, max_length=128)


class ModelProviderConfigurationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: UUID
    provider_key: str
    display_name: str
    adapter_kind: Literal["openai_compatible"]
    base_url: str
    probe_model_id: str
    location: Literal["external", "private"]
    declared_capabilities: list[Capability]
    policy_review_status: Literal["pending", "approved", "rejected"]
    max_security_level: SecurityLevel
    retention_days: int | None
    training_usage_allowed: bool
    policy_url: str | None
    policy_version: str | None
    policy_reviewed_at: datetime | None
    probe_status: Literal["not_run", "passed", "failed"]
    probed_capabilities: list[Capability]
    last_probe_error_code: str | None
    last_probed_at: datetime | None
    status: Literal["draft", "active", "disabled"]
    created_at: datetime
    updated_at: datetime
    version: int


class ModelProviderConfigurationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ModelProviderConfigurationResponse]
