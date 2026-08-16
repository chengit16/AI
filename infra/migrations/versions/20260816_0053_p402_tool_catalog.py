"""扩展 P4-02 不可变工具版本，并建立套餐可用性事实。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0053"
down_revision: str | None = "20260816_0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
REGISTERED_AT = datetime(2026, 8, 16, tzinfo=UTC)
PLAN_CODES = ("personal_local", "enterprise_simulated")


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """在原工具事实源就地补齐治理字段，并恢复更严格的不可变保护。"""

    schema = _schema()
    op.execute(
        sa.text(
            f"DROP TRIGGER IF EXISTS trg_agent_tool_definitions_immutable "
            f'ON "{schema}"."agent_tool_definitions"'
        )
    )
    _add_definition_columns(schema)
    _enrich_existing_catalog(schema)
    _require_definition_columns(schema)
    _add_definition_constraints(schema)
    _create_plan_availability(schema)
    _seed_plan_availability(schema)
    _replace_immutable_function(schema, include_plan_availability=True)
    _create_immutable_triggers(schema)


def downgrade() -> None:
    """仅在目录仍为 P4-02 固定种子时移除治理扩展，避免丢失后续版本。"""

    schema = _schema()
    _reject_unsafe_downgrade(schema)
    op.execute(
        sa.text(
            f"DROP TRIGGER IF EXISTS trg_tool_plan_availability_immutable "
            f'ON "{schema}"."tool_plan_availability"'
        )
    )
    op.drop_table("tool_plan_availability", schema=schema)
    op.execute(
        sa.text(
            f"DROP TRIGGER IF EXISTS trg_agent_tool_definitions_immutable "
            f'ON "{schema}"."agent_tool_definitions"'
        )
    )
    _drop_definition_constraints(schema)
    _drop_definition_columns(schema)
    _replace_immutable_function(schema, include_plan_availability=False)
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_agent_tool_definitions_immutable
            BEFORE UPDATE OR DELETE ON "{schema}"."agent_tool_definitions"
            FOR EACH ROW EXECUTE FUNCTION "{schema}".reject_agent_configuration_mutation()
            """
        )
    )


