from dataclasses import dataclass
from typing import ClassVar

from ai_platform_api.common.errors import PlatformError


class ReleaseManifestInvalidError(PlatformError):
    """发布清单缺失、损坏或字段无效时拒绝启动。"""

    error_code = "RELEASE_MANIFEST_INVALID"


@dataclass
class ReleaseCombinationIncompatibleError(PlatformError):
    """发布组件组合不在兼容矩阵内时携带稳定原因供启动日志诊断。"""

    error_code: ClassVar[str] = "RELEASE_COMBINATION_INCOMPATIBLE"
    reasons: tuple[str, ...]
