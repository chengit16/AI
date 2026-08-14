"""编排 FastPass、重排、来源排序、受控精读和引用验证。"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import FieldPolicyRegistry
from ai_platform_api.modules.authorization.domain.policy import PolicyDecisionPoint
from ai_platform_api.modules.retrieval.application.authorization import (
    resolve_retrieval_authorization,
    retrieval_policy_request,
    same_retrieval_authorization,
    same_retrieval_requester,
)
from ai_platform_api.modules.retrieval.application.citations import CitationService
from ai_platform_api.modules.retrieval.application.reader import (
    AuthorizedDocumentReader,
    ReaderBudget,
)
from ai_platform_api.modules.retrieval.domain.errors import (
    RetrievalConfigurationError,
    RetrievalScopeDeniedError,
)
from ai_platform_api.modules.retrieval.domain.evidence import (
    EvidenceCandidateSource,
    EvidenceDegradationReason,
    EvidenceItemSnapshot,
    EvidenceProcessingBudget,
    EvidenceProcessingUnitOfWork,
    EvidenceSetSnapshot,
    RankedEvidenceCandidate,
)
from ai_platform_api.modules.retrieval.domain.models import (
    AuthorizedSearchScope,
    Reranker,
)
from ai_platform_api.modules.retrieval.domain.planning import RetrievalPlanSnapshot

SUPPORTED_SOURCE_RANKING = "source-priority-v1"
_CLAIM_PATTERN = re.compile(
    r"(?:\d+(?:\.\d+)?%?|[一二三四五六七八九十百千万]+(?:天|日|月|年|次|级|元))"
)
_NEGATION_PATTERN = re.compile(r"(?:不得|禁止|无需|不需要|不允许|不可|不能|未)")
_SOURCE_AUTHORITY = {
    "manual": 1.0,
    "upload": 0.95,
    "data_source": 0.9,
    "web": 0.8,
}


class RetrievalEvidenceService:
    """把不可变候选快照收敛为当前授权、可引用且受预算约束的证据。"""

    def __init__(
        self,
        unit_of_work: EvidenceProcessingUnitOfWork,
        policy: PolicyDecisionPoint,
        field_registry: FieldPolicyRegistry,
        reranker: Reranker,
        *,
        budget: EvidenceProcessingBudget | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy
        self._field_registry = field_registry
        self._reranker = reranker
        self._budget = budget or EvidenceProcessingBudget()

    def prepare(self, context: RequestContext, run_id: UUID) -> EvidenceSetSnapshot:
        """重新授权并精读候选；重复调用只返回首次提交的证据事实。"""

        # 1. 锁定冻结 Run、检索计划和当前权限，禁止调用方替换组件版本或沿用旧字段授权。
        with self._unit_of_work as unit_of_work:
            run = unit_of_work.planning.lock_run(run_id)
            if run is None or not same_retrieval_requester(context, run):
                raise RetrievalScopeDeniedError
            self._require_runtime_compatibility(
                run.reranker_model_version, run.source_ranking_version
            )
            plan = unit_of_work.planning.get_plan(run_id)
            if plan is None:
                raise RetrievalConfigurationError
            decision = self._policy.decide(retrieval_policy_request(context))
            authorization = resolve_retrieval_authorization(decision, self._field_registry)
            if not same_retrieval_authorization(plan, authorization):
                raise RetrievalScopeDeniedError
            existing = unit_of_work.evidence.get_evidence_set(run_id)
            if existing is not None:
                if existing.policy_version != authorization.policy_version:
                    raise RetrievalScopeDeniedError
                current_scope = unit_of_work.planning.resolve_search_scope(run, authorization)
                if existing.items and (
                    current_scope is None
                    or not unit_of_work.evidence.evidence_set_is_current(
                        current_scope,
                        existing,
                    )
                ):
                    raise RetrievalScopeDeniedError
                return existing

            # 2. 候选必须按当前活动索引和资源范围重新加载；撤权、旧版本或 Hash 漂移均被拒绝。
            started = monotonic()
            created_at = datetime.now(UTC)
            scope = unit_of_work.planning.resolve_search_scope(run, authorization)
            sources = self._load_current_sources(unit_of_work, plan, scope, started)
            ranked, fastpass_used, reranker_used = self._rank(plan, sources, created_at)

            # 3. 受控 Reader 按全局文档、Chunk、字符、Token 和耗时预算精读，再逐条签发原文引用。
            items, read_metrics = self._read_evidence(
                unit_of_work,
                plan,
                scope,
                ranked,
                started,
            )
            items = _mark_conflicts(items)
            degradation_reason = _degradation_reason(plan, items)
            completed_at = datetime.now(UTC)
            snapshot = EvidenceSetSnapshot(
                evidence_set_id=uuid4(),
                retrieval_plan_id=plan.retrieval_plan_id,
                run_id=run.run_id,
                workspace_id=run.workspace_id,
                requested_by_account_id=run.requested_by_account_id,
                status="sufficient" if degradation_reason is None else "uncertain",
                degradation_reason=degradation_reason,
                policy_decision_id=authorization.decision_id,
                policy_version=authorization.policy_version,
                reranker_model_version=self._reranker.model_version,
                source_ranking_version=SUPPORTED_SOURCE_RANKING,
                fastpass_used=fastpass_used,
                reranker_used=reranker_used,
                candidate_count=len(plan.candidates),
                rejected_candidate_count=max(0, len(plan.candidates) - len(items)),
                conflict_count=sum(item.conflict_detected for item in items),
                read_document_count=read_metrics[0],
                read_chunk_count=read_metrics[1],
                read_character_count=read_metrics[2],
                estimated_token_count=read_metrics[3],
                budget=self._budget,
                items=items,
                created_at=created_at,
                completed_at=completed_at,
                duration_ms=max(0, int((monotonic() - started) * 1000)),
            )
            unit_of_work.evidence.add_evidence_set(snapshot)
            unit_of_work.commit()
            return snapshot

    def _load_current_sources(
        self,
        unit_of_work: EvidenceProcessingUnitOfWork,
        plan: RetrievalPlanSnapshot,
        scope: AuthorizedSearchScope | None,
        started: float,
    ) -> tuple[EvidenceCandidateSource, ...]:
        if scope is None:
            return ()
        sources: list[EvidenceCandidateSource] = []
        for candidate in plan.candidates[: self._budget.rerank_candidate_limit]:
            _require_time_budget(started, self._budget)
            source = unit_of_work.evidence.load_candidate_source(scope, candidate)
            if source is not None:
                sources.append(source)
        return tuple(sources)

    def _rank(
        self,
        plan: RetrievalPlanSnapshot,
        sources: tuple[EvidenceCandidateSource, ...],
        now: datetime,
    ) -> tuple[tuple[RankedEvidenceCandidate, ...], bool, bool]:
        if not sources:
            return (), False, False
        fastpass = _fastpass_allowed(plan, sources, self._budget)
        maximum_retrieval_score = max(source.candidate.score for source in sources)
        normalized_scores = tuple(
            source.candidate.score / maximum_retrieval_score if maximum_retrieval_score > 0 else 0.0
            for source in sources
        )
        if fastpass:
            relevance_scores = normalized_scores
        else:
            relevance_scores = self._reranker.score(
                plan.variants[0].text,
                tuple(source.chunk.content for source in sources),
            )
            if len(relevance_scores) != len(sources) or any(
                not math.isfinite(score) or not 0 <= score <= 1 for score in relevance_scores
            ):
                raise RetrievalConfigurationError
        ranked = tuple(
            sorted(
                (
                    _ranked_candidate(source, relevance, now)
                    for source, relevance in zip(sources, relevance_scores, strict=True)
                ),
                key=lambda item: (
                    -item.final_score,
                    str(item.source.chunk.document_id),
                    str(item.source.chunk.chunk_id),
                ),
            )
        )
        return ranked, fastpass, not fastpass

    def _read_evidence(
        self,
        unit_of_work: EvidenceProcessingUnitOfWork,
        plan: RetrievalPlanSnapshot,
        scope: AuthorizedSearchScope | None,
        ranked: tuple[RankedEvidenceCandidate, ...],
        started: float,
    ) -> tuple[tuple[EvidenceItemSnapshot, ...], tuple[int, int, int, int]]:
        if scope is None:
            return (), (0, 0, 0, 0)
        # 1. Reader 和 CitationService 共用当前授权索引，所有候选共享一组全局读取预算。
        reader = AuthorizedDocumentReader(unit_of_work.search_index)
        citations = CitationService(unit_of_work.search_index)
        items: list[EvidenceItemSnapshot] = []
        read_documents: set[UUID] = set()
        consumed_chunks: set[UUID] = set()
        used_characters = 0
        used_tokens = 0
        # 2. 依照最终来源分数消耗文档、Chunk、字符和耗时预算，低置信候选不会触发正文读取。
        for candidate in ranked:
            _require_time_budget(started, self._budget)
            if candidate.final_score < self._budget.minimum_final_score:
                continue
            document_id = candidate.source.chunk.document_id
            if (
                document_id not in read_documents
                and len(read_documents) >= self._budget.max_documents
            ):
                continue
            remaining_chunks = self._budget.max_chunks - len(consumed_chunks)
            remaining_characters = self._budget.max_characters - used_characters
            if remaining_chunks < 1 or remaining_characters < 1:
                break
            chunks = reader.read(
                scope,
                candidate.source.chunk.document_version_id,
                candidate.source.chunk.sequence_no,
                ReaderBudget(
                    surrounding_chunks=self._budget.surrounding_chunks,
                    max_chunks=remaining_chunks,
                    max_characters=remaining_characters,
                ),
            )
            fresh_chunks = tuple(chunk for chunk in chunks if chunk.chunk_id not in consumed_chunks)
            center = next(
                (
                    chunk
                    for chunk in fresh_chunks
                    if chunk.chunk_id == candidate.source.chunk.chunk_id
                    and chunk.content_hash == candidate.source.chunk.content_hash
                ),
                None,
            )
            if center is None:
                continue
            context_text = "\n".join(chunk.content for chunk in fresh_chunks)
            estimated_tokens = _estimate_tokens(context_text)
            if used_tokens + estimated_tokens > self._budget.max_tokens:
                continue
            # 3. 中心原文再次签发引用后才进入证据快照，相邻上下文只作为受限生成上下文保存。
            quote = " ".join(center.content.split())[: self._budget.max_quote_characters]
            citation = citations.issue(scope, center.chunk_id, quote)
            if citation.content_hash != center.content_hash:
                continue
            items.append(
                EvidenceItemSnapshot(
                    rank=len(items) + 1,
                    chunk_id=center.chunk_id,
                    index_version_id=center.index_version_id,
                    knowledge_base_id=center.knowledge_base_id,
                    document_id=center.document_id,
                    document_version_id=center.document_version_id,
                    content_hash=center.content_hash,
                    quote=citation.quote,
                    context_text=context_text,
                    context_hash=_digest(context_text),
                    context_chunk_ids=tuple(chunk.chunk_id for chunk in fresh_chunks),
                    source_position=citation.source_position,
                    document_title=candidate.source.document_title,
                    source_kind=candidate.source.source_kind,
                    source_name=candidate.source.source_name,
                    retrieval_score=candidate.source.candidate.score,
                    relevance_score=candidate.relevance_score,
                    authority_score=candidate.authority_score,
                    freshness_score=candidate.freshness_score,
                    final_score=candidate.final_score,
                    conflict_detected=False,
                )
            )
            read_documents.add(document_id)
            consumed_chunks.update(chunk.chunk_id for chunk in fresh_chunks)
            used_characters += len(context_text)
            used_tokens += estimated_tokens
            if len(items) >= self._budget.final_evidence_limit:
                break
        return (
            tuple(items),
            (len(read_documents), len(consumed_chunks), used_characters, used_tokens),
        )

    def _require_runtime_compatibility(
        self,
        reranker_model_version: str,
        source_ranking_version: str,
    ) -> None:
        if (
            reranker_model_version != self._reranker.model_version
            or source_ranking_version != SUPPORTED_SOURCE_RANKING
        ):
            raise RetrievalConfigurationError


def _ranked_candidate(
    source: EvidenceCandidateSource,
    relevance_score: float,
    now: datetime,
) -> RankedEvidenceCandidate:
    authority_score = _SOURCE_AUTHORITY[source.source_kind]
    source_time = source.captured_at or source.published_at
    age_days = max(0.0, (now - source_time).total_seconds() / 86_400)
    freshness_score = 0.7 + 0.3 / (1 + age_days / 365)
    final_score = relevance_score * 0.75 + authority_score * 0.15 + freshness_score * 0.1
    return RankedEvidenceCandidate(
        source=source,
        relevance_score=relevance_score,
        authority_score=authority_score,
        freshness_score=freshness_score,
        final_score=final_score,
    )


def _fastpass_allowed(
    plan: RetrievalPlanSnapshot,
    sources: tuple[EvidenceCandidateSource, ...],
    budget: EvidenceProcessingBudget,
) -> bool:
    top = sources[0].candidate
    second_score = sources[1].candidate.score if len(sources) > 1 else 0.0
    has_margin = second_score == 0 or top.score >= second_score * budget.fastpass_score_ratio
    return (
        top.keyword_hit_count >= 1
        and top.vector_hit_count >= 1
        and top.query_hit_count == len(plan.variants)
        and has_margin
    )


def _mark_conflicts(
    items: tuple[EvidenceItemSnapshot, ...],
) -> tuple[EvidenceItemSnapshot, ...]:
    conflicting_indexes: set[int] = set()
    for left_index, left in enumerate(items):
        left_title = " ".join(left.document_title.casefold().split())
        left_claim = _claim_signature(left.quote)
        if not left_claim:
            continue
        for right_index in range(left_index + 1, len(items)):
            right = items[right_index]
            right_title = " ".join(right.document_title.casefold().split())
            right_claim = _claim_signature(right.quote)
            if (
                left.document_id != right.document_id
                and left_title == right_title
                and right_claim
                and left_claim != right_claim
            ):
                conflicting_indexes.update((left_index, right_index))
    return tuple(
        replace(item, conflict_detected=index in conflicting_indexes)
        for index, item in enumerate(items)
    )


def _claim_signature(text: str) -> tuple[tuple[str, ...], bool] | None:
    values = tuple(sorted(set(_CLAIM_PATTERN.findall(text))))
    return (values, bool(_NEGATION_PATTERN.search(text))) if values else None


def _degradation_reason(
    plan: RetrievalPlanSnapshot,
    items: tuple[EvidenceItemSnapshot, ...],
) -> EvidenceDegradationReason | None:
    if not plan.candidates:
        return "no_candidates"
    if not items:
        return "no_current_evidence"
    if any(item.conflict_detected for item in items):
        return "conflicting_evidence"
    if plan.classification == "comparison" and len({item.document_id for item in items}) < 2:
        return "insufficient_sources"
    return None


def _estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


def _require_time_budget(started: float, budget: EvidenceProcessingBudget) -> None:
    if (monotonic() - started) * 1000 > budget.max_elapsed_ms:
        raise RetrievalConfigurationError


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
