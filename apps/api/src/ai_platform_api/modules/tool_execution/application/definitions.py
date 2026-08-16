"""严格解析工具治理文档，并复算 Schema 与定义内容摘要。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from jsonschema import Draft202012Validator, SchemaError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from ai_platform_api.modules.tool_execution.application.errors import (
    ToolDefinitionInvalidError,
)
from ai_platform_api.modules.tool_execution.domain.catalog import ToolDefinition

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
MAXIMUM_SCHEMA_BYTES = 32 * 1024
ToolKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"),
]
PermissionCode = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){2,}$"),
]


class ToolDefinitionDocument(BaseModel):
    """接收代码受控的注册文档，未知字段和隐式类型转换一律拒绝。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    tool_id: str
    tool_version: int = Field(ge=1)
    tool_key: ToolKey
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    access_mode: Literal["read", "write"]
    risk_level: Literal["low", "medium", "high", "critical"]
    adapter_kind: Literal["internal_read", "synthetic_internal_write"]
    input_schema_document: dict[str, object]
    output_schema_document: dict[str, object]
    permission_code: PermissionCode
    credential_requirement: Literal["none", "credential_ref"]
    timeout_seconds: int = Field(ge=1, le=120)
    retry_mode: Literal["none", "safe_read", "idempotent_write"]
    status: Literal["active", "retired"]
    synthetic: bool

    @field_validator("tool_id")
    @classmethod
    def validate_tool_id(cls, value: str) -> str:
        """拒绝非 UUID 身份，避免数据库与契约对同一工具产生不同解析。"""

        UUID(value)
        return value

    @model_validator(mode="after")
    def validate_adapter_boundary(self) -> Self:
        """把阶段 4 Adapter、读写和重试组合收敛为封闭允许列表。"""

        if self.adapter_kind == "internal_read":
            if (
                self.access_mode != "read"
                or self.synthetic
                or self.retry_mode == "idempotent_write"
            ):
                raise ValueError("内部只读 Adapter 的读写、合成或重试组合无效")
        elif (
            self.access_mode != "write"
            or not self.synthetic
            or self.risk_level not in {"high", "critical"}
            or self.retry_mode == "safe_read"
        ):
            raise ValueError("合成写 Adapter 的读写、风险、合成或重试组合无效")
        return self


def parse_tool_definition(
    document: object,
    *,
    created_at: datetime,
) -> ToolDefinition:
    """校验代码受控注册文档并生成不可变工具版本事实。

    时间必须带时区。输入和输出 Schema 都必须声明 Draft 2020-12、以对象为
    顶层并显式拒绝未知字段；这样后续模型或客户端不能通过未治理参数扩大调用。
    """

    # 1. 先封闭时间、字段、Adapter 组合与 Schema 边界，失败文档不能产生半有效摘要。
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ToolDefinitionInvalidError("工具注册时间必须包含时区")
    try:
        parsed = ToolDefinitionDocument.model_validate(document)
        input_hash = _validate_schema(parsed.input_schema_document, "input_schema_document")
        output_hash = _validate_schema(parsed.output_schema_document, "output_schema_document")
        semantic_document = _semantic_document(parsed, input_hash, output_hash)
        definition_hash = _digest(semantic_document)
    except (SchemaError, TypeError, ValueError, ValidationError) as error:
        raise ToolDefinitionInvalidError(str(error)) from error
    # 2. 所有语义校验完成后一次性构造不可变事实，计算字段不再接受调用方输入。
    return ToolDefinition(
        tool_id=UUID(parsed.tool_id),
        tool_version=parsed.tool_version,
        tool_key=parsed.tool_key,
        display_name=parsed.display_name,
        description=parsed.description,
        access_mode=parsed.access_mode,
        risk_level=parsed.risk_level,
        adapter_kind=parsed.adapter_kind,
        input_schema_document=parsed.input_schema_document,
        input_schema_hash=input_hash,
        output_schema_document=parsed.output_schema_document,
        output_schema_hash=output_hash,
        permission_code=parsed.permission_code,
        credential_requirement=parsed.credential_requirement,
        timeout_seconds=parsed.timeout_seconds,
        retry_mode=parsed.retry_mode,
        status=parsed.status,
        definition_hash=definition_hash,
        synthetic=parsed.synthetic,
        created_at=created_at,
    )


def verify_tool_definition(definition: ToolDefinition) -> None:
    """复算持久化事实，Schema 或任一治理字段漂移时立即失败关闭。"""

    reconstructed = parse_tool_definition(
        {
            "tool_id": str(definition.tool_id),
            "tool_version": definition.tool_version,
            "tool_key": definition.tool_key,
            "display_name": definition.display_name,
            "description": definition.description,
            "access_mode": definition.access_mode,
            "risk_level": definition.risk_level,
            "adapter_kind": definition.adapter_kind,
            "input_schema_document": definition.input_schema_document,
            "output_schema_document": definition.output_schema_document,
            "permission_code": definition.permission_code,
            "credential_requirement": definition.credential_requirement,
            "timeout_seconds": definition.timeout_seconds,
            "retry_mode": definition.retry_mode,
            "status": definition.status,
            "synthetic": definition.synthetic,
        },
        created_at=definition.created_at,
    )
    if (
        reconstructed.input_schema_hash != definition.input_schema_hash
        or reconstructed.output_schema_hash != definition.output_schema_hash
        or reconstructed.definition_hash != definition.definition_hash
    ):
        raise ToolDefinitionInvalidError("工具定义摘要与持久化内容不一致")


def _validate_schema(schema: dict[str, object], field_name: str) -> str:
    """验证有界对象 Schema，并返回确定性 SHA-256 摘要。"""

    payload = _canonical_json(schema)
    if len(payload) > MAXIMUM_SCHEMA_BYTES:
        raise ValueError(f"{field_name} 超过 {MAXIMUM_SCHEMA_BYTES} 字节")
    if (
        schema.get("$schema") != JSON_SCHEMA_DIALECT
        or schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
    ):
        raise ValueError(f"{field_name} 必须是拒绝未知字段的 Draft 2020-12 对象 Schema")
    Draft202012Validator.check_schema(schema)
    return hashlib.sha256(payload).hexdigest()


def _semantic_document(
    definition: ToolDefinitionDocument,
    input_schema_hash: str,
    output_schema_hash: str,
) -> dict[str, object]:
    """定义参与版本摘要的完整语义；注册时间和数据库位置不参与。"""

    document = definition.model_dump(mode="json")
    document["input_schema_hash"] = input_schema_hash
    document["output_schema_hash"] = output_schema_hash
    return document


def _digest(document: object) -> str:
    return hashlib.sha256(_canonical_json(document)).hexdigest()


def _canonical_json(document: object) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
