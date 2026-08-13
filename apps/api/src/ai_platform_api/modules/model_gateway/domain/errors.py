from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ai_platform_api.modules.model_gateway.domain.models import ModelAttempt

ProviderFailureKind = Literal[
    "timeout",
    "rate_limited",
    "unavailable",
    "authentication",
    "capability_unsupported",
    "content_policy",
    "invalid_request",
    "invalid_response",
    "internal",
]


@dataclass
class ProviderInvocationError(Exception):
    """Adapter 已脱敏并分类的供应商错误，原始响应不能越过该边界。"""

    kind: ProviderFailureKind
    retryable: bool
    fallback_allowed: bool
    provider_request_id: str | None = None


class ModelRequestRejectedError(Exception):
    """请求超过 Prompt、输出或能力等确定性限制。"""


class ModelDataBoundaryDeniedError(Exception):
    """当前数据禁止外发且没有可用的私有模型路由。"""


class ModelRouteUnavailableError(Exception):
    """没有满足状态、能力或数据边界要求的模型路由。"""


@dataclass
class ModelGatewayUnavailableError(Exception):
    """所有允许尝试的模型均失败，携带脱敏尝试记录供上层审计。"""

    attempts: tuple[ModelAttempt, ...]
