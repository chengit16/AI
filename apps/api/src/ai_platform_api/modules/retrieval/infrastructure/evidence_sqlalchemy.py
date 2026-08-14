"""实现当前来源复核、证据快照和受控精读的 PostgreSQL Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from types import TracebackType
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from ai_platform_api.modules.retrieval.domain.evidence import (
    EvidenceCandidateSource,
    EvidenceItemSnapshot,
    EvidenceProcessingBudget,
    EvidenceProcessingUnitOfWork,
    EvidenceRepository,
    EvidenceSetSnapshot,
)
from ai_platform_api.modules.retrieval.domain.models import AuthorizedSearchScope, SearchIndex
from ai_platform_api.modules.retrieval.domain.planning import (
    RetrievalCandidateSnapshot,
    RetrievalPlanningRepository,
)
from ai_platform_api.modules.retrieval.infrastructure.planning_sqlalchemy import (
    SqlAlchemyRetrievalPlanningRepository,
)
from ai_platform_api.modules.retrieval.infrastructure.sqlalchemy import SqlAlchemySearchIndex
from ai_platform_api.persistence.tables import (
    document_index_publications,
    document_publications,
    document_sources,
    document_versions,
    documents,
    index_versions,
    retrieval_evidence_items,
    retrieval_evidence_sets,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyEvidenceRepository(EvidenceRepository):
    """只读取当前活动来源，并在同一工作空间内追加不可变证据事实。"""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._search_index = SqlAlchemySearchIndex(session)

    def get_evidence_set(self, run_id: UUID) -> EvidenceSetSnapshot | None:
        # 1. 证据集按 Run 唯一读取，不存在时才允许应用服务进入首次处理路径。
        row = self._session.execute(
            select(retrieval_evidence_sets).where(retrieval_evidence_sets.c.run_id == run_id)
        ).one_or_none()
        if row is None:
            return None
        # 2. 证据项按稳定排名恢复，预算和处理指标从首次快照完整重建。
        items = tuple(
            _evidence_item(item)
            for item in self._session.execute(
                select(retrieval_evidence_items)
                .where(retrieval_evidence_items.c.evidence_set_id == row.evidence_set_id)
                .order_by(retrieval_evidence_items.c.rank)
            )
        )
        return EvidenceSetSnapshot(
            evidence_set_id=row.evidence_set_id,
            retrieval_plan_id=row.retrieval_plan_id,
            run_id=row.run_id,
            workspace_id=row.workspace_id,
            requested_by_account_id=row.requested_by_account_id,
            status=row.status,
            degradation_reason=row.degradation_reason,
            policy_decision_id=row.policy_decision_id,
            policy_version=row.policy_version,
            reranker_model_version=row.reranker_model_version,
            source_ranking_version=row.source_ranking_version,
            fastpass_used=row.fastpass_used,
            reranker_used=row.reranker_used,
            candidate_count=row.candidate_count,
            rejected_candidate_count=row.rejected_candidate_count,
            conflict_count=row.conflict_count,
            read_document_count=row.read_document_count,
            read_chunk_count=row.read_chunk_count,
            read_character_count=row.read_character_count,
            estimated_token_count=row.estimated_token_count,
            budget=EvidenceProcessingBudget(
                rerank_candidate_limit=row.rerank_candidate_limit,
                final_evidence_limit=row.final_evidence_limit,
                max_documents=row.max_documents,
                surrounding_chunks=row.surrounding_chunks,
                max_chunks=row.max_chunks,
                max_characters=row.max_characters,
                max_tokens=row.max_tokens,
                max_elapsed_ms=row.max_elapsed_ms,
                fastpass_score_ratio=row.fastpass_score_ratio,
                minimum_final_score=row.minimum_final_score,
                max_quote_characters=row.max_quote_characters,
            ),
            items=items,
            created_at=row.created_at,
            completed_at=row.completed_at,
            duration_ms=row.duration_ms,
        )

    def load_candidate_source(
        self,
        scope: AuthorizedSearchScope,
        candidate: RetrievalCandidateSnapshot,
    ) -> EvidenceCandidateSource | None:
        # 1. Chunk 必须仍命中当前授权范围，且所有版本标识和内容 Hash 与候选快照完全一致。
        chunk = self._search_index.get_chunk(scope, candidate.chunk_id)
        if chunk is None or (
            chunk.index_version_id,
            chunk.knowledge_base_id,
            chunk.document_id,
            chunk.document_version_id,
            chunk.content_hash,
        ) != (
            candidate.index_version_id,
            candidate.knowledge_base_id,
            candidate.document_id,
            candidate.document_version_id,
            candidate.content_hash,
        ):
            return None
        # 2. 文档发布指针和索引发布指针必须同时仍指向该版本；撤权、删除和替代版本均失败关闭。
        row = self._session.execute(
            select(
                documents.c.title,
                document_sources.c.source_kind,
                document_sources.c.source_name,
                document_sources.c.captured_at,
                document_versions.c.published_at,
            )
            .join(
                document_versions,
                and_(
                    document_versions.c.workspace_id == documents.c.workspace_id,
                    document_versions.c.document_id == documents.c.document_id,
                    document_versions.c.document_version_id == candidate.document_version_id,
                ),
            )
            .join(
                document_sources,
                and_(
                    document_sources.c.workspace_id == document_versions.c.workspace_id,
                    document_sources.c.document_version_id
                    == document_versions.c.document_version_id,
                ),
            )
            .join(
                document_publications,
                and_(
                    document_publications.c.workspace_id == documents.c.workspace_id,
                    document_publications.c.document_id == documents.c.document_id,
                    document_publications.c.current_document_version_id
                    == document_versions.c.document_version_id,
                ),
            )
            .join(
                document_index_publications,
                and_(
                    document_index_publications.c.workspace_id == documents.c.workspace_id,
                    document_index_publications.c.document_id == documents.c.document_id,
                    document_index_publications.c.document_version_id
                    == document_versions.c.document_version_id,
                    document_index_publications.c.index_version_id == candidate.index_version_id,
                ),
            )
            .join(
                index_versions,
                and_(
                    index_versions.c.workspace_id == documents.c.workspace_id,
                    index_versions.c.document_id == documents.c.document_id,
                    index_versions.c.document_version_id == document_versions.c.document_version_id,
                    index_versions.c.index_version_id == candidate.index_version_id,
                ),
            )
            .where(
                documents.c.workspace_id == scope.workspace_id,
                documents.c.document_id == candidate.document_id,
                documents.c.status == "active",
                document_versions.c.status == "published",
                index_versions.c.status == "active",
            )
        ).one_or_none()
        if row is None or row.published_at is None:
            return None
        # 3. 只把已通过双发布指针复核的来源映射回领域，字段遮罩仍在离开 Adapter 前应用。
        return EvidenceCandidateSource(
            candidate=candidate,
            chunk=chunk.apply_field_mask(scope.field_mask),
            document_title=row.title,
            source_kind=row.source_kind,
            source_name=row.source_name,
            captured_at=row.captured_at,
            published_at=row.published_at,
        )

    def evidence_set_is_current(
        self,
        scope: AuthorizedSearchScope,
        evidence_set: EvidenceSetSnapshot,
    ) -> bool:
        """复核已签发证据仍属于当前活动索引，防止重试泄漏已撤权正文。"""

        for item in evidence_set.items:
            candidate = RetrievalCandidateSnapshot(
                rank=item.rank,
                chunk_id=item.chunk_id,
                index_version_id=item.index_version_id,
                knowledge_base_id=item.knowledge_base_id,
                document_id=item.document_id,
                document_version_id=item.document_version_id,
                content_hash=item.content_hash,
                source_position=item.source_position,
                score=item.retrieval_score,
                query_hit_count=1,
                keyword_hit_count=0,
                vector_hit_count=0,
            )
            if self.load_candidate_source(scope, candidate) is None:
                return False
        return True

    def add_evidence_set(self, evidence_set: EvidenceSetSnapshot) -> None:
        # 1. 主记录冻结授权、组件版本、预算和降级结论，数据库触发器拒绝修改。
        budget = evidence_set.budget
        self._session.execute(
            retrieval_evidence_sets.insert().values(
                evidence_set_id=evidence_set.evidence_set_id,
                retrieval_plan_id=evidence_set.retrieval_plan_id,
                run_id=evidence_set.run_id,
                workspace_id=evidence_set.workspace_id,
                requested_by_account_id=evidence_set.requested_by_account_id,
                status=evidence_set.status,
                degradation_reason=evidence_set.degradation_reason,
                policy_decision_id=evidence_set.policy_decision_id,
                policy_version=evidence_set.policy_version,
                reranker_model_version=evidence_set.reranker_model_version,
                source_ranking_version=evidence_set.source_ranking_version,
                fastpass_used=evidence_set.fastpass_used,
                reranker_used=evidence_set.reranker_used,
                candidate_count=evidence_set.candidate_count,
                rejected_candidate_count=evidence_set.rejected_candidate_count,
                conflict_count=evidence_set.conflict_count,
                read_document_count=evidence_set.read_document_count,
                read_chunk_count=evidence_set.read_chunk_count,
                read_character_count=evidence_set.read_character_count,
                estimated_token_count=evidence_set.estimated_token_count,
                rerank_candidate_limit=budget.rerank_candidate_limit,
                final_evidence_limit=budget.final_evidence_limit,
                max_documents=budget.max_documents,
                surrounding_chunks=budget.surrounding_chunks,
                max_chunks=budget.max_chunks,
                max_characters=budget.max_characters,
                max_tokens=budget.max_tokens,
                max_elapsed_ms=budget.max_elapsed_ms,
                fastpass_score_ratio=budget.fastpass_score_ratio,
                minimum_final_score=budget.minimum_final_score,
                max_quote_characters=budget.max_quote_characters,
                created_at=evidence_set.created_at,
                completed_at=evidence_set.completed_at,
                duration_ms=evidence_set.duration_ms,
            )
        )
        # 2. 不确定且没有可用证据时只保存主结论，不创建没有正文语义的占位项。
        if not evidence_set.items:
            return
        # 3. 最终证据保存实际送入上下文的有限原文，引用可回溯到活动文档与索引版本。
        self._session.execute(
            retrieval_evidence_items.insert(),
            [
                {
                    "evidence_set_id": evidence_set.evidence_set_id,
                    "rank": item.rank,
                    "chunk_id": item.chunk_id,
                    "index_version_id": item.index_version_id,
                    "knowledge_base_id": item.knowledge_base_id,
                    "document_id": item.document_id,
                    "document_version_id": item.document_version_id,
                    "content_hash": item.content_hash,
                    "quote": item.quote,
                    "context_text": item.context_text,
                    "context_hash": item.context_hash,
                    "context_chunk_ids": list(item.context_chunk_ids),
                    "source_position": item.source_position,
                    "document_title": item.document_title,
                    "source_kind": item.source_kind,
                    "source_name": item.source_name,
                    "retrieval_score": item.retrieval_score,
                    "relevance_score": item.relevance_score,
                    "authority_score": item.authority_score,
                    "freshness_score": item.freshness_score,
                    "final_score": item.final_score,
                    "conflict_detected": item.conflict_detected,
                }
                for item in evidence_set.items
            ],
        )


class SqlAlchemyEvidenceProcessingUnitOfWork(EvidenceProcessingUnitOfWork):
    """让候选复核、精读、引用验证和证据写入共用单一 Session。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[
                Session,
                RetrievalPlanningRepository,
                EvidenceRepository,
                SearchIndex,
            ]
            | None
        ] = ContextVar("retrieval_evidence_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyEvidenceProcessingUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Evidence Processing Unit of Work 不允许重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyRetrievalPlanningRepository(session),
                SqlAlchemyEvidenceRepository(session),
                SqlAlchemySearchIndex(session),
            )
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        state = self._state.get()
        if state is not None:
            if exc_type is not None:
                state[0].rollback()
            state[0].close()
            self._state.set(None)

    @property
    def planning(self) -> RetrievalPlanningRepository:
        return self._require_state()[1]

    @property
    def evidence(self) -> EvidenceRepository:
        return self._require_state()[2]

    @property
    def search_index(self) -> SearchIndex:
        return self._require_state()[3]

    def commit(self) -> None:
        self._require_state()[0].commit()

    def _require_state(
        self,
    ) -> tuple[Session, RetrievalPlanningRepository, EvidenceRepository, SearchIndex]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Evidence Processing Unit of Work 尚未进入事务范围")
        return state


def _evidence_item(row: Row[Any]) -> EvidenceItemSnapshot:
    return EvidenceItemSnapshot(
        rank=row.rank,
        chunk_id=row.chunk_id,
        index_version_id=row.index_version_id,
        knowledge_base_id=row.knowledge_base_id,
        document_id=row.document_id,
        document_version_id=row.document_version_id,
        content_hash=row.content_hash,
        quote=row.quote,
        context_text=row.context_text,
        context_hash=row.context_hash,
        context_chunk_ids=tuple(row.context_chunk_ids),
        source_position=row.source_position,
        document_title=row.document_title,
        source_kind=row.source_kind,
        source_name=row.source_name,
        retrieval_score=row.retrieval_score,
        relevance_score=row.relevance_score,
        authority_score=row.authority_score,
        freshness_score=row.freshness_score,
        final_score=row.final_score,
        conflict_detected=row.conflict_detected,
    )
