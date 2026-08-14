"""定义模型供应商和不可变运行配置接口 Schema。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr

Capability = Literal["generation", "streaming", "tools", "structured_output"]
SecurityLevel = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]


class CreateModelProviderRequest(BaseModel):
    """定义创建模型供应商操作的请求字段与协议校验边界。"""

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
    """定义轮换模型供应商凭据操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr = Field(min_length=1, max_length=4096)


class ReviewModelProviderDataPolicyRequest(BaseModel):
    """定义审核模型供应商数据策略操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    approved: bool
    max_security_level: SecurityLevel = "PUBLIC"
    retention_days: int | None = Field(default=None, ge=0, le=3650)
    training_usage_allowed: bool = False
    policy_url: HttpUrl | None = None
    policy_version: str | None = Field(default=None, min_length=1, max_length=128)


class ModelProviderConfigurationResponse(BaseModel):
    """定义模型供应商配置操作的稳定响应结构。"""

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
    """定义模型供应商配置列表操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    items: list[ModelProviderConfigurationResponse]


class RuntimeComponentVersionsSchema(BaseModel):
    """定义运行时组件版本集合结构的协议字段和序列化边界。"""

    model_config = ConfigDict(extra="forbid")

    chunking: str = Field(min_length=1, max_length=128)
    embedding: str = Field(min_length=1, max_length=128)
    index_schema: str = Field(min_length=1, max_length=128)
    reranker: str = Field(min_length=1, max_length=128)
    retrieval: str = Field(min_length=1, max_length=128)
    source_ranking: str = Field(min_length=1, max_length=128)
    safety: str = Field(min_length=1, max_length=128)
    data_source_interface: str = Field(min_length=1, max_length=128)
    relevance_grader_interface: str = Field(min_length=1, max_length=128)
    multimodal_router_interface: str = Field(min_length=1, max_length=128)


class GatewayPolicySchema(BaseModel):
    """定义网关策略结构的协议字段和序列化边界。"""

    model_config = ConfigDict(extra="forbid")

    attempt_timeout_ms: int = Field(ge=1, le=120_000)
    total_timeout_ms: int = Field(ge=1, le=300_000)
    max_attempts_per_route: int = Field(ge=1, le=5)
    max_prompt_characters: int = Field(ge=1, le=2_000_000)
    max_output_tokens: int = Field(ge=1, le=65_536)
    max_response_characters: int = Field(ge=1, le=4_000_000)
    circuit_failure_threshold: int = Field(ge=1, le=20)
    circuit_recovery_ms: int = Field(ge=1, le=3_600_000)
    rule_degradation_message: str | None = Field(default=None, min_length=1, max_length=1_000)
    max_estimated_cost_microunits: int = Field(ge=1, le=1_000_000_000_000)


class RuntimeRouteRequest(BaseModel):
    """定义运行时路由操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    provider_id: UUID
    priority: int = Field(ge=1, le=8)
    model_id: str = Field(min_length=1, max_length=255)
    capabilities: list[Capability] = Field(min_length=1, max_length=4)
    input_price_microunits_per_million_tokens: int = Field(ge=0, le=1_000_000_000_000)
    output_price_microunits_per_million_tokens: int = Field(ge=0, le=1_000_000_000_000)


class CreateAiRuntimeConfigRequest(BaseModel):
    """定义创建AI运行时配置操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=120)
    system_prompt_template: str = Field(min_length=1, max_length=16_000)
    components: RuntimeComponentVersionsSchema
    policy: GatewayPolicySchema
    routes: list[RuntimeRouteRequest] = Field(min_length=1, max_length=8)


class RuntimeRouteResponse(BaseModel):
    """定义运行时路由操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    route_id: UUID
    provider_id: UUID
    provider_configuration_version: int
    priority: int
    model_id: str
    location: Literal["external", "private"]
    capabilities: list[Capability]
    input_price_microunits_per_million_tokens: int
    output_price_microunits_per_million_tokens: int
    currency: Literal["CNY"]


class AiRuntimeConfigResponse(BaseModel):
    """定义AI运行时配置操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    runtime_config_version_id: UUID
    version_number: int
    display_name: str
    content_hash: str
    system_prompt_template: str
    system_prompt_hash: str
    components: RuntimeComponentVersionsSchema
    policy: GatewayPolicySchema
    routes: list[RuntimeRouteResponse]
    created_by_account_id: UUID
    created_at: datetime


class AiRuntimeConfigListResponse(BaseModel):
    """定义AI运行时配置列表操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    items: list[AiRuntimeConfigResponse]


class CurrentAiRuntimeConfigResponse(BaseModel):
    """定义当前AI运行时配置操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    item: AiRuntimeConfigResponse | None


class AiRuntimeConfigPublicationResponse(BaseModel):
    """定义AI运行时配置发布记录操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    runtime_config_version_id: UUID
    generation: int
    published_by_account_id: UUID
    published_at: datetime
