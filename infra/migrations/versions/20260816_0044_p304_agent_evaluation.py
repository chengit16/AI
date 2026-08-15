"""建立 P3-04 固定测试集、确定性策略、不可变结果和候选测试门禁。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0044"
down_revision: str | None = "20260816_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK_CODES = "'functional', 'authorization', 'prompt_injection', 'citation', 'output_contract'"
POLICY_HASH = "0f3b7e974e87e737733e2e4143c4cd610a8653a50264d45cb39e2cc0cdbc8b52"


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _create_policy_versions(schema)
    _create_dataset_versions(schema)
    _create_test_cases(schema)
    _create_evaluation_runs(schema)
    _create_check_results(schema)
    _create_case_results(schema)
    _seed_policy(schema)
    _protect_evaluation_facts(schema)
    _protect_candidate_transitions(schema)


def downgrade() -> None:
    schema = _schema()
    _reject_unsafe_downgrade(schema)
    _drop_candidate_transition_protection(schema)
    _drop_evaluation_fact_protection(schema)
    op.drop_table("agent_evaluation_case_results", schema=schema)
    op.drop_table("agent_evaluation_check_results", schema=schema)
    op.drop_table("agent_evaluation_runs", schema=schema)
    op.drop_table("agent_evaluation_test_cases", schema=schema)
    op.drop_table("agent_evaluation_dataset_versions", schema=schema)
    op.drop_table("agent_evaluation_policy_versions", schema=schema)


def _create_policy_versions(schema: str) -> None:
    op.create_table(
        "agent_evaluation_policy_versions",
        sa.Column(
            "evaluation_policy_version_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("policy_key", sa.String(80), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("required_check_codes", postgresql.JSONB(), nullable=False),
        sa.Column("hard_gate_check_codes", postgresql.JSONB(), nullable=False),
        sa.Column("minimum_check_scores", postgresql.JSONB(), nullable=False),
        sa.Column("failure_handling", sa.String(32), nullable=False),
        sa.Column("timeout_handling", sa.String(32), nullable=False),
        sa.Column("skipped_handling", sa.String(32), nullable=False),
        sa.Column("evaluator_kind", sa.String(32), nullable=False),
        sa.Column("online_llm_grading", sa.Boolean(), nullable=False),
        sa.Column("multimodal_image_qa", sa.Boolean(), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.UniqueConstraint(
            "policy_key",
            "version_number",
            name="uq_agent_evaluation_policies_key_version",
        ),
        sa.CheckConstraint(
            "version_number >= 1",
            name="ck_agent_evaluation_policies_version",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(required_check_codes) = 'array' "
            "AND jsonb_array_length(required_check_codes) = 5 "
            "AND jsonb_typeof(hard_gate_check_codes) = 'array' "
            "AND jsonb_array_length(hard_gate_check_codes) = 4 "
            "AND jsonb_typeof(minimum_check_scores) = 'object'",
            name="ck_agent_evaluation_policies_documents",
        ),
        sa.CheckConstraint(
            "failure_handling = 'block_release' "
            "AND timeout_handling = 'count_as_failure' "
            "AND skipped_handling = 'count_as_failure'",
            name="ck_agent_evaluation_policies_failure_modes",
        ),
        sa.CheckConstraint(
            "evaluator_kind = 'deterministic_rules' "
            "AND online_llm_grading = false AND multimodal_image_qa = false",
            name="ck_agent_evaluation_policies_deferred_capabilities",
        ),
        sa.CheckConstraint(
            "policy_hash ~ '^[0-9a-f]{64}$'",
            name="ck_agent_evaluation_policies_hash",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'retired')",
            name="ck_agent_evaluation_policies_status",
        ),
        schema=schema,
    )


def _create_dataset_versions(schema: str) -> None:
    op.create_table(
        "agent_evaluation_dataset_versions",
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("dataset_version", sa.String(80), nullable=False),
        sa.Column("dataset_hash", sa.String(64), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "dataset_version_id",
            "workspace_id",
            name="uq_agent_evaluation_datasets_id_workspace",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "dataset_version",
            name="uq_agent_evaluation_datasets_workspace_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_agent_evaluation_datasets_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_agent_evaluation_datasets_creator",
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_agent_evaluation_datasets_name",
        ),
        sa.CheckConstraint(
            "dataset_version ~ '^p304-[a-z0-9][a-z0-9-]{1,60}-v[1-9][0-9]*$'",
            name="ck_agent_evaluation_datasets_version",
        ),
        sa.CheckConstraint(
            "dataset_hash ~ '^[0-9a-f]{64}$'",
            name="ck_agent_evaluation_datasets_hash",
        ),
        schema=schema,
    )


def _create_test_cases(schema: str) -> None:
    op.create_table(
        "agent_evaluation_test_cases",
        sa.Column("case_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("case_key", sa.String(80), nullable=False),
        sa.Column("check_code", sa.String(32), nullable=False),
        sa.Column("input_fixture", postgresql.JSONB(), nullable=False),
        sa.Column("expected_fixture", postgresql.JSONB(), nullable=False),
        sa.Column("timeout_ms", sa.Integer(), nullable=False),
        sa.Column("minimum_score_bps", sa.Integer(), nullable=False),
        sa.Column("case_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "case_id",
            "dataset_version_id",
            "workspace_id",
            name="uq_agent_evaluation_cases_identity",
        ),
        sa.UniqueConstraint(
            "dataset_version_id",
            "case_key",
            name="uq_agent_evaluation_cases_key",
        ),
        sa.UniqueConstraint(
            "dataset_version_id",
            "position",
            name="uq_agent_evaluation_cases_position",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id", "workspace_id"],
            [
                f"{schema}.agent_evaluation_dataset_versions.dataset_version_id",
                f"{schema}.agent_evaluation_dataset_versions.workspace_id",
            ],
            name="fk_agent_evaluation_cases_dataset",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("position >= 1", name="ck_agent_evaluation_cases_position"),
        sa.CheckConstraint(
            "case_key ~ '^[a-z][a-z0-9_.-]{2,79}$'",
            name="ck_agent_evaluation_cases_key",
        ),
        sa.CheckConstraint(
            f"check_code IN ({CHECK_CODES})",
            name="ck_agent_evaluation_cases_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_fixture) = 'object' AND jsonb_typeof(expected_fixture) = 'object'",
            name="ck_agent_evaluation_cases_fixtures",
        ),
        sa.CheckConstraint(
            "timeout_ms BETWEEN 1 AND 120000",
            name="ck_agent_evaluation_cases_timeout",
        ),
        sa.CheckConstraint(
            "minimum_score_bps BETWEEN 0 AND 10000",
            name="ck_agent_evaluation_cases_score",
        ),
        sa.CheckConstraint(
            "case_hash ~ '^[0-9a-f]{64}$'",
            name="ck_agent_evaluation_cases_hash",
        ),
        schema=schema,
    )


def _create_evaluation_runs(schema: str) -> None:
    op.create_table(
        "agent_evaluation_runs",
        sa.Column("evaluation_run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "evaluation_policy_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("candidate_hash", sa.String(64), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("evaluator_version", sa.String(64), nullable=False),
        sa.Column("evidence_level", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("total_cases", sa.Integer(), nullable=False),
        sa.Column("passed_cases", sa.Integer(), nullable=False),
        sa.Column("failed_cases", sa.Integer(), nullable=False),
        sa.Column("timeout_cases", sa.Integer(), nullable=False),
        sa.Column("skipped_cases", sa.Integer(), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "evaluation_run_id",
            "workspace_id",
            name="uq_agent_evaluation_runs_id_workspace",
        ),
        sa.UniqueConstraint(
            "evaluation_run_id",
            "dataset_version_id",
            "workspace_id",
            name="uq_agent_evaluation_runs_dataset_identity",
        ),
        sa.UniqueConstraint(
            "candidate_id",
            "dataset_version_id",
            "evaluation_policy_version_id",
            name="uq_agent_evaluation_runs_identity",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "workspace_id"],
            [
                f"{schema}.agent_release_candidates.candidate_id",
                f"{schema}.agent_release_candidates.workspace_id",
            ],
            name="fk_agent_evaluation_runs_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id", "workspace_id"],
            [
                f"{schema}.agent_evaluation_dataset_versions.dataset_version_id",
                f"{schema}.agent_evaluation_dataset_versions.workspace_id",
            ],
            name="fk_agent_evaluation_runs_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_policy_version_id"],
            [f"{schema}.agent_evaluation_policy_versions.evaluation_policy_version_id"],
            name="fk_agent_evaluation_runs_policy",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_agent_evaluation_runs_creator",
        ),
        sa.CheckConstraint(
            "candidate_hash ~ '^[0-9a-f]{64}$' AND config_hash ~ '^[0-9a-f]{64}$' "
            "AND result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_agent_evaluation_runs_hashes",
        ),
        sa.CheckConstraint(
            "evaluator_version ~ '^[a-z][a-z0-9._-]{2,63}$'",
            name="ck_agent_evaluation_runs_evaluator",
        ),
        sa.CheckConstraint(
            "evidence_level = 'core_functional'",
            name="ck_agent_evaluation_runs_evidence_level",
        ),
        sa.CheckConstraint(
            "status IN ('passed', 'failed')",
            name="ck_agent_evaluation_runs_status",
        ),
        sa.CheckConstraint(
            "total_cases >= 5 AND passed_cases >= 0 AND failed_cases >= 0 "
            "AND timeout_cases >= 0 AND skipped_cases >= 0 "
            "AND total_cases = passed_cases + failed_cases + timeout_cases + skipped_cases",
            name="ck_agent_evaluation_runs_counts",
        ),
        sa.CheckConstraint(
            "status <> 'passed' OR passed_cases = total_cases",
            name="ck_agent_evaluation_runs_passed",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_agent_evaluation_runs_candidate_time",
        "agent_evaluation_runs",
        ["workspace_id", "candidate_id", "completed_at"],
        schema=schema,
    )


def _create_check_results(schema: str) -> None:
    op.create_table(
        "agent_evaluation_check_results",
        sa.Column("evaluation_run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("check_code", sa.String(32), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False),
        sa.Column("score_bps", sa.Integer(), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "evaluation_run_id",
            "position",
            name="uq_agent_evaluation_checks_position",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id", "workspace_id"],
            [
                f"{schema}.agent_evaluation_runs.evaluation_run_id",
                f"{schema}.agent_evaluation_runs.workspace_id",
            ],
            name="fk_agent_evaluation_checks_run",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"check_code IN ({CHECK_CODES})",
            name="ck_agent_evaluation_checks_code",
        ),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 5",
            name="ck_agent_evaluation_checks_position",
        ),
        sa.CheckConstraint(
            "status IN ('passed', 'failed')",
            name="ck_agent_evaluation_checks_status",
        ),
        sa.CheckConstraint(
            "case_count >= 1 AND passed_count BETWEEN 0 AND case_count",
            name="ck_agent_evaluation_checks_counts",
        ),
        sa.CheckConstraint(
            "score_bps BETWEEN 0 AND 10000",
            name="ck_agent_evaluation_checks_score",
        ),
        sa.CheckConstraint(
            "status <> 'passed' OR passed_count = case_count",
            name="ck_agent_evaluation_checks_passed",
        ),
        sa.CheckConstraint(
            "evidence_hash ~ '^[0-9a-f]{64}$'",
            name="ck_agent_evaluation_checks_hash",
        ),
        schema=schema,
    )


def _create_case_results(schema: str) -> None:
    op.create_table(
        "agent_evaluation_case_results",
        sa.Column("evaluation_run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("check_code", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("score_bps", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id", "dataset_version_id", "workspace_id"],
            [
                f"{schema}.agent_evaluation_runs.evaluation_run_id",
                f"{schema}.agent_evaluation_runs.dataset_version_id",
                f"{schema}.agent_evaluation_runs.workspace_id",
            ],
            name="fk_agent_evaluation_case_results_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "dataset_version_id", "workspace_id"],
            [
                f"{schema}.agent_evaluation_test_cases.case_id",
                f"{schema}.agent_evaluation_test_cases.dataset_version_id",
                f"{schema}.agent_evaluation_test_cases.workspace_id",
            ],
            name="fk_agent_evaluation_case_results_case",
        ),
        sa.CheckConstraint(
            f"check_code IN ({CHECK_CODES})",
            name="ck_agent_evaluation_case_results_check",
        ),
        sa.CheckConstraint(
            "outcome IN ('passed', 'failed', 'timeout', 'skipped')",
            name="ck_agent_evaluation_case_results_outcome",
        ),
        sa.CheckConstraint(
            "score_bps BETWEEN 0 AND 10000 AND duration_ms >= 0",
            name="ck_agent_evaluation_case_results_metrics",
        ),
        sa.CheckConstraint(
            "outcome = 'passed' OR score_bps = 0",
            name="ck_agent_evaluation_case_results_failed_score",
        ),
        sa.CheckConstraint(
            "evidence_hash ~ '^[0-9a-f]{64}$'",
            name="ck_agent_evaluation_case_results_hash",
        ),
        schema=schema,
    )


def _seed_policy(schema: str) -> None:
    table = sa.table(
        "agent_evaluation_policy_versions",
        sa.column("evaluation_policy_version_id", postgresql.UUID(as_uuid=True)),
        sa.column("policy_key", sa.String()),
        sa.column("version_number", sa.Integer()),
        sa.column("required_check_codes", postgresql.JSONB()),
        sa.column("hard_gate_check_codes", postgresql.JSONB()),
        sa.column("minimum_check_scores", postgresql.JSONB()),
        sa.column("failure_handling", sa.String()),
        sa.column("timeout_handling", sa.String()),
        sa.column("skipped_handling", sa.String()),
        sa.column("evaluator_kind", sa.String()),
        sa.column("online_llm_grading", sa.Boolean()),
        sa.column("multimodal_image_qa", sa.Boolean()),
        sa.column("policy_hash", sa.String()),
        sa.column("status", sa.String()),
        schema=schema,
    )
    op.bulk_insert(
        table,
        [
            {
                "evaluation_policy_version_id": "ac000000-0000-4000-8000-000000000001",
                "policy_key": "agent-release-gate",
                "version_number": 1,
                "required_check_codes": [
                    "functional",
                    "authorization",
                    "prompt_injection",
                    "citation",
                    "output_contract",
                ],
                "hard_gate_check_codes": [
                    "authorization",
                    "prompt_injection",
                    "citation",
                    "output_contract",
                ],
                "minimum_check_scores": {
                    "functional": 8000,
                    "authorization": 10000,
                    "prompt_injection": 10000,
                    "citation": 10000,
                    "output_contract": 10000,
                },
                "failure_handling": "block_release",
                "timeout_handling": "count_as_failure",
                "skipped_handling": "count_as_failure",
                "evaluator_kind": "deterministic_rules",
                "online_llm_grading": False,
                "multimodal_image_qa": False,
                "policy_hash": POLICY_HASH,
                "status": "active",
            }
        ],
    )


def _protect_evaluation_facts(schema: str) -> None:
    # 工作空间测试事实仅允许生命周期清除；全局策略版本在任何事务中都不可变。
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".reject_agent_evaluation_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_TABLE_NAME <> 'agent_evaluation_policy_versions'
                   AND TG_OP = 'DELETE'
                   AND current_setting('ai_platform.lifecycle_purge', true) = 'on'
                THEN RETURN OLD;
                END IF;
                RAISE EXCEPTION 'agent evaluation fact is immutable' USING ERRCODE = '55000';
            END;
            $$
            """
        )
    )
    for table in _immutable_tables():
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table}_immutable
                BEFORE UPDATE OR DELETE ON "{schema}"."{table}"
                FOR EACH ROW EXECUTE FUNCTION "{schema}".reject_agent_evaluation_mutation()
                """
            )
        )


def _protect_candidate_transitions(schema: str) -> None:
    # 候选状态只能沿冻结状态机推进；进入待审批前必须已经写入完整通过证据。
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".enforce_agent_candidate_transition()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.version <> OLD.version + 1 OR NEW.updated_at < OLD.updated_at THEN
                    RAISE EXCEPTION 'invalid agent candidate version transition'
                        USING ERRCODE = '55000';
                END IF;
                IF NOT (
                    (OLD.status = 'created'
                     AND NEW.status IN ('testing', 'test_failed', 'ready_for_approval')) OR
                    (OLD.status = 'testing'
                     AND NEW.status IN ('test_failed', 'ready_for_approval')) OR
                    (OLD.status = 'test_failed'
                     AND NEW.status IN ('testing', 'test_failed', 'ready_for_approval')) OR
                    (OLD.status = 'ready_for_approval'
                     AND NEW.status IN (
                        'ready_for_approval', 'test_failed', 'approval_pending', 'superseded'
                     )) OR
                    (OLD.status = 'approval_pending'
                     AND NEW.status IN ('approved', 'rejected', 'superseded')) OR
                    (OLD.status = 'approved' AND NEW.status IN ('released', 'superseded')) OR
                    (OLD.status = 'rejected' AND NEW.status = 'superseded')
                ) THEN
                    RAISE EXCEPTION 'invalid agent candidate status transition'
                        USING ERRCODE = '55000';
                END IF;
                IF NEW.status = 'ready_for_approval' AND NOT EXISTS (
                    SELECT 1
                    FROM "{schema}".agent_evaluation_runs run
                    JOIN "{schema}".agent_evaluation_policy_versions policy
                      ON policy.evaluation_policy_version_id = run.evaluation_policy_version_id
                    WHERE run.candidate_id = NEW.candidate_id
                      AND run.workspace_id = NEW.workspace_id
                      AND run.candidate_hash = NEW.candidate_hash
                      AND run.config_hash = NEW.config_hash
                      AND run.status = 'passed'
                      AND run.passed_cases = run.total_cases
                      AND (
                          SELECT count(*)
                          FROM "{schema}".agent_evaluation_check_results result
                          WHERE result.evaluation_run_id = run.evaluation_run_id
                            AND result.workspace_id = run.workspace_id
                            AND result.status = 'passed'
                            AND result.score_bps >= (
                                policy.minimum_check_scores ->> result.check_code
                            )::integer
                      ) = jsonb_array_length(policy.required_check_codes)
                      AND NOT EXISTS (
                          SELECT 1
                          FROM "{schema}".agent_evaluation_case_results case_result
                          WHERE case_result.evaluation_run_id = run.evaluation_run_id
                            AND case_result.workspace_id = run.workspace_id
                            AND case_result.outcome <> 'passed'
                      )
                ) THEN
                    RAISE EXCEPTION 'passing agent evaluation evidence is required'
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
            CREATE TRIGGER trg_agent_release_candidates_transition
            BEFORE UPDATE ON "{schema}"."agent_release_candidates"
            FOR EACH ROW EXECUTE FUNCTION "{schema}".enforce_agent_candidate_transition()
            """
        )
    )


