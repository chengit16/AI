"""检索模块 PostgreSQL 与 pgvector Adapter 的公开入口。"""

from ai_platform_api.modules.retrieval.infrastructure.sqlalchemy import SqlAlchemySearchIndex

__all__ = ["SqlAlchemySearchIndex"]
