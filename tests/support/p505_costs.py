"""提供 P5-05 成本归因测试使用的固定受信采集器和七类用量。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_api.modules.quality.application.cost_policy import COST_COLLECTOR_VERSION
from ai_platform_api.modules.quality.domain.costs import (
    CostAttributionTarget,
    CostCollectionBatch,
    CostCollectionRequest,
    CostComponent,
    CostEvidenceKind,
    CostSourceKind,
    CostSupplierStatement,
    CostSupplierStatementStatus,
    CostUsageObservation,
    CostUsageUnit,
)
from ai_platform_api.persistence.tables import (
    agent_releases,
    agents,
    ai_runtime_config_versions,
    service_access_policy_versions,
    services,
)
from sqlalchemy import insert
from sqlalchemy.orm import Session, sessionmaker

SOURCE_IDS = {
    "model": UUID("55000000-0000-4000-8000-000000000561"),
    "retrieval": UUID("55000000-0000-4000-8000-000000000562"),
    "ocr": UUID("55000000-0000-4000-8000-000000000563"),
    "indexing": UUID("55000000-0000-4000-8000-000000000564"),
    "embedding": UUID("55000000-0000-4000-8000-000000000565"),
    "reranker": UUID("55000000-0000-4000-8000-000000000566"),
    "tool": UUID("55000000-0000-4000-8000-000000000567"),
}
SOURCE_KINDS: dict[CostComponent, CostSourceKind] = {
    "model": "model_attempt",
    "retrieval": "retrieval_run",
    "ocr": "ocr_attempt",
    "indexing": "index_build",
    "embedding": "embedding_batch",
    "reranker": "reranker_request",
    "tool": "tool_attempt",
}
UNITS: dict[CostComponent, CostUsageUnit] = {
    "model": "token",
    "retrieval": "request",
    "ocr": "page",
    "indexing": "chunk",
    "embedding": "token",
    "reranker": "pair",
    "tool": "request",
}


class StaticCostUsageCollector:
    """返回固定成本事实，并允许测试显式制造身份或结果漂移。"""

    def __init__(
        self,
        *,
        evidence_kind: CostEvidenceKind = "synthetic",
        observations: tuple[CostUsageObservation, ...] = (),
        supplier_statement: CostSupplierStatement | None = None,
    ) -> None:
        self.version = COST_COLLECTOR_VERSION
        self.evidence_kind = evidence_kind
        self.observations = observations
        self.supplier_statement = supplier_statement or empty_supplier_statement()
        self.batch_target: CostAttributionTarget | None = None

    @property
    def collector_version(self) -> str:
        return self.version

    def collect(self, request: CostCollectionRequest) -> CostCollectionBatch:
        return CostCollectionBatch(
            request.workspace_id,
            self.batch_target or request.target,
            self.evidence_kind,
            self.observations,
            self.supplier_statement,
        )


def empty_supplier_statement(
    status: CostSupplierStatementStatus = "not_configured",
) -> CostSupplierStatement:
    """构造不含账单正文和金额的未配置或未执行状态。"""

    return CostSupplierStatement(status, None, None, None, None, (), {})


def synthetic_observations(
    *,
    evidence_marker: str = "synthetic-p505-cost-evidence",
) -> tuple[CostUsageObservation, ...]:
    """生成覆盖七组件、一次失败重试和模型双计量项的全合成事实。"""

    observations = tuple(
        CostUsageObservation(
            component,
            SOURCE_KINDS[component],
            SOURCE_IDS[component],
            f"{component}.usage",
            1,
            "succeeded",
            False,
            1_000 if component in {"model", "embedding"} else 10,
            UNITS[component],
            1_000 if component in {"model", "embedding"} else 10,
            10,
            "synthetic-price-v1",
            "CNY",
            "synthetic_rate",
            None,
            {"marker": f"{evidence_marker}-{component}"},
        )
        for component in SOURCE_KINDS
    )
    model_output = replace(
        observations[0],
        meter_key="model.output_tokens",
        quantity=200,
        unit_price_minor=20,
    )
    failed_retry = replace(
        observations[0],
        source_record_id=UUID("55000000-0000-4000-8000-000000000568"),
        meter_key="model.retry_input_tokens",
        attempt_no=2,
        outcome="failed",
        is_retry=True,
        quantity=100,
    )
    return (*observations, model_output, failed_retry)


def as_authorized_real(
    observations: tuple[CostUsageObservation, ...],
) -> tuple[CostUsageObservation, ...]:
    """把合成结构转换为已审核价格输入，仍只供机制测试使用。"""

    return tuple(
        replace(
            item,
            price_version="reviewed-price-v1",
            estimate_source="contract_rate",
        )
        for item in observations
    )


def seed_cost_release(
    sessions: sessionmaker[Session],
    *,
    workspace_id: UUID,
    account_id: UUID,
) -> tuple[UUID, UUID, UUID, str]:
    """建立成本测试复用的全合成 Service、Release 和运行配置身份。"""

    runtime_id, agent_id, release_id = uuid4(), uuid4(), uuid4()
    service_id, access_policy_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    run_digest = uuid4().hex * 2
    with sessions.begin() as session:
        session.execute(
            insert(ai_runtime_config_versions).values(
                runtime_config_version_id=runtime_id,
                version_number=runtime_id.int % 2_000_000_000 + 1,
                display_name="合成 P5-05 运行配置",
                content_hash=run_digest,
                system_prompt_template="仅使用全合成成本事实。",
                system_prompt_hash="e" * 64,
                component_versions={"cost": "synthetic-p505"},
                attempt_timeout_ms=500,
                total_timeout_ms=2_000,
                max_attempts_per_route=1,
                max_prompt_characters=4_000,
                max_output_tokens=256,
                max_response_characters=8_000,
                circuit_failure_threshold=3,
                circuit_recovery_ms=30_000,
                rule_degradation_message=None,
                max_estimated_cost_microunits=5_000_000,
                created_by_account_id=account_id,
                created_at=now,
            )
        )
        session.execute(
            insert(agents).values(
                agent_id=agent_id,
                workspace_id=workspace_id,
                agent_key=f"synthetic-p505-{agent_id.hex[:12]}",
                agent_kind="system",
                name="合成 P5-05 Agent",
                description=None,
                status="active",
                created_by_account_id=account_id,
                created_at=now,
                updated_at=now,
                version=1,
            )
        )
        session.execute(
            insert(agent_releases).values(
                release_id=release_id,
                agent_id=agent_id,
                workspace_id=workspace_id,
                release_kind="system",
                version=1,
                status="released",
                runtime_config_version_id=runtime_id,
                config_hash="f" * 64,
                candidate_id=None,
                candidate_hash=None,
                source_draft_id=None,
                source_draft_revision=None,
                evaluation_run_id=None,
                approval_binding_id=None,
                snapshot_hash=None,
                released_by_account_id=account_id,
                released_at=now,
            )
        )
        # Service 与策略存在延迟外键循环，测试数据也必须遵守正式事务顺序。
        session.execute(
            insert(service_access_policy_versions).values(
                access_policy_version_id=access_policy_id,
                service_id=service_id,
                workspace_id=workspace_id,
                version=1,
                visibility="workspace",
                allowed_department_ids=[],
                allowed_account_ids=[],
                policy_hash="1" * 64,
                created_by_account_id=account_id,
                created_at=now,
            )
        )
        session.execute(
            insert(services).values(
                service_id=service_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                service_key=f"synthetic-p505-{service_id.hex[:12]}",
                name="合成 P5-05 服务",
                service_type="system_assistant",
                status="draft",
                access_policy_version_id=access_policy_id,
                created_by_account_id=account_id,
                created_at=now,
                updated_by_account_id=account_id,
                updated_at=now,
                version=1,
            )
        )
    return service_id, release_id, runtime_id, run_digest
