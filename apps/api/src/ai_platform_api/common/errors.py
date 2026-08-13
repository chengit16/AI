from typing import ClassVar


class PlatformError(Exception):
    """平台可预期错误的共同边界，外部文案和状态码仍由错误目录决定。"""

    error_code: ClassVar[str] = "INTERNAL_ERROR"
