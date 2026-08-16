"""编排五个内部只读工具的目录、授权、Schema 和结果安全门禁。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from jsonschema import Draft202012Validator

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.tool_execution.application.definitions import (
    verify_tool_definition,
)
from ai_platform_api.modules.tool_execution.application.errors import (
    ToolAdapterNotAllowedError,
    ToolAdapterUnavailableError,
    ToolExecutionDeniedError,
    ToolResultRejectedError,
)
from ai_platform_api.modules.tool_execution.domain.adapters import (
    InternalReadAdapter,
    ToolAdapterRequest,
    ToolAdapterResult,
)
from ai_platform_api.modules.tool_execution.domain.catalog import ToolDefinition

MAXIMUM_RESULT_BYTES = 262_144
SENSITIVE_KEY_PARTS = frozenset(
    {"api_key", "authorization", "cookie", "credential", "password", "secret", "token"}
)
PROMPT_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(?:all\s+)?previous", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"忽略(?:之前|上面|先前)的(?:指令|提示)"),
    re.compile(r"系统提示(?:词|信息)?"),
)
RESOURCE_ARGUMENTS = {
    "document.read_authorized_range": "document_id",
    "workflow.get_status": "workflow_run_id",
    "approval.get_status": "approval_instance_id",
}


class ToolCatalogPort(Protocol):
    """只暴露 Adapter 网关需要的精确工具版本解析能力。"""

    def require_available_tool(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        tool_id: UUID,
        tool_version: int,
    ) -> ToolDefinition: ...


class ToolAdapterService:
    """在调用责任模块前后执行统一工具 Adapter 安全边界。"""

    def __init__(
        self,
        catalog: ToolCatalogPort,
        adapters: Mapping[str, InternalReadAdapter],
    ) -> None:
        self._catalog = catalog
        self._adapters = dict(adapters)

    def execute(
        self,
        context: RequestContext,
        *,
        tool_id: UUID,
        tool_version: int,
        arguments: Mapping[str, object],
    ) -> ToolAdapterResult:
        """解析精确工具版本并执行一次受控内部只读调用。"""

        definition = self._catalog.require_available_tool(
            context,
            workspace_id=context.workspace_id,
            tool_id=tool_id,
            tool_version=tool_version,
        )
        self._verify_adapter_boundary(definition)
        self._validate_input(definition, arguments)
        self._require_resource_scope(context, definition, arguments)
        adapter = self._adapters.get(definition.tool_key)
        if adapter is None or adapter.tool_key != definition.tool_key:
            raise ToolAdapterUnavailableError
        try:
            raw_payload = adapter.execute(ToolAdapterRequest(context, dict(arguments)))
        except (ToolExecutionDeniedError, ToolResultRejectedError):
            raise
        except Exception as error:
            raise ToolAdapterUnavailableError from error
        return self._secure_result(definition, raw_payload, context)

    @staticmethod
    def _verify_adapter_boundary(definition: ToolDefinition) -> None:
        """拒绝把工具目录中的写入、凭证或任意 Adapter 误接入只读网关。"""

        verify_tool_definition(definition)
        if (
            definition.adapter_kind != "internal_read"
            or definition.access_mode != "read"
            or definition.credential_requirement != "none"
            or definition.synthetic
        ):
            raise ToolAdapterNotAllowedError

    @staticmethod
    def _validate_input(definition: ToolDefinition, arguments: Mapping[str, object]) -> None:
        """按注册时冻结的 Schema 校验参数，未知字段和错误类型全部拒绝。"""

        if not isinstance(arguments, Mapping):
            raise ToolResultRejectedError
        validator = Draft202012Validator(definition.input_schema_document)
        if any(validator.iter_errors(dict(arguments))):
            raise ToolResultRejectedError

    @staticmethod
    def _require_resource_scope(
        context: RequestContext,
        definition: ToolDefinition,
        arguments: Mapping[str, object],
    ) -> None:
        """当 PDP 返回资源集合时，阻止 Adapter 把目标 ID 替换成其他资源。"""

        argument_name = RESOURCE_ARGUMENTS.get(definition.tool_key)
        if argument_name is None or context.authorized_workspace:
            return
        # 资源级授权为空表示当前主体没有任何可读资源，不能退化成全量放行。
        raw_resource_id = arguments.get(argument_name)
        if not isinstance(raw_resource_id, str):
            raise ToolExecutionDeniedError
        try:
            resource_id = UUID(raw_resource_id)
        except ValueError as error:
            raise ToolExecutionDeniedError from error
        if resource_id not in context.authorized_resource_ids:
            raise ToolExecutionDeniedError

    @classmethod
    def _secure_result(
        cls,
        definition: ToolDefinition,
        raw_payload: Mapping[str, object],
        context: RequestContext,
    ) -> ToolAdapterResult:
        """执行 Schema、大小、敏感字段和 Prompt Injection 四项结果检查。"""

        # 1. 结果必须是 JSON 对象并通过工具版本 Schema，责任模块不能扩大输出字段。
        if not isinstance(raw_payload, Mapping):
            raise ToolResultRejectedError
        payload = dict(raw_payload)
        validator = Draft202012Validator(definition.output_schema_document)
        if any(validator.iter_errors(payload)):
            raise ToolResultRejectedError
        # 2. 字段遮罩在离开责任模块前再次生效；正文被遮罩时不允许以 text/quote 变体绕过。
        if "content" in context.authorized_field_mask and _contains_key(
            payload, frozenset({"content", "text", "quote"})
        ):
            raise ToolResultRejectedError
        if _contains_sensitive_field(payload) or _contains_prompt_injection(payload):
            raise ToolResultRejectedError
        serialized = _canonical_json(payload)
        if len(serialized) > MAXIMUM_RESULT_BYTES:
            raise ToolResultRejectedError
        return ToolAdapterResult(
            tool_key=definition.tool_key,
            tool_version=definition.tool_version,
            payload=payload,
            output_schema_hash=definition.output_schema_hash,
            result_sha256=hashlib.sha256(serialized).hexdigest(),
            result_size_bytes=len(serialized),
            checks=("schema", "size", "sensitive_fields", "prompt_injection"),
        )


def _canonical_json(value: object) -> bytes:
    """生成稳定 JSON 摘要，并拒绝不可安全表达的 NaN 或自定义对象。"""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ToolResultRejectedError from error


def _contains_key(value: object, keys: frozenset[str]) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in keys or _contains_key(item, keys) for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_key(item, keys) for item in value)
    return False


def _contains_sensitive_field(value: object) -> bool:
    """扫描常见凭证字段和凭证格式，避免 Adapter 结果越界进入模型上下文。"""

    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                return True
            if _contains_sensitive_field(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_field(item) for item in value)
    if isinstance(value, str):
        return bool(re.search(r"(?:cred_[a-z0-9]{16,64}|sk-[A-Za-z0-9_-]{16,})", value))
    return False


def _contains_prompt_injection(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_prompt_injection(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_prompt_injection(item) for item in value)
    return isinstance(value, str) and any(
        pattern.search(value) is not None for pattern in PROMPT_INJECTION_PATTERNS
    )


__all__ = ["MAXIMUM_RESULT_BYTES", "ToolAdapterService", "ToolCatalogPort"]
