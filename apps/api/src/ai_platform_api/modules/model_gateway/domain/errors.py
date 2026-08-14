"""定义模型请求、路由、供应商失败和数据边界错误。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ai_platform_api.common.errors import PlatformError

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
    "data_boundary",
    "internal",
]


@dataclass
class ProviderInvocationError(Exception):
    """Adapter 已脱敏并分类的供应商错误，原始响应不能越过该边界。"""

    kind: ProviderFailureKind
    retryable: bool
    fallback_allowed: bool
    provider_request_id: str | None = None


class ModelRequestRejectedError(PlatformError):
    """请求超过 Prompt、输出或能力等确定性限制。"""

    error_code = "MODEL_REQUEST_REJECTED"


@dataclass
class ModelDataBoundaryDeniedError(PlatformError):
    """当前数据禁止外发且没有可用的私有模型路由。"""

    error_code = "MODEL_DATA_BOUNDARY_DENIED"
    attempts: tuple[ModelAttempt, ...] = ()


class ModelRouteUnavailableError(PlatformError):
    """没有满足状态、能力或数据边界要求的模型路由。"""

    error_code = "MODEL_ROUTE_UNAVAILABLE"


@dataclass
class ModelGatewayUnavailableError(PlatformError):
    """所有允许尝试的模型均失败，携带脱敏尝试记录供上层审计。"""

    error_code = "MODEL_GATEWAY_UNAVAILABLE"
    attempts: tuple[ModelAttempt, ...]
