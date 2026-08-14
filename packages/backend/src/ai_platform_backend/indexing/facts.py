from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from ai_platform_backend.database import SCHEMA_TOKEN

metadata = MetaData(schema=SCHEMA_TOKEN)

# Worker 只读取索引构建所需字段，业务事实的完整写模型仍由 Knowledge 模块拥有。
documents = Table(
    "documents",
    metadata,
    Column("workspace_id", UUID(as_uuid=True)),
    Column("knowledge_base_id", UUID(as_uuid=True)),
    Column("document_id", UUID(as_uuid=True)),
    Column("department_ids", ARRAY(UUID(as_uuid=True))),
    Column("visibility", String(32)),
    Column("security_level", String(32)),
    Column("permission_labels", ARRAY(String(80))),
    Column("status", String(32)),
)

document_versions = Table(
    "document_versions",
    metadata,
    Column("workspace_id", UUID(as_uuid=True)),
    Column("document_id", UUID(as_uuid=True)),
    Column("document_version_id", UUID(as_uuid=True)),
    Column("status", String(32)),
    Column("content_hash", String(64)),
    Column("record_version", Integer),
)

document_publications = Table(
    "document_publications",
    metadata,
    Column("workspace_id", UUID(as_uuid=True)),
    Column("document_id", UUID(as_uuid=True)),
    Column("current_document_version_id", UUID(as_uuid=True)),
)
