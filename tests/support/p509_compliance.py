"""提供 P5-09 法规策略与法律保留测试使用的受信合成配置。"""

from __future__ import annotations

from uuid import UUID

from ai_platform_api.modules.lifecycle.application.compliance import RegulatoryComplianceService
from ai_platform_api.modules.lifecycle.domain.compliance import RegulatoryPolicyConfiguration
from ai_platform_api.modules.lifecycle.infrastructure.compliance import (
    SqlAlchemyLifecycleComplianceUnitOfWork,
)
from sqlalchemy.orm import Session, sessionmaker


class StaticRegulatoryPolicySource:
    """返回显式标记的合成法域配置，不模拟真实外部法规审核。"""

    def __init__(self, *, configured: bool = True) -> None:
        self.configured = configured

    def resolve(self, workspace_id: UUID) -> RegulatoryPolicyConfiguration:
        del workspace_id
        if not self.configured:
            return RegulatoryPolicyConfiguration(
                jurisdiction_status="not_configured",
                jurisdiction_codes=(),
                retention_period_days={},
                external_review_status="not_configured",
                external_review_digest=None,
            )
        return RegulatoryPolicyConfiguration(
            jurisdiction_status="configured",
            jurisdiction_codes=("SYNTHETIC-P5-09",),
            retention_period_days={
                "stream_events": 1,
                "published_outbox": 30,
                "attempts_and_dead_letters": 90,
                "minimum_records": 365,
            },
            external_review_status="not_configured",
            external_review_digest=None,
        )


def regulatory_compliance_service(
    sessions: sessionmaker[Session],
    source: StaticRegulatoryPolicySource | None = None,
) -> RegulatoryComplianceService:
    """使用隔离 Schema 的真实事务边界装配法规治理服务。"""

    return RegulatoryComplianceService(
        SqlAlchemyLifecycleComplianceUnitOfWork(sessions),
        source or StaticRegulatoryPolicySource(),
    )
