"""加载可观测字段白名单，并在任何导出器之前拒绝敏感属性。"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ObservationChannel = Literal["log", "span", "metric", "alert"]
type ObservationScalar = str | int | float | bool

_STABLE_NAME_FIELDS = {
    "alert_name",
    "component",
    "degraded_reason",
    "dependency",
    "environment",
    "error_code",
    "event_name",
    "notification_mode",
    "operation",
    "outcome",
    "queue",
    "reason_code",
    "service",
    "severity",
    "status",
    "task_name",
}
_STABLE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")


class UnsafeObservabilityFieldError(ValueError):
    """表示字段未登记、疑似敏感或值不适合作为可观测属性。"""


@dataclass(frozen=True)
class ObservabilityFieldRegistry:
    """保存四类导出通道的字段白名单和统一敏感值规则。"""

    registry_version: int
    allowed_fields: dict[ObservationChannel, frozenset[str]]
    forbidden_field_fragments: tuple[str, ...]
    forbidden_value_markers: tuple[str, ...]
    maximum_string_length: int

    @classmethod
    def load(cls, path: str | Path) -> ObservabilityFieldRegistry:
        """从版本化契约加载注册表；结构错误必须在服务启动时暴露。"""

        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("可观测字段注册表版本无效")
        channels = document.get("channels")
        if not isinstance(channels, dict) or set(channels) != {"log", "span", "metric", "alert"}:
            raise ValueError("可观测字段注册表通道无效")
        allowed: dict[ObservationChannel, frozenset[str]] = {}
        for channel in ("log", "span", "metric", "alert"):
            channel_document = channels.get(channel)
            if not isinstance(channel_document, dict):
                raise ValueError(f"可观测字段通道无效: {channel}")
            values = channel_document.get("allowed_fields")
            if (
                not isinstance(values, list)
                or not values
                or not all(isinstance(value, str) and value for value in values)
            ):
                raise ValueError(f"可观测字段白名单无效: {channel}")
            allowed[channel] = frozenset(values)
        return cls(
            registry_version=_positive_integer(document.get("registry_version")),
            allowed_fields=allowed,
            forbidden_field_fragments=_string_tuple(document.get("forbidden_field_fragments")),
            forbidden_value_markers=_string_tuple(document.get("forbidden_value_markers")),
            maximum_string_length=_positive_integer(document.get("maximum_string_length")),
        )

    def validate(
        self,
        channel: ObservationChannel,
        attributes: Mapping[str, ObservationScalar],
    ) -> dict[str, ObservationScalar]:
        """验证字段名、类型和长度；未知字段不允许静默降级为自由文本。"""

        allowed = self.allowed_fields[channel]
        safe: dict[str, ObservationScalar] = {}
        for name, value in attributes.items():
            normalized_name = name.casefold()
            if any(fragment in normalized_name for fragment in self.forbidden_field_fragments):
                raise UnsafeObservabilityFieldError(f"可观测字段禁止承载敏感语义: {name}")
            if name not in allowed:
                raise UnsafeObservabilityFieldError(f"可观测字段未登记: {channel}.{name}")
            if not isinstance(value, str | int | float | bool):
                raise UnsafeObservabilityFieldError(f"可观测字段类型不受支持: {channel}.{name}")
            if isinstance(value, str):
                if len(value) > self.maximum_string_length:
                    raise UnsafeObservabilityFieldError(f"可观测字段字符串过长: {channel}.{name}")
                if any(marker in value for marker in self.forbidden_value_markers):
                    raise UnsafeObservabilityFieldError(f"可观测字段包含敏感标记: {channel}.{name}")
                if name in _STABLE_NAME_FIELDS and not _STABLE_NAME_PATTERN.fullmatch(value):
                    raise UnsafeObservabilityFieldError(
                        f"可观测字段值不是稳定标识: {channel}.{name}"
                    )
            if isinstance(value, float) and not math.isfinite(value):
                raise UnsafeObservabilityFieldError(f"可观测数值必须为有限值: {channel}.{name}")
            safe[name] = value
        return safe


def _positive_integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("可观测字段注册表整数必须为正数")
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise ValueError("可观测字段注册表字符串列表无效")
    return tuple(value)
