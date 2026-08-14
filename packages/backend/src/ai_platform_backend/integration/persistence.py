"""定义审计、Outbox 和幂等消费 PostgreSQL 表。"""

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
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ai_platform_backend.database import SCHEMA_TOKEN

metadata = MetaData(schema=SCHEMA_TOKEN)

audit_records = Table(
    "audit_records",
    metadata,
    Column("audit_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("actor_id", UUID(as_uuid=True), nullable=False),
    Column("user_id", UUID(as_uuid=True), nullable=True),
    Column("action", String(255), nullable=False),
    Column("resource_type", String(128), nullable=False),
    Column("resource_id", UUID(as_uuid=True), nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("request_id", UUID(as_uuid=True), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("traceparent", String(55), nullable=False),
    Column("attributes", JSONB, nullable=False),
    CheckConstraint("outcome IN ('succeeded', 'denied', 'failed')", name="ck_audit_outcome"),
)
Index(
    "ix_audit_records_workspace_occurred",
    audit_records.c.workspace_id,
    audit_records.c.occurred_at,
    audit_records.c.audit_id,
)
Index(
    "ix_audit_records_resource",
    audit_records.c.workspace_id,
    audit_records.c.resource_type,
    audit_records.c.resource_id,
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
    Column("actor_id", UUID(as_uuid=True), nullable=True),
    Column("user_id", UUID(as_uuid=True), nullable=True),
    Column("request_id", UUID(as_uuid=True), nullable=True),
    Column("payload", JSON, nullable=False),
    Column("status", String(32), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("claimed_by", String(255), nullable=True),
    Column("claim_until", DateTime(timezone=True), nullable=True),
    Column("last_error_code", String(128), nullable=True),
    Column("published_at", DateTime(timezone=True), nullable=True),
    CheckConstraint("schema_version >= 1", name="ck_outbox_events_schema_version"),
    CheckConstraint("aggregate_version >= 1", name="ck_outbox_events_aggregate_version"),
    CheckConstraint("attempt_count >= 0", name="ck_outbox_events_attempt_count"),
    CheckConstraint(
        "status IN ('pending', 'publishing', 'published', 'dead_letter')",
        name="ck_outbox_events_status",
    ),
)
Index(
    "ix_outbox_events_dispatch",
    outbox_events.c.status,
    outbox_events.c.available_at,
    outbox_events.c.occurred_at,
)
Index("ix_outbox_events_claim", outbox_events.c.status, outbox_events.c.claim_until)

consumer_receipts = Table(
    "consumer_receipts",
    metadata,
    Column("consumer_name", String(255), primary_key=True),
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("task_id", UUID(as_uuid=True), nullable=False),
    Column("processed_at", DateTime(timezone=True), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("traceparent", String(55), nullable=False),
    UniqueConstraint("consumer_name", "event_id", name="uq_consumer_receipts_consumer_event"),
)

resource_projections = Table(
    "resource_projections",
    metadata,
    Column("aggregate_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("applied_event_id", UUID(as_uuid=True), nullable=False),
    Column("apply_count", Integer, nullable=False),
    Column("last_traceparent", Text, nullable=False),
    CheckConstraint("apply_count >= 1", name="ck_resource_projections_apply_count"),
)
