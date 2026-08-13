from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from ai_platform_api.persistence.database import SCHEMA_TOKEN

metadata = MetaData(schema=SCHEMA_TOKEN)

workspace_resources = Table(
    "workspace_resources",
    metadata,
    Column("resource_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("title", String(255), nullable=False),
    Column("sensitive_value", String(1024), nullable=True),
    Column("version", Integer, nullable=False),
    CheckConstraint("version >= 1", name="ck_workspace_resources_version"),
)
Index(
    "ix_workspace_resources_workspace_resource",
    workspace_resources.c.workspace_id,
    workspace_resources.c.resource_id,
)

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("event_type", String(255), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("aggregate_id", UUID(as_uuid=True), nullable=False),
    Column("aggregate_version", Integer, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("traceparent", String(55), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=True),
    CheckConstraint("schema_version >= 1", name="ck_outbox_events_schema_version"),
    CheckConstraint("aggregate_version >= 1", name="ck_outbox_events_aggregate_version"),
)
Index("ix_outbox_events_unpublished", outbox_events.c.published_at, outbox_events.c.occurred_at)

consumer_receipts = Table(
    "consumer_receipts",
    metadata,
    Column("consumer_name", String(255), primary_key=True),
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("processed_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("consumer_name", "event_id", name="uq_consumer_receipts_consumer_event"),
)

resource_projections = Table(
    "resource_projections",
    metadata,
    Column("aggregate_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("applied_event_id", UUID(as_uuid=True), nullable=False),
    Column("apply_count", Integer, nullable=False),
    CheckConstraint("apply_count >= 1", name="ck_resource_projections_apply_count"),
)
