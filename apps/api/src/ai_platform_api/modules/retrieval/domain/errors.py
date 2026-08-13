from ai_platform_api.common.errors import PlatformError


class RetrievalScopeDeniedError(PlatformError):
    """策略范围禁止读取正文时直接终止检索，不能返回空白伪装成功。"""

    error_code = "RETRIEVAL_SCOPE_DENIED"


class RetrievalConfigurationError(PlatformError):
    """Embedding 维度或 Adapter 输出不符合当前索引版本。"""

    error_code = "RETRIEVAL_CONFIGURATION_ERROR"


class CitationInvalidError(PlatformError):
    """引用不存在、已撤权、版本失效或原文不包含声明片段。"""

    error_code = "CITATION_INVALID"
