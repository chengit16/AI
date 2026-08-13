class RetrievalScopeDeniedError(Exception):
    """策略范围禁止读取正文时直接终止检索，不能返回空白伪装成功。"""


class RetrievalConfigurationError(Exception):
    """Embedding 维度或 Adapter 输出不符合当前索引版本。"""


class CitationInvalidError(Exception):
    """引用不存在、已撤权、版本失效或原文不包含声明片段。"""
