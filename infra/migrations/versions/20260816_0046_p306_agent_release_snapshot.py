"""建立 P3-06 AgentRelease 来源绑定、快照校验和发布防绕过门禁。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0046"
down_revision: str | None = "20260816_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _extend_release_sources(schema)
    _extend_idempotency_contract(schema, include_release=True)
    _validate_release_insert(schema)
    _protect_candidate_release_transition(schema)


def downgrade() -> None:
    schema = _schema()
    _reject_unsafe_downgrade(schema)
    _drop_candidate_release_protection(schema)
    _drop_release_insert_validation(schema)
    _extend_idempotency_contract(schema, include_release=False)
    _contract_release_sources(schema)


def _extend_release_sources(schema: str) -> None:
    op.drop_constraint(
        "ck_agent_releases_kind_payload",
        "agent_releases",
        schema=schema,
        type_="check",
    )
    for column in (
        sa.Column("source_draft_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_draft_revision", sa.Integer(), nullable=True),
        sa.Column("evaluation_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approval_binding_id", postgresql.UUID(as_uuid=True), nullable=True),
    ):
        op.add_column("agent_releases", column, schema=schema)
    op.create_foreign_key(
        "fk_agent_releases_source_revision",
        "agent_releases",
        "agent_draft_revisions",
        ["source_draft_id", "source_draft_revision", "agent_id", "workspace_id"],
        ["draft_id", "revision", "agent_id", "workspace_id"],
        source_schema=schema,
        referent_schema=schema,
    )
    op.create_foreign_key(
        "fk_agent_releases_evaluation_run",
        "agent_releases",
        "agent_evaluation_runs",
        ["evaluation_run_id", "workspace_id"],
        ["evaluation_run_id", "workspace_id"],
        source_schema=schema,
        referent_schema=schema,
    )
    op.create_foreign_key(
        "fk_agent_releases_approval_binding",
        "agent_releases",
        "agent_approval_bindings",
        ["approval_binding_id", "workspace_id"],
        ["approval_binding_id", "workspace_id"],
        source_schema=schema,
        referent_schema=schema,
    )
    op.create_check_constraint(
        "ck_agent_releases_source_revision",
        "agent_releases",
        "source_draft_revision IS NULL OR source_draft_revision >= 1",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_agent_releases_kind_payload",
        "agent_releases",
        "(release_kind = 'system' AND candidate_id IS NULL AND candidate_hash IS NULL "
        "AND source_draft_id IS NULL AND source_draft_revision IS NULL "
        "AND evaluation_run_id IS NULL AND approval_binding_id IS NULL "
        "AND snapshot IS NULL AND snapshot_hash IS NULL) OR "
        "(release_kind = 'custom' AND candidate_id IS NOT NULL AND candidate_hash IS NOT NULL "
        "AND source_draft_id IS NOT NULL AND source_draft_revision IS NOT NULL "
        "AND evaluation_run_id IS NOT NULL AND approval_binding_id IS NOT NULL "
        "AND jsonb_typeof(snapshot) = 'object' AND snapshot_hash IS NOT NULL)",
        schema=schema,
    )


def _extend_idempotency_contract(schema: str, *, include_release: bool) -> None:
    for constraint in (
        "ck_agent_control_requests_result_type",
        "ck_agent_control_requests_operation",
    ):
        op.drop_constraint(
            constraint,
            "agent_control_requests",
            schema=schema,
            type_="check",
        )
    operations = (
        "'agent.create', 'agent.draft.update', 'agent.release.request', "
        + ("'agent.release.publish', " if include_release else "")
        + "'agent.archive'"
    )
    result_types = "'agent', 'draft', 'candidate'"
    if include_release:
        result_types += ", 'release'"
    op.create_check_constraint(
        "ck_agent_control_requests_operation",
        "agent_control_requests",
        f"operation IN ({operations})",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_agent_control_requests_result_type",
        "agent_control_requests",
        f"result_type IN ({result_types})",
        schema=schema,
    )


def _validate_release_insert(schema: str) -> None:
    # Trigger 逐项复核快照与不可变来源；摘要由应用复算，现有不可变 Trigger 禁止事后替换。
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".validate_custom_agent_release()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.release_kind = 'system' THEN
                    RETURN NEW;
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                    FROM "{schema}".agent_release_candidates candidate
                    JOIN "{schema}".agents agent
                      ON agent.agent_id = candidate.agent_id
                     AND agent.workspace_id = candidate.workspace_id
                    JOIN "{schema}".agent_draft_revisions revision
                      ON revision.draft_id = candidate.draft_id
                     AND revision.revision = candidate.draft_revision
                     AND revision.agent_id = candidate.agent_id
                     AND revision.workspace_id = candidate.workspace_id
                    JOIN "{schema}".agent_approval_bindings binding
                      ON binding.approval_binding_id = NEW.approval_binding_id
                     AND binding.workspace_id = NEW.workspace_id
                     AND binding.candidate_id = candidate.candidate_id
                    JOIN "{schema}".approval_instances approval
                      ON approval.approval_instance_id = binding.approval_instance_id
                     AND approval.workspace_id = binding.workspace_id
                    JOIN "{schema}".agent_evaluation_runs evaluation
                      ON evaluation.evaluation_run_id = NEW.evaluation_run_id
                     AND evaluation.workspace_id = NEW.workspace_id
                     AND evaluation.candidate_id = candidate.candidate_id
                    JOIN "{schema}".agent_evaluation_dataset_versions dataset
                      ON dataset.dataset_version_id = evaluation.dataset_version_id
                     AND dataset.workspace_id = evaluation.workspace_id
                    JOIN "{schema}".agent_evaluation_policy_versions policy
                      ON policy.evaluation_policy_version_id
                       = evaluation.evaluation_policy_version_id
                    WHERE candidate.candidate_id = NEW.candidate_id
                      AND candidate.workspace_id = NEW.workspace_id
                      AND candidate.agent_id = NEW.agent_id
                      AND candidate.status = 'approved'
                      AND agent.agent_kind = 'custom'
                      AND agent.status = 'active'
                      AND candidate.candidate_hash = NEW.candidate_hash
                      AND candidate.config_hash = NEW.config_hash
                      AND revision.draft_id = NEW.source_draft_id
                      AND revision.revision = NEW.source_draft_revision
                      AND revision.config_hash = NEW.config_hash
                      AND binding.agent_id = NEW.agent_id
                      AND binding.candidate_hash = NEW.candidate_hash
                      AND binding.config_hash = NEW.config_hash
                      AND binding.evaluation_run_id = NEW.evaluation_run_id
                      AND binding.evaluation_policy_version_id
                          = evaluation.evaluation_policy_version_id
                      AND binding.evaluation_result_hash = evaluation.result_hash
                      AND approval.status = 'approved'
                      AND approval.completed_at IS NOT NULL
                      AND evaluation.status = 'passed'
                      AND evaluation.passed_cases = evaluation.total_cases
                      AND NEW.runtime_config_version_id
                          = (revision.configuration ->> 'runtime_config_version_id')::uuid
                      AND NEW.released_at >= approval.completed_at
                      AND (
                          SELECT count(*) FROM jsonb_object_keys(NEW.snapshot)
                      ) = 6
                      AND (NEW.snapshot ->> 'snapshot_schema_version')::integer = 1
                      AND NEW.snapshot ->> 'source_draft_id' = revision.draft_id::text
                      AND (NEW.snapshot ->> 'source_draft_revision')::integer
                          = revision.revision
                      AND NEW.snapshot -> 'configuration' = revision.configuration
                      AND jsonb_typeof(NEW.snapshot -> 'evaluation') = 'object'
                      AND (
                          SELECT count(*)
                          FROM jsonb_object_keys(NEW.snapshot -> 'evaluation')
                      ) = 6
                      AND NEW.snapshot -> 'evaluation' ->> 'evaluation_run_id'
                          = evaluation.evaluation_run_id::text
                      AND NEW.snapshot -> 'evaluation' ->> 'dataset_version'
                          = dataset.dataset_version
                      AND NEW.snapshot -> 'evaluation' ->> 'evaluation_policy_version_id'
                          = evaluation.evaluation_policy_version_id::text
                      AND NEW.snapshot -> 'evaluation' ->> 'status' = 'passed'
                      AND NEW.snapshot -> 'evaluation' -> 'required_checks'
                          = policy.required_check_codes
                      AND (NEW.snapshot -> 'evaluation' ->> 'completed_at')::timestamptz
                          = evaluation.completed_at
                      AND jsonb_typeof(NEW.snapshot -> 'approval') = 'object'
                      AND (
                          SELECT count(*)
                          FROM jsonb_object_keys(NEW.snapshot -> 'approval')
                      ) = 5
                      AND NEW.snapshot -> 'approval' ->> 'approval_instance_id'
                          = approval.approval_instance_id::text
                      AND NEW.snapshot -> 'approval' ->> 'approval_policy_version_id'
                          = binding.approval_policy_version_id::text
                      AND NEW.snapshot -> 'approval' ->> 'candidate_hash'
                          = candidate.candidate_hash
                      AND NEW.snapshot -> 'approval' ->> 'status' = 'approved'
                      AND (NEW.snapshot -> 'approval' ->> 'approved_at')::timestamptz
                          = approval.completed_at
                      AND (
                          SELECT count(*)
                          FROM "{schema}".agent_evaluation_check_results result
                          WHERE result.evaluation_run_id = evaluation.evaluation_run_id
                            AND result.workspace_id = evaluation.workspace_id
                            AND result.status = 'passed'
                      ) = jsonb_array_length(policy.required_check_codes)
                      AND NOT EXISTS (
                          SELECT 1
                          FROM "{schema}".agent_evaluation_case_results result
                          WHERE result.evaluation_run_id = evaluation.evaluation_run_id
                            AND result.workspace_id = evaluation.workspace_id
                            AND result.outcome <> 'passed'
                      )
                ) THEN
                    RAISE EXCEPTION 'invalid custom agent release snapshot'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_agent_releases_validate_insert
            BEFORE INSERT ON "{schema}"."agent_releases"
            FOR EACH ROW EXECUTE FUNCTION "{schema}".validate_custom_agent_release()
            """
        )
    )


