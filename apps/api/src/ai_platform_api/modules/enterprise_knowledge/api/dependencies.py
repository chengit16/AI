"""解析企业知识治理应用服务与可信请求上下文。"""

from fastapi import Request

from ai_platform_api.modules.enterprise_knowledge.application.service import (
    EnterpriseKnowledgeService,
)
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context


def enterprise_knowledge_service(request: Request) -> EnterpriseKnowledgeService:
    """从应用容器解析企业知识治理服务，避免路由自行装配基础设施。"""

    service = getattr(request.app.state, "enterprise_knowledge_service", None)
    if not isinstance(service, EnterpriseKnowledgeService):
        raise RuntimeError("企业知识治理服务尚未完成装配")
    return service


__all__ = ["enterprise_knowledge_service", "trusted_request_context"]
