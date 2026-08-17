"""提供 P5-07 隔离合规状态和服务装配测试支持。"""

from __future__ import annotations

from ai_platform_api.modules.isolation.application.service import IsolationGovernanceService
from ai_platform_api.modules.isolation.domain.models import (
    ComplianceStatus,
    IsolationComplianceAssessment,
    IsolationLevel,
    WorkspaceIsolationEntitlement,
)
from ai_platform_api.modules.isolation.infrastructure.sqlalchemy import (
    SqlAlchemyIsolationUnitOfWork,
)
from sqlalchemy.orm import Session, sessionmaker


class StaticIsolationComplianceSource:
    """按等级返回固定合规状态，不接收浏览器提供的审核结论。"""

    def __init__(self) -> None:
        self.statuses: dict[IsolationLevel, ComplianceStatus] = {
            "L1": "not_required",
            "L2": "not_required",
            "L3": "not_configured",
            "L4": "not_configured",
        }

    def assess(
        self,
        entitlement: WorkspaceIsolationEntitlement,
        target_level: IsolationLevel,
    ) -> IsolationComplianceAssessment:
        status = self.statuses[target_level]
        digest = "c" * 64 if status in {"approved", "rejected"} else None
        return IsolationComplianceAssessment(status, digest)


def isolation_service(
    sessions: sessionmaker[Session],
    compliance: StaticIsolationComplianceSource,
) -> IsolationGovernanceService:
    """使用隔离 Schema 的真实 Unit of Work 装配治理服务。"""

    return IsolationGovernanceService(
        SqlAlchemyIsolationUnitOfWork(sessions),
        compliance,
    )