def _protect_candidate_release_transition(schema: str) -> None:
    # Release 必须先在同一事务写入，候选才能从 approved 进入 released。
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".require_agent_release_for_candidate()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.status = 'released' AND NOT EXISTS (
                    SELECT 1
                    FROM "{schema}".agent_releases release
                    WHERE release.candidate_id = NEW.candidate_id
                      AND release.workspace_id = NEW.workspace_id
                      AND release.agent_id = NEW.agent_id
                      AND release.release_kind = 'custom'
                      AND release.candidate_hash = NEW.candidate_hash
                      AND release.config_hash = NEW.config_hash
                      AND release.source_draft_id = NEW.draft_id
                      AND release.source_draft_revision = NEW.draft_revision
                ) THEN
                    RAISE EXCEPTION 'immutable agent release is required'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_agent_release_candidates_release
            BEFORE UPDATE ON "{schema}"."agent_release_candidates"
            FOR EACH ROW EXECUTE FUNCTION "{schema}".require_agent_release_for_candidate()
            """
        )
    )


def _drop_candidate_release_protection(schema: str) -> None:
    op.execute(
        sa.text(
            f"DROP TRIGGER trg_agent_release_candidates_release "
            f'ON "{schema}"."agent_release_candidates"'
        )
    )
    op.execute(sa.text(f'DROP FUNCTION "{schema}".require_agent_release_for_candidate()'))


def _drop_release_insert_validation(schema: str) -> None:
    op.execute(
        sa.text(f'DROP TRIGGER trg_agent_releases_validate_insert ON "{schema}"."agent_releases"')
    )
    op.execute(sa.text(f'DROP FUNCTION "{schema}".validate_custom_agent_release()'))


def _contract_release_sources(schema: str) -> None:
    op.drop_constraint(
        "ck_agent_releases_kind_payload",
        "agent_releases",
        schema=schema,
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_releases_source_revision",
        "agent_releases",
        schema=schema,
        type_="check",
    )
    for constraint in (
        "fk_agent_releases_approval_binding",
        "fk_agent_releases_evaluation_run",
        "fk_agent_releases_source_revision",
    ):
        op.drop_constraint(
            constraint,
            "agent_releases",
            schema=schema,
            type_="foreignkey",
        )
    for column in (
        "approval_binding_id",
        "evaluation_run_id",
        "source_draft_revision",
        "source_draft_id",
    ):
        op.drop_column("agent_releases", column, schema=schema)
    op.create_check_constraint(
        "ck_agent_releases_kind_payload",
        "agent_releases",
        "(release_kind = 'system' AND candidate_id IS NULL AND candidate_hash IS NULL "
        "AND snapshot IS NULL AND snapshot_hash IS NULL) OR "
        "(release_kind = 'custom' AND candidate_id IS NOT NULL AND candidate_hash IS NOT NULL "
        "AND jsonb_typeof(snapshot) = 'object' AND snapshot_hash IS NOT NULL)",
        schema=schema,
    )


def _reject_unsafe_downgrade(schema: str) -> None:
    count = (
        op.get_bind()
        .execute(
            sa.text(
                f'SELECT count(*) FROM "{schema}"."agent_releases" WHERE release_kind = \'custom\''
            )
        )
        .scalar_one()
    )
    if int(count) > 0:
        raise RuntimeError("存在自定义 AgentRelease, 拒绝降级并丢失发布来源")