def _add_definition_columns(schema: str) -> None:
    columns: tuple[sa.Column[Any], ...] = (
        sa.Column("display_name", sa.String(120), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("risk_level", sa.String(16), nullable=True),
        sa.Column("adapter_kind", sa.String(32), nullable=True),
        sa.Column("input_schema_document", postgresql.JSONB(), nullable=True),
        sa.Column("input_schema_hash", sa.String(64), nullable=True),
        sa.Column("output_schema_document", postgresql.JSONB(), nullable=True),
        sa.Column("output_schema_hash", sa.String(64), nullable=True),
        sa.Column("credential_requirement", sa.String(32), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("retry_mode", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=True),
        sa.Column("definition_hash", sa.String(64), nullable=True),
        sa.Column("synthetic", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in columns:
        op.add_column("agent_tool_definitions", column, schema=schema)


def _enrich_existing_catalog(schema: str) -> None:
    """只接受 P3-03 冻结的五个身份，避免在未知目录上猜测治理语义。"""

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            f"""
            SELECT tool_id::text, tool_version, tool_key, access_mode, permission_code
            FROM "{schema}"."agent_tool_definitions"
            ORDER BY tool_key
            """
        )
    ).mappings()
    actual = {
        (str(row["tool_id"]), int(row["tool_version"])): (
            str(row["tool_key"]),
            str(row["access_mode"]),
            str(row["permission_code"]),
        )
        for row in rows
    }
    definitions = _definitions()
    expected = {
        (item["tool_id"], item["tool_version"]): (
            item["tool_key"],
            item["access_mode"],
            item["permission_code"],
        )
        for item in definitions
    }
    if actual != expected:
        raise RuntimeError("既有工具目录偏离 P3-03 冻结事实; 拒绝猜测回填")
    table = sa.table(
        "agent_tool_definitions",
        sa.column("tool_id", postgresql.UUID(as_uuid=True)),
        sa.column("tool_version", sa.Integer()),
        sa.column("display_name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("risk_level", sa.String()),
        sa.column("adapter_kind", sa.String()),
        sa.column("input_schema_document", postgresql.JSONB()),
        sa.column("input_schema_hash", sa.String()),
        sa.column("output_schema_document", postgresql.JSONB()),
        sa.column("output_schema_hash", sa.String()),
        sa.column("credential_requirement", sa.String()),
        sa.column("timeout_seconds", sa.Integer()),
        sa.column("retry_mode", sa.String()),
        sa.column("status", sa.String()),
        sa.column("definition_hash", sa.String()),
        sa.column("synthetic", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        schema=schema,
    )
    for definition in definitions:
        values = {
            key: value
            for key, value in definition.items()
            if key not in {"tool_id", "tool_version", "tool_key", "access_mode", "permission_code"}
        }
        connection.execute(
            table.update()
            .where(
                table.c.tool_id == definition["tool_id"],
                table.c.tool_version == definition["tool_version"],
            )
            .values(**values)
        )


def _require_definition_columns(schema: str) -> None:
    for name in (
        "display_name",
        "description",
        "risk_level",
        "adapter_kind",
        "input_schema_document",
        "input_schema_hash",
        "output_schema_document",
        "output_schema_hash",
        "credential_requirement",
        "timeout_seconds",
        "retry_mode",
        "status",
        "definition_hash",
        "synthetic",
        "created_at",
    ):
        op.alter_column("agent_tool_definitions", name, nullable=False, schema=schema)


def _add_definition_constraints(schema: str) -> None:
    constraints = (
        (
            "ck_agent_tool_definitions_display_name",
            "char_length(btrim(display_name)) BETWEEN 1 AND 120",
        ),
        (
            "ck_agent_tool_definitions_description",
            "char_length(btrim(description)) BETWEEN 1 AND 500",
        ),
        (
            "ck_agent_tool_definitions_risk",
            "risk_level IN ('low', 'medium', 'high', 'critical')",
        ),
        (
            "ck_agent_tool_definitions_adapter",
            "adapter_kind IN ('internal_read', 'synthetic_internal_write')",
        ),
        (
            "ck_agent_tool_definitions_input_schema",
            "jsonb_typeof(input_schema_document) = 'object' "
            f"AND input_schema_document ->> '$schema' = '{SCHEMA_DIALECT}' "
            "AND input_schema_document ->> 'type' = 'object' "
            "AND input_schema_document -> 'additionalProperties' = 'false'::jsonb "
            "AND pg_column_size(input_schema_document) <= 32768",
        ),
        (
            "ck_agent_tool_definitions_output_schema",
            "jsonb_typeof(output_schema_document) = 'object' "
            f"AND output_schema_document ->> '$schema' = '{SCHEMA_DIALECT}' "
            "AND output_schema_document ->> 'type' = 'object' "
            "AND output_schema_document -> 'additionalProperties' = 'false'::jsonb "
            "AND pg_column_size(output_schema_document) <= 32768",
        ),
        (
            "ck_agent_tool_definitions_hashes",
            "input_schema_hash ~ '^[0-9a-f]{64}$' "
            "AND output_schema_hash ~ '^[0-9a-f]{64}$' "
            "AND definition_hash ~ '^[0-9a-f]{64}$'",
        ),
        (
            "ck_agent_tool_definitions_credential",
            "credential_requirement IN ('none', 'credential_ref')",
        ),
        ("ck_agent_tool_definitions_timeout", "timeout_seconds BETWEEN 1 AND 120"),
        (
            "ck_agent_tool_definitions_retry",
            "retry_mode IN ('none', 'safe_read', 'idempotent_write')",
        ),
        ("ck_agent_tool_definitions_status", "status IN ('active', 'retired')"),
        (
            "ck_agent_tool_definitions_governance",
            "(adapter_kind = 'internal_read' AND access_mode = 'read' "
            "AND synthetic = false AND retry_mode IN ('none', 'safe_read')) OR "
            "(adapter_kind = 'synthetic_internal_write' AND access_mode = 'write' "
            "AND synthetic = true AND risk_level IN ('high', 'critical') "
            "AND retry_mode IN ('none', 'idempotent_write'))",
        ),
    )
    for name, condition in constraints:
        op.create_check_constraint(name, "agent_tool_definitions", condition, schema=schema)


def _create_plan_availability(schema: str) -> None:
    op.create_table(
        "tool_plan_availability",
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tool_version", sa.Integer(), primary_key=True),
        sa.Column("plan_code", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tool_id", "tool_version"],
            [
                f"{schema}.agent_tool_definitions.tool_id",
                f"{schema}.agent_tool_definitions.tool_version",
            ],
            name="fk_tool_plan_availability_definition",
        ),
        sa.CheckConstraint(
            "plan_code ~ '^[a-z][a-z0-9_]{2,63}$'",
            name="ck_tool_plan_availability_plan_code",
        ),
        schema=schema,
    )


def _seed_plan_availability(schema: str) -> None:
    table = sa.table(
        "tool_plan_availability",
        sa.column("tool_id", postgresql.UUID(as_uuid=True)),
        sa.column("tool_version", sa.Integer()),
        sa.column("plan_code", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        schema=schema,
    )
    op.bulk_insert(
        table,
        [
            {
                "tool_id": definition["tool_id"],
                "tool_version": definition["tool_version"],
                "plan_code": plan_code,
                "created_at": REGISTERED_AT,
            }
            for definition in _definitions()
            for plan_code in PLAN_CODES
        ],
    )


def _replace_immutable_function(schema: str, *, include_plan_availability: bool) -> None:
    global_tables = ["agent_safety_policy_versions", "agent_tool_definitions"]
    if include_plan_availability:
        global_tables.append("tool_plan_availability")
    table_literals = ", ".join(f"'{name}'" for name in global_tables)
    op.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION "{schema}".reject_agent_configuration_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_TABLE_NAME NOT IN ({table_literals}) AND TG_OP = 'DELETE'
                   AND current_setting('ai_platform.lifecycle_purge', true) = 'on'
                THEN RETURN OLD;
                END IF;
                RAISE EXCEPTION 'agent configuration version is immutable'
                    USING ERRCODE = '55000';
            END;
            $$
            """
        )
    )


def _create_immutable_triggers(schema: str) -> None:
    for table_name in ("agent_tool_definitions", "tool_plan_availability"):
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table_name}_immutable
                BEFORE UPDATE OR DELETE ON "{schema}"."{table_name}"
                FOR EACH ROW EXECUTE FUNCTION "{schema}".reject_agent_configuration_mutation()
                """
            )
        )


def _drop_definition_constraints(schema: str) -> None:
    for name in reversed(
        (
            "ck_agent_tool_definitions_display_name",
            "ck_agent_tool_definitions_description",
            "ck_agent_tool_definitions_risk",
            "ck_agent_tool_definitions_adapter",
            "ck_agent_tool_definitions_input_schema",
            "ck_agent_tool_definitions_output_schema",
            "ck_agent_tool_definitions_hashes",
            "ck_agent_tool_definitions_credential",
            "ck_agent_tool_definitions_timeout",
            "ck_agent_tool_definitions_retry",
            "ck_agent_tool_definitions_status",
            "ck_agent_tool_definitions_governance",
        )
    ):
        op.drop_constraint(name, "agent_tool_definitions", type_="check", schema=schema)


def _drop_definition_columns(schema: str) -> None:
    for name in reversed(
        (
            "display_name",
            "description",
            "risk_level",
            "adapter_kind",
            "input_schema_document",
            "input_schema_hash",
            "output_schema_document",
            "output_schema_hash",
            "credential_requirement",
            "timeout_seconds",
            "retry_mode",
            "status",
            "definition_hash",
            "synthetic",
            "created_at",
        )
    ):
        op.drop_column("agent_tool_definitions", name, schema=schema)


def _reject_unsafe_downgrade(schema: str) -> None:
    connection = op.get_bind()
    identities = {
        (str(row.tool_id), int(row.tool_version))
        for row in connection.execute(
            sa.text(f'SELECT tool_id, tool_version FROM "{schema}"."agent_tool_definitions"')
        )
    }
    expected_identities = {(item["tool_id"], item["tool_version"]) for item in _definitions()}
    plan_rows = {
        (str(row.tool_id), int(row.tool_version), str(row.plan_code))
        for row in connection.execute(
            sa.text(
                f'SELECT tool_id, tool_version, plan_code FROM "{schema}"."tool_plan_availability"'
            )
        )
    }
    expected_plans = {
        (item["tool_id"], item["tool_version"], plan_code)
        for item in _definitions()
        for plan_code in PLAN_CODES
    }
    if identities != expected_identities or plan_rows != expected_plans:
        raise RuntimeError("存在 P4-02 之后的工具版本或套餐映射; 拒绝破坏性降级")


def _definitions() -> list[dict[str, Any]]:
    """返回五个首批工具的完整冻结语义；所有样本均为平台合成配置。"""

    definitions = [
        _definition(
            "001",
            "knowledge.search",
            "知识检索",
            "在已授权知识库范围内检索相关内容。",
            "medium",
            "knowledge.document.read",
            15,
            _object_schema(
                required=["query"],
                properties={
                    "query": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "knowledge_base_ids": {
                        "type": "array",
                        "items": {"type": "string", "format": "uuid"},
                        "maxItems": 50,
                        "uniqueItems": True,
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
            ),
            _object_schema(
                required=["items"],
                properties={
                    "items": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["chunk_id", "document_id", "text", "score"],
                            "properties": {
                                "chunk_id": {"type": "string", "format": "uuid"},
                                "document_id": {"type": "string", "format": "uuid"},
                                "text": {"type": "string", "maxLength": 16000},
                                "score": {"type": "number", "minimum": 0, "maximum": 1},
                            },
                        },
                    }
                },
            ),
        ),
        _definition(
            "002",
            "document.read_authorized_range",
            "读取授权文档片段",
            "按稳定 Chunk 序号读取当前主体有权访问的文档片段。",
            "medium",
            "knowledge.document.read",
            10,
            _object_schema(
                required=["document_id", "start_sequence", "end_sequence"],
                properties={
                    "document_id": {"type": "string", "format": "uuid"},
                    "start_sequence": {"type": "integer", "minimum": 1},
                    "end_sequence": {"type": "integer", "minimum": 1},
                },
            ),
            _object_schema(
                required=["document_id", "chunks"],
                properties={
                    "document_id": {"type": "string", "format": "uuid"},
                    "chunks": {
                        "type": "array",
                        "maxItems": 100,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["chunk_id", "sequence", "text", "page_number"],
                            "properties": {
                                "chunk_id": {"type": "string", "format": "uuid"},
                                "sequence": {"type": "integer", "minimum": 1},
                                "text": {"type": "string", "maxLength": 16000},
                                "page_number": {"type": ["integer", "null"], "minimum": 1},
                            },
                        },
                    },
                },
            ),
        ),
        _definition(
            "003",
            "workflow.get_status",
            "查询工作流状态",
            "读取当前工作空间内已授权工作流运行状态。",
            "low",
            "workflow.run.read",
            5,
            _object_schema(
                required=["workflow_run_id"],
                properties={"workflow_run_id": {"type": "string", "format": "uuid"}},
            ),
            _object_schema(
                required=["workflow_run_id", "status", "current_step", "total_steps"],
                properties={
                    "workflow_run_id": {"type": "string", "format": "uuid"},
                    "status": {"enum": ["pending", "running", "completed", "failed", "cancelled"]},
                    "current_step": {"type": ["integer", "null"], "minimum": 1},
                    "total_steps": {"type": "integer", "minimum": 0},
                },
            ),
        ),
        _definition(
            "004",
            "approval.get_status",
            "查询审批状态",
            "读取当前工作空间内已授权审批实例状态。",
            "low",
            "approval.instance.read",
            5,
            _object_schema(
                required=["approval_instance_id"],
                properties={"approval_instance_id": {"type": "string", "format": "uuid"}},
            ),
            _object_schema(
                required=["approval_instance_id", "status", "current_level", "total_levels"],
                properties={
                    "approval_instance_id": {"type": "string", "format": "uuid"},
                    "status": {"enum": ["pending", "approved", "rejected", "expired", "withdrawn"]},
                    "current_level": {"type": ["integer", "null"], "minimum": 1},
                    "total_levels": {"type": "integer", "minimum": 1, "maximum": 5},
                },
            ),
        ),
        _definition(
            "005",
            "quota.get_usage",
            "查询套餐用量",
            "读取当前工作空间套餐和各项额度用量。",
            "low",
            "workspace.entitlement.read",
            5,
            _object_schema(
                required=[],
                properties={
                    "metrics": {
                        "type": "array",
                        "uniqueItems": True,
                        "maxItems": 5,
                        "items": {
                            "enum": [
                                "members",
                                "storage_bytes",
                                "knowledge_bases",
                                "published_agents",
                                "questions_monthly",
                            ]
                        },
                    }
                },
            ),
            _object_schema(
                required=["plan_code", "quotas"],
                properties={
                    "plan_code": {"type": "string", "minLength": 1, "maxLength": 64},
                    "quotas": {
                        "type": "array",
                        "maxItems": 5,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["metric", "used_value", "limit_value"],
                            "properties": {
                                "metric": {"type": "string", "minLength": 1, "maxLength": 64},
                                "used_value": {"type": "integer", "minimum": 0},
                                "limit_value": {"type": "integer", "minimum": 0},
                            },
                        },
                    },
                },
            ),
        ),
    ]
    return definitions


def _definition(
    suffix: str,
    key: str,
    display_name: str,
    description: str,
    risk_level: str,
    permission_code: str,
    timeout_seconds: int,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
) -> dict[str, Any]:
    semantic = {
        "tool_id": f"a7000000-0000-4000-8000-000000000{suffix}",
        "tool_version": 1,
        "tool_key": key,
        "display_name": display_name,
        "description": description,
        "access_mode": "read",
        "risk_level": risk_level,
        "adapter_kind": "internal_read",
        "input_schema_document": input_schema,
        "output_schema_document": output_schema,
        "permission_code": permission_code,
        "credential_requirement": "none",
        "timeout_seconds": timeout_seconds,
        "retry_mode": "safe_read",
        "status": "active",
        "synthetic": False,
    }
    semantic["input_schema_hash"] = _digest(input_schema)
    semantic["output_schema_hash"] = _digest(output_schema)
    semantic["definition_hash"] = _digest(semantic)
    semantic["created_at"] = REGISTERED_AT
    return semantic


def _object_schema(
    *,
    required: list[str],
    properties: dict[str, Any],
) -> dict[str, Any]:
    return {
        "$schema": SCHEMA_DIALECT,
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _digest(document: object) -> str:
    return hashlib.sha256(_canonical_json(document)).hexdigest()


def _canonical_json(document: object) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
