"""导出企业知识治理 PostgreSQL 适配器。"""

from ai_platform_api.modules.enterprise_knowledge.infrastructure.sqlalchemy import (
    SqlAlchemyEnterpriseKnowledgeUnitOfWork,
)

__all__ = ["SqlAlchemyEnterpriseKnowledgeUnitOfWork"]
