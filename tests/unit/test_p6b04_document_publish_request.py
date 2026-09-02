"""验证 P6B-04 企业文档发布请求的不可变摘要与终态约束。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from ai_platform_api.modules.enterprise_knowledge.domain.models import (
    DocumentPublishRequest,
    InvalidEnterpriseKnowledgeError,
)


def _request() -> DocumentPublishRequest:
    """构造只含合成标识和摘要的待审批发布请求。"""

    now = datetime.now(UTC)
    return DocumentPublishRequest(
        publish_request_id=uuid4(),
        workspace_id=uuid4(),
        document_id=uuid4(),
        document_version_id=uuid4(),
        knowledge_base_id=uuid4(),
        requester_account_id=uuid4(),
        approval_instance_id=uuid4(),
        category_ids=(uuid4(),),
        version_number=2,
        content_hash="a" * 64,
        governance_digest="b" * 64,
        idempotency_key="synthetic-p6b04-request",
        status="pending",
        failure_reason_code=None,
        created_at=now,
        updated_at=now,
        completed_at=None,
        version=1,
    )


def test_publish_request_allows_one_terminal_transition() -> None:
    """批准发布只递增一次版本，终态申请不能被再次转换。"""

    request = _request()
    request.assert_valid()
    completed = request.finish(
        status="published", occurred_at=request.created_at + timedelta(minutes=2)
    )

    assert completed.status == "published"
    assert completed.completed_at is not None
    assert completed.version == 2
    with pytest.raises(InvalidEnterpriseKnowledgeError):
        completed.finish(status="withdrawn", occurred_at=datetime.now(UTC))


def test_publish_failed_requires_stable_reason() -> None:
    """批准后复核失败必须保留稳定原因，其他终态不得伪造失败原因。"""

    request = _request()
    with pytest.raises(InvalidEnterpriseKnowledgeError):
        request.finish(status="publish_failed", occurred_at=datetime.now(UTC))
    failed = request.finish(
        status="publish_failed",
        occurred_at=datetime.now(UTC),
        failure_reason_code="permission_revoked",
    )
    assert failed.failure_reason_code == "permission_revoked"
    with pytest.raises(InvalidEnterpriseKnowledgeError):
        request.finish(
            status="rejected",
            occurred_at=datetime.now(UTC),
            failure_reason_code="permission_revoked",
        )


def test_publish_request_rejects_untraceable_snapshot() -> None:
    """缺失分类范围、非法摘要或不稳定幂等键均不得进入审批链。"""

    request = _request()
    invalid_requests = (
        replace(request, content_hash="unsafe"),
        replace(request, governance_digest="z" * 64),
        replace(request, idempotency_key="contains spaces"),
        replace(request, category_ids=()),
    )
    for invalid_request in invalid_requests:
        with pytest.raises(InvalidEnterpriseKnowledgeError):
            invalid_request.assert_valid()
