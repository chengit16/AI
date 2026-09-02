"""定义审计、Outbox 和幂等消费 PostgreSQL 表。"""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

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
    Column("permission_code", String(160), nullable=True),
    Column("policy_decision_id", UUID(as_uuid=True), nullable=True),
    Column("policy_version", Integer, nullable=True),
    Column("attributes", JSONB, nullable=False),
    CheckConstraint("outcome IN ('succeeded', 'denied', 'failed')", name="ck_audit_outcome"),
    CheckConstraint(
        "(permission_code IS NULL AND policy_decision_id IS NULL AND policy_version IS NULL) "
        "OR (permission_code IS NOT NULL AND policy_decision_id IS NOT NULL "
        "AND policy_version >= 1)",
        name="ck_audit_authorization",
    ),
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
    UniqueConstraint(
        "event_id",
        "workspace_id",
        name="uq_outbox_events_event_workspace",
    ),
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
    Column("delivery_count", Integer, nullable=False),
    Column("last_received_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("consumer_name", "event_id", name="uq_consumer_receipts_consumer_event"),
    CheckConstraint("delivery_count >= 1", name="ck_consumer_receipts_delivery_count"),
)

Index(
    "ix_consumer_receipts_event_received",
    consumer_receipts.c.event_id,
    consumer_receipts.c.last_received_at,
)

outbox_replay_requests = Table(
    "outbox_replay_requests",
    metadata,
    Column("replay_request_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("event_id", UUID(as_uuid=True), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("reason_code", String(64), nullable=False),
    Column("source_status", String(32), nullable=False),
    Column("source_attempt_count", Integer, nullable=False),
    Column("source_published_at", DateTime(timezone=True), nullable=True),
    Column("source_error_code", String(128), nullable=True),
    Column("requested_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("requested_by_user_id", UUID(as_uuid=True), nullable=True),
    Column("request_id", UUID(as_uuid=True), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("traceparent", String(55), nullable=False),
    Column("requested_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "workspace_id",
        "idempotency_key",
        name="uq_outbox_replay_requests_idempotency",
    ),
    ForeignKeyConstraint(
        ["event_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.outbox_events.event_id",
            f"{SCHEMA_TOKEN}.outbox_events.workspace_id",
        ],
        name="fk_outbox_replay_requests_event_workspace",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "reason_code ~ '^[A-Z][A-Z0-9_]{2,63}$'",
        name="ck_outbox_replay_requests_reason",
    ),
    CheckConstraint(
        "source_status IN ('published', 'dead_letter')",
        name="ck_outbox_replay_requests_source_status",
    ),
    CheckConstraint(
        "source_attempt_count >= 0",
        name="ck_outbox_replay_requests_attempt_count",
    ),
)

Index(
    "ix_outbox_replay_requests_event_time",
    outbox_replay_requests.c.workspace_id,
    outbox_replay_requests.c.event_id,
    outbox_replay_requests.c.requested_at,
)

audit_export_requests = Table(
    "audit_export_requests",
    metadata,
    Column("audit_export_request_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("actor_id", UUID(as_uuid=True), nullable=True),
    Column("action", String(255), nullable=True),
    Column("resource_type", String(128), nullable=True),
    Column("outcome", String(32), nullable=True),
    Column("occurred_from", DateTime(timezone=True), nullable=True),
    Column("occurred_to", DateTime(timezone=True), nullable=True),
    Column("field_mask", ARRAY(String(128)), nullable=False),
    Column("requested_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("requested_by_user_id", UUID(as_uuid=True), nullable=True),
    Column("request_id", UUID(as_uuid=True), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("traceparent", String(55), nullable=False),
    Column("status", String(32), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("claimed_by", String(255), nullable=True),
    Column("claim_until", DateTime(timezone=True), nullable=True),
    Column("last_error_code", String(128), nullable=True),
    Column("row_count", Integer, nullable=True),
    Column("result_sha256", String(64), nullable=True),
    Column("result_summary", String(500), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint(
        "workspace_id",
        "idempotency_key",
        name="uq_audit_export_requests_idempotency",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_audit_export_requests_workspace",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "outcome IS NULL OR outcome IN ('succeeded', 'denied', 'failed')",
        name="ck_audit_export_requests_outcome",
    ),
    CheckConstraint(
        "status IN ('pending', 'running', 'retry_wait', 'completed', 'dead_letter')",
        name="ck_audit_export_requests_status",
    ),
    CheckConstraint(
        "attempt_count BETWEEN 0 AND 3",
        name="ck_audit_export_requests_attempts",
    ),
    CheckConstraint(
        "request_hash ~ '^[0-9a-f]{64}$'",
        name="ck_audit_export_requests_hash",
    ),
    CheckConstraint(
        "occurred_from IS NULL OR occurred_to IS NULL OR occurred_from < occurred_to",
        name="ck_audit_export_requests_window",
    ),
    CheckConstraint(
        "(status = 'running' AND claimed_by IS NOT NULL AND claim_until IS NOT NULL) OR "
        "(status <> 'running' AND claimed_by IS NULL AND claim_until IS NULL)",
        name="ck_audit_export_requests_claim",
    ),
    CheckConstraint(
        "(status = 'completed' AND completed_at IS NOT NULL AND row_count IS NOT NULL "
        "AND row_count >= 0 AND result_sha256 IS NOT NULL AND result_summary IS NOT NULL "
        "AND last_error_code IS NULL) OR "
        "(status = 'dead_letter' AND completed_at IS NOT NULL AND row_count IS NULL "
        "AND result_sha256 IS NULL AND result_summary IS NULL "
        "AND last_error_code IS NOT NULL) OR "
        "(status NOT IN ('completed', 'dead_letter') AND completed_at IS NULL "
        "AND row_count IS NULL AND result_sha256 IS NULL AND result_summary IS NULL)",
        name="ck_audit_export_requests_completion",
    ),
)
Index(
    "ix_audit_export_requests_claim",
    audit_export_requests.c.status,
    audit_export_requests.c.updated_at,
)
Index(
    "ix_audit_export_requests_workspace_created",
    audit_export_requests.c.workspace_id,
    audit_export_requests.c.created_at,
    audit_export_requests.c.audit_export_request_id,
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
