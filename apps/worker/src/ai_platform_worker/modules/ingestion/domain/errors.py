from typing import Literal

IngestionErrorCode = Literal[
    "INGESTION_EMPTY_FILE",
    "INGESTION_FILE_TOO_LARGE",
    "INGESTION_UNSUPPORTED_FORMAT",
    "INGESTION_PARSER_UNAVAILABLE",
    "INGESTION_PARSE_FAILED",
    "INGESTION_EMPTY_CONTENT",
    "INGESTION_PAGE_LIMIT_EXCEEDED",
    "INGESTION_SOURCE_CHANGED",
    "INGESTION_SOURCE_UNAVAILABLE",
    "INGESTION_ARTIFACT_UNAVAILABLE",
]

IngestionFailureStage = Literal["source", "parse", "ocr", "artifact", "worker"]


class IngestionError(Exception):
    """向任务状态提供稳定错误码，避免泄漏解析器原始异常。"""

    def __init__(
        self,
        code: IngestionErrorCode,
        message: str,
        *,
        retryable: bool,
        stage: IngestionFailureStage = "parse",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.stage = stage
