"""定义 Worker 清理任务所需的知识事实窄表元数据。

Worker 只能依赖这里声明的列，不导入 API 的 Repository 或完整 ORM 模型。
表结构的所有权仍属于 API Knowledge 模块；本文件只为跨进程 SQL 操作提供稳定字段。
"""

from sqlalchemy import Column, DateTime, MetaData, String, Table
from sqlalchemy.dialects.postgresql import UUID

from ai_platform_backend.database import SCHEMA_TOKEN

metadata = MetaData(schema=SCHEMA_TOKEN)

documents = Table(
    "documents",
    metadata,
    Column("document_id", UUID(as_uuid=True)),
    Column("workspace_id", UUID(as_uuid=True)),
    Column("status", String(32)),
    Column("deleted_at", DateTime(timezone=True)),
)

document_versions = Table(
    "document_versions",
    metadata,
    Column("document_version_id", UUID(as_uuid=True)),
    Column("workspace_id", UUID(as_uuid=True)),
    Column("document_id", UUID(as_uuid=True)),
)

document_sources = Table(
    "document_sources",
    metadata,
    Column("source_id", UUID(as_uuid=True)),
    Column("workspace_id", UUID(as_uuid=True)),
    Column("document_version_id", UUID(as_uuid=True)),
    Column("original_object_key", String(1024)),
)

document_publications = Table(
    "document_publications",
    metadata,
    Column("workspace_id", UUID(as_uuid=True)),
    Column("document_id", UUID(as_uuid=True)),
)

document_folder_bindings = Table(
    "document_folder_bindings",
    metadata,
    Column("workspace_id", UUID(as_uuid=True)),
    Column("document_id", UUID(as_uuid=True)),
    Column("folder_id", UUID(as_uuid=True)),
)

document_tag_bindings = Table(
    "document_tag_bindings",
    metadata,
    Column("workspace_id", UUID(as_uuid=True)),
    Column("document_id", UUID(as_uuid=True)),
)

document_favorites = Table(
    "document_favorites",
    metadata,
    Column("workspace_id", UUID(as_uuid=True)),
    Column("document_id", UUID(as_uuid=True)),
)
