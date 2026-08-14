"""定义索引构建、对象存储和 Embedding 失败的稳定错误码。"""

from typing import Literal

from ai_platform_backend.indexing.domain import IndexFailureStage


class IndexBuildError(Exception):
    """表示索引构建错误，由协议层映射为稳定错误码。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        stage: IndexFailureStage,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.stage = stage


class IndexStorageUnavailableError(Exception):
    """解析产物存储暂时无法读取。"""


class EmbeddingUnavailableError(Exception):
    """Embedding Adapter 暂时无法返回结果。"""


IndexProcessOutcome = Literal["succeeded", "retried", "failed", "lost"]