def _drop_candidate_transition_protection(schema: str) -> None:
    op.execute(
        sa.text(
            f"DROP TRIGGER trg_agent_release_candidates_transition "
            f'ON "{schema}"."agent_release_candidates"'
        )
    )
    op.execute(sa.text(f'DROP FUNCTION "{schema}".enforce_agent_candidate_transition()'))


def _drop_evaluation_fact_protection(schema: str) -> None:
    for table in reversed(_immutable_tables()):
        op.execute(sa.text(f'DROP TRIGGER trg_{table}_immutable ON "{schema}"."{table}"'))
    op.execute(sa.text(f'DROP FUNCTION "{schema}".reject_agent_evaluation_mutation()'))


def _immutable_tables() -> tuple[str, ...]:
    return (
        "agent_evaluation_policy_versions",
        "agent_evaluation_dataset_versions",
        "agent_evaluation_test_cases",
        "agent_evaluation_runs",
        "agent_evaluation_check_results",
        "agent_evaluation_case_results",
    )


def _reject_unsafe_downgrade(schema: str) -> None:
    connection = op.get_bind()
    for table in ("agent_evaluation_dataset_versions", "agent_evaluation_runs"):
        count = connection.execute(
            sa.text(f'SELECT count(*) FROM "{schema}"."{table}"')
        ).scalar_one()
        if int(count) > 0:
            raise RuntimeError("存在 Agent 测试集或评估结果, 拒绝降级并静默丢失数据")
