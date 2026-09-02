"""提供知识模块拥有的事务内文档版本发布能力。"""

from datetime import datetime
from uuid import UUID

from ai_platform_api.modules.knowledge.domain.models import (
    DocumentIndexNotReadyError,
    DocumentVersion,
    InvalidDocumentVersionTransitionError,
    KnowledgeRepository,
)


def publish_document_version_in_transaction(
    repository: KnowledgeRepository,
    *,
    workspace_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
    document_version_id: UUID,
    occurred_at: datetime,
    require_ready_index: bool = False,
) -> DocumentVersion:
    """锁定知识事实并原子切换版本、发布指针和索引发布指针。"""

    # 1. 锁定文档、目标版本和当前发布版本，确保状态转换基于同一数据库快照。
    document = repository.get_document(workspace_id, document_id, for_update=True)
    version = repository.get_document_version(
        workspace_id,
        document_id,
        document_version_id,
        for_update=True,
    )
    if (
        document is None
        or document.knowledge_base_id != knowledge_base_id
        or document.status != "active"
        or version is None
    ):
        raise InvalidDocumentVersionTransitionError
    # 2. 先计算版本终态，再由同一事务更新发布与索引指针。
    published = version.publish(occurred_at=occurred_at)
    current = repository.get_current_document_version(workspace_id, document_id, for_update=True)
    if current is not None:
        repository.save_document_version(current.supersede())
    repository.save_document_version(published)
    repository.set_current_document_version(
        workspace_id,
        document_id,
        document_version_id,
        published_at=occurred_at,
    )
    index_version_id = repository.switch_document_index(
        workspace_id,
        document_id,
        document_version_id,
        activated_at=occurred_at,
    )
    if require_ready_index and index_version_id is None:
        # 审批层会在数据库保存点中调用本函数；专用异常既保留稳定失败原因，
        # 也让保存点回滚本次版本、发布指针和索引指针的全部临时写入。
        raise DocumentIndexNotReadyError
    return published
