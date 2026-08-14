"""跨助手与模型网关复用的不可变运行配置标识。"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class RuntimeConfigSnapshot:
    """标识一次问答可冻结引用的运行配置版本和内容摘要。"""

    runtime_config_version_id: UUID
    content_hash: str
