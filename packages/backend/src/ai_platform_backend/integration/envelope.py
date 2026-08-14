"""在集成事件与跨进程任务信封之间执行版本化严格转换。"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from ai_platform_backend.integration.domain import IntegrationEvent


class InvalidTaskEnvelopeError(ValueError):
    """任务信封结构或签名无效，调用方不得继续恢复可信上下文。"""


@dataclass(frozen=True)
class SignedTaskEnvelope:
    """承载语言无关的任务载荷、签名、追踪信息和重放保护字段。"""

    task_name: str
    task_id: UUID
    issued_at: datetime
    event: IntegrationEvent
    signature: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "task_name": self.task_name,
            "task_id": str(self.task_id),
            "issued_at": self.issued_at.isoformat().replace("+00:00", "Z"),
            "event": event_to_dict(self.event),
            "schema_version": self.schema_version,
            "signature": self.signature,
        }


class HmacTaskEnvelopeSigner:
    """签名覆盖规范 JSON，Worker 只从验签成功的信封恢复主体和 Trace。"""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("内部任务签名密钥必须正好为 32 字节")
        self._key = key

    def issue(
        self,
        *,
        task_name: str,
        task_id: UUID,
        issued_at: datetime,
        event: IntegrationEvent,
    ) -> SignedTaskEnvelope:
        unsigned = {
            "task_name": task_name,
            "task_id": str(task_id),
            "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
            "event": event_to_dict(event),
            "schema_version": 1,
        }
        return SignedTaskEnvelope(
            task_name=task_name,
            task_id=task_id,
            issued_at=issued_at,
            event=event,
            signature=self._signature(unsigned),
        )

    def verify(self, document: object) -> SignedTaskEnvelope:
        if not isinstance(document, dict):
            raise InvalidTaskEnvelopeError("内部任务信封必须是对象")
        required = {"task_name", "task_id", "issued_at", "event", "schema_version", "signature"}
        if set(document) != required or document.get("schema_version") != 1:
            raise InvalidTaskEnvelopeError("内部任务信封字段无效")
        signature = document.get("signature")
        if not isinstance(signature, str):
            raise InvalidTaskEnvelopeError("内部任务签名缺失")
        unsigned = {key: value for key, value in document.items() if key != "signature"}
        if not hmac.compare_digest(signature, self._signature(unsigned)):
            raise InvalidTaskEnvelopeError("内部任务签名无效")
        try:
            task_name = document["task_name"]
            if not isinstance(task_name, str) or not task_name:
                raise ValueError
            task_id = UUID(str(document["task_id"]))
            issued_at = datetime.fromisoformat(str(document["issued_at"]).replace("Z", "+00:00"))
            event = event_from_dict(document["event"])
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidTaskEnvelopeError("内部任务信封值无效") from error
        return SignedTaskEnvelope(
            task_name=task_name,
            task_id=task_id,
            issued_at=issued_at,
            event=event,
            signature=signature,
        )

    def _signature(self, document: dict[str, object]) -> str:
        canonical = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hmac.new(self._key, canonical, hashlib.sha256).hexdigest()


class SigningKeyFile:
    """内部任务签名使用独立密钥，避免向 Worker 暴露平台主加密密钥。"""

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def load(self) -> bytes:
        if self._path.is_symlink():
            raise PermissionError("内部任务签名密钥不能是符号链接")
        if not self._path.is_file():
            raise FileNotFoundError("内部任务签名密钥文件不存在")
        key = self._path.read_bytes()
        if len(key) != 32:
            raise ValueError("内部任务签名密钥必须正好为 32 字节")
        if self._path.stat().st_mode & 0o077:
            raise PermissionError("内部任务签名密钥权限必须限制为当前用户可读")
        return key


def event_to_dict(event: IntegrationEvent) -> dict[str, object]:
    """处理事件转换为字典，并保持调用方可依赖的稳定返回语义。"""

    return {
        "event_id": str(event.event_id),
        "event_type": event.event_type,
        "schema_version": event.schema_version,
        "workspace_id": str(event.workspace_id),
        "aggregate_id": str(event.aggregate_id),
        "aggregate_version": event.aggregate_version,
        "occurred_at": event.occurred_at.isoformat().replace("+00:00", "Z"),
        "trace_id": event.trace_id,
        "traceparent": event.traceparent,
        "actor_id": str(event.actor_id) if event.actor_id is not None else None,
        "user_id": str(event.user_id) if event.user_id is not None else None,
        "request_id": str(event.request_id) if event.request_id is not None else None,
        "payload": event.payload,
    }


def event_from_dict(value: object) -> IntegrationEvent:
    """处理事件从字典，并保持调用方可依赖的稳定返回语义。"""

    # 1. 先验证固定字段集合和 Schema 版本，未知字段不能静默进入跨进程契约。
    if not isinstance(value, dict):
        raise ValueError("集成事件必须是对象")
    required = {
        "event_id",
        "event_type",
        "schema_version",
        "workspace_id",
        "aggregate_id",
        "aggregate_version",
        "occurred_at",
        "trace_id",
        "traceparent",
        "actor_id",
        "user_id",
        "request_id",
        "payload",
    }
    if set(value) != required or value.get("schema_version") != 1:
        raise ValueError("集成事件字段无效")
    # 2. 载荷键通过检查后再恢复 UUID、时间和 Trace，任何类型错误统一拒绝整个事件。
    payload = value["payload"]
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise ValueError("集成事件载荷必须是字符串键对象")
    return IntegrationEvent(
        event_id=UUID(str(value["event_id"])),
        event_type=_required_string(value["event_type"]),
        schema_version=1,
        workspace_id=UUID(str(value["workspace_id"])),
        aggregate_id=UUID(str(value["aggregate_id"])),
        aggregate_version=_positive_integer(value["aggregate_version"]),
        occurred_at=datetime.fromisoformat(str(value["occurred_at"]).replace("Z", "+00:00")),
        trace_id=_required_string(value["trace_id"]),
        traceparent=_required_string(value["traceparent"]),
        actor_id=_optional_uuid(value["actor_id"]),
        user_id=_optional_uuid(value["user_id"]),
        request_id=_optional_uuid(value["request_id"]),
        payload=dict(payload),
    )


def _required_string(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("字段必须是非空字符串")
    return value


def _positive_integer(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("字段必须是正整数")
    return value


def _optional_uuid(value: Any) -> UUID | None:
    return None if value is None else UUID(str(value))
