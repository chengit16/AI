"""在当前授权边界内读取助手消息绑定的持久化引用来源。"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.assistant.application.errors import AssistantConversationBusyError
from ai_platform_api.modules.assistant.domain.models import AssistantRun
from ai_platform_api.modules.retrieval.application.evidence import RetrievalEvidenceService
from ai_platform_api.modules.retrieval.domain.evidence import SourceKind


@dataclass(frozen=True)
class AssistantSource:
    """表示重新授权后可向当前账号展示的一条引用来源。"""

    rank: int
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    content_hash: str
    quote: str
    source_position: Mapping[str, object]
    document_title: str
    source_kind: SourceKind
    source_name: str
    conflict_detected: bool


class AssistantMessageRunReader(Protocol):
    """抽象普通助手与企业大脑共用的消息归属校验入口。"""

    def get_run_for_message(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        message_id: UUID,
    ) -> AssistantRun: ...


class CurrentEvidenceCitationCounter(Protocol):
    """为报告生成提供当前仍获授权的不可变引用数量。"""

    def count_current_citations(self, context: RequestContext, *, run_id: UUID) -> int:
        """重新鉴权既有证据后返回当前可读取的引用数量。"""
        ...


class RetrievalCurrentEvidenceCitationCounter:
    """通过 Retrieval 公开应用服务读取报告所需的当前引用事实。"""

    def __init__(self, evidence: RetrievalEvidenceService) -> None:
        self._evidence = evidence

    def count_current_citations(self, context: RequestContext, *, run_id: UUID) -> int:
        """复核策略、范围和活动索引，禁止从模型正文猜测引用数量。"""

        return len(self._evidence.prepare(context, run_id).items)


class AssistantSourceService:
    """把私有消息归属验证和 Retrieval 当前性复核封装为单一来源查询。"""

    def __init__(
        self,
        conversations: AssistantMessageRunReader,
        evidence: RetrievalEvidenceService,
    ) -> None:
        self._conversations = conversations
        self._evidence = evidence

    def list_sources(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        message_id: UUID,
    ) -> tuple[AssistantSource, ...]:
        """重新执行当前权限与版本检查后返回来源，撤权或漂移时失败关闭。"""

        run = self._conversations.get_run_for_message(
            context,
            conversation_id=conversation_id,
            message_id=message_id,
        )
        if run.status != "completed":
            raise AssistantConversationBusyError
        # `prepare` 对既有证据不会重新生成，只会复核请求者、策略版本、范围和当前索引。
        evidence = self._evidence.prepare(context, run.run_id)
        return tuple(
            AssistantSource(
                rank=item.rank,
                document_id=item.document_id,
                document_version_id=item.document_version_id,
                chunk_id=item.chunk_id,
                content_hash=item.content_hash,
                quote=item.quote,
                source_position=item.source_position,
                document_title=item.document_title,
                source_kind=item.source_kind,
                source_name=item.source_name,
                conflict_detected=item.conflict_detected,
            )
            for item in evidence.items
        )
