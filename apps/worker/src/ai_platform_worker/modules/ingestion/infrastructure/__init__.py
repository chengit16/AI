"""文档类型路由、PDF、Tika、对象存储和任务 Store Adapter 入口。"""

from ai_platform_worker.modules.ingestion.infrastructure.parsers import DefaultParserRouter
from ai_platform_worker.modules.ingestion.infrastructure.pdf import PdfDocumentParser
from ai_platform_worker.modules.ingestion.infrastructure.tika import TikaDocumentParser

__all__ = ["DefaultParserRouter", "PdfDocumentParser", "TikaDocumentParser"]
