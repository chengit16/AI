"""实现权限前过滤、确定性查询改写和有界多查询混合检索。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID, uuid4

from ai_platform_backend.observability import observed_operation
from ai_platform_backend.safety import RagSafetyGate

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import FieldPolicyRegistry
from ai_platform_api.modules.authorization.domain.policy import PolicyDecisionPoint
from ai_platform_api.modules.retrieval.application.authorization import (
    resolve_retrieval_authorization,
    retrieval_policy_request,
    same_retrieval_authorization,
    same_retrieval_requester,
)
from ai_platform_api.modules.retrieval.application.search import reciprocal_rank_fusion
from ai_platform_api.modules.retrieval.application.tokenization import (
    TOKENIZER_VERSION,
    keyword_query,
)
from ai_platform_api.modules.retrieval.domain.errors import (
    RetrievalConfigurationError,
    RetrievalScopeDeniedError,
)
from ai_platform_api.modules.retrieval.domain.models import (
    AuthorizedSearchScope,
    EmbeddingProvider,
    StoredChunk,
)
from ai_platform_api.modules.retrieval.domain.planning import (
    QueryClassification,
    QueryVariant,
    RetrievalCandidateSnapshot,
    RetrievalPlannerBudget,
    RetrievalPlanningUnitOfWork,
    RetrievalPlanSnapshot,
    RetrievalRunInput,
)

SUPPORTED_RETRIEVAL_STRATEGY = "hybrid-rrf-v1"
_COMPARISON_PATTERN = re.compile(
    r"(?P<left>.{1,80}?)(?:与|和|及|以及)(?P<right>.{1,80}?)(?:的)?(?:区别|差异|对比|比较)"
)
_EXACT_PATTERN = re.compile(r"(?:[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,36}|[A-Z]{2,12}[-_][0-9]{2,})")


@dataclass
class _CandidateAccumulator:
    """在单次请求内聚合多变体和双通道命中，不跨请求保存正文。"""

    chunk: StoredChunk
    score: float = 0.0
    query_hits: set[int] | None = None
    keyword_hits: int = 0
    vector_hits: int = 0

    def __post_init__(self) -> None:
        if self.query_hits is None:
            self.query_hits = set()


class DeterministicQueryRewriter:
    """不调用模型地分类并生成有限聚焦变体，保证本地闭环可复现。"""

    def rewrite(
        self,
        query: str,
        budget: RetrievalPlannerBudget,
    ) -> tuple[QueryClassification, tuple[QueryVariant, ...]]:
        """保留规范化原问题，并按问题类型最多补充两个聚焦变体。"""

        normalized = _normalize_query(query)
        if not normalized or len(normalized) > budget.max_query_characters:
            raise RetrievalConfigurationError
        classification = _classify(normalized)
        candidates = [normalized]
        if classification == "comparison":
            candidates.extend(_comparison_variants(normalized))
        elif classification in {"summary", "knowledge"}:
            focused = _focused_query(normalized, classification)
            if focused:
                candidates.append(focused)

        variants: list[QueryVariant] = []
        seen_candidates: set[str] = set()
        for candidate in candidates:
            if candidate in seen_candidates:
                continue
            seen_candidates.add(candidate)
            sequence_no = len(variants) + 1
            variants.append(
                QueryVariant(
                    sequence_no=sequence_no,
                    kind="original" if sequence_no == 1 else "focused",
                    text=candidate,
                    query_hash=_digest(candidate),
                )
            )
            if len(variants) == budget.max_query_variants:
                break
        return classification, tuple(variants)


class BoundedRetrievalPlanningService:
    """为排队 Run 生成一次不可变、可审计且权限收敛的检索候选快照。"""

    def __init__(
        self,
        unit_of_work: RetrievalPlanningUnitOfWork,
        policy: PolicyDecisionPoint,
        field_registry: FieldPolicyRegistry,
        embedding_provider: EmbeddingProvider,
        *,
        budget: RetrievalPlannerBudget | None = None,
        query_rewriter: DeterministicQueryRewriter | None = None,
        safety_gate: RagSafetyGate | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy
        self._field_registry = field_registry
        self._embedding_provider = embedding_provider
        self._budget = budget or RetrievalPlannerBudget()
        self._query_rewriter = query_rewriter or DeterministicQueryRewriter()
        self._safety_gate = safety_gate or RagSafetyGate()

    @observed_operation(component="retrieval", operation="plan")
    def retrieve(self, context: RequestContext, run_id: UUID) -> RetrievalPlanSnapshot:
        """在冻结 Run 与当前授权同时有效时执行检索；重复调用返回同一快照。"""

        # 1. 锁定 Run 并从服务端事实恢复用户、空间和组件版本，不接受调用方覆盖检索配置。
        with self._unit_of_work as unit_of_work:
            run = unit_of_work.planning.lock_run(run_id)
            if run is None or not same_retrieval_requester(context, run):
                raise RetrievalScopeDeniedError
            _require_runtime_compatibility(run, self._embedding_provider)
            if not self._safety_gate.inspect_user_query(run.query).allowed:
                raise RetrievalScopeDeniedError
            decision = self._policy.decide(retrieval_policy_request(context))
            authorization = resolve_retrieval_authorization(decision, self._field_registry)
            existing = unit_of_work.planning.get_plan(run_id)
            if existing is not None:
                if not same_retrieval_authorization(existing, authorization):
                    raise RetrievalScopeDeniedError
                return existing

            # 2. 先生成有界变体和授权索引范围；无可见索引时记录空结果，不扩大搜索条件。
            started = monotonic()
            created_at = datetime.now(UTC)
            classification, variants = self._query_rewriter.rewrite(run.query, self._budget)
            scope = unit_of_work.planning.resolve_search_scope(run, authorization)
            rewrite_decision = self._safety_gate.validate_rewrite_scope(
                variants[0].text,
                tuple(variant.text for variant in variants[1:]),
                None
                if scope is None or scope.document_ids is None
                else frozenset(str(document_id) for document_id in scope.document_ids),
            )
            if not rewrite_decision.allowed:
                raise RetrievalScopeDeniedError
            candidates, operations, keyword_count, vector_count = self._search(
                unit_of_work,
                variants,
                scope,
                started,
            )

            # 3. 计划、查询变体和候选引用一次提交；快照不复制 Chunk 正文，后续精读仍需重新授权。
            completed_at = datetime.now(UTC)
            plan = RetrievalPlanSnapshot(
                retrieval_plan_id=uuid4(),
                run_id=run.run_id,
                workspace_id=run.workspace_id,
                requested_by_account_id=run.requested_by_account_id,
                original_query_hash=_digest(variants[0].text),
                classification=classification,
                strategy_version=run.retrieval_strategy_version,
                policy_decision_id=authorization.decision_id,
                policy_version=authorization.policy_version,
                maximum_security_level=authorization.maximum_security_level,
                field_mask=authorization.field_mask,
                embedding_model_version=run.embedding_model_version,
                tokenizer_version=run.tokenizer_version,
                budget=self._budget,
                variants=variants,
                candidates=candidates,
                search_operation_count=operations,
                keyword_candidate_count=keyword_count,
                vector_candidate_count=vector_count,
                created_at=created_at,
                completed_at=completed_at,
                duration_ms=max(0, int((monotonic() - started) * 1000)),
            )
            unit_of_work.planning.add_plan(plan)
            unit_of_work.commit()
            return plan

    def _search(
        self,
        unit_of_work: RetrievalPlanningUnitOfWork,
        variants: tuple[QueryVariant, ...],
        scope: AuthorizedSearchScope | None,
        started: float,
    ) -> tuple[tuple[RetrievalCandidateSnapshot, ...], int, int, int]:
        # 1. 先确认授权索引范围和固定维度向量，缺少范围时只记录空计划。
        if scope is None:
            return (), 0, 0, 0
        embeddings = self._embedding_provider.embed(tuple(item.text for item in variants))
        if len(embeddings) != len(variants) or any(
            len(vector) != self._embedding_provider.dimension for vector in embeddings
        ):
            raise RetrievalConfigurationError

        accumulators: dict[tuple[UUID, UUID], _CandidateAccumulator] = {}
        operation_count = 0
        keyword_count = 0
        vector_count = 0
        # 2. 每个有限变体固定执行关键词与向量两个通道，并在融合前再次做 Chunk 范围过滤。
        for variant, embedding in zip(variants, embeddings, strict=True):
            # 每个变体固定消耗两个搜索操作；超时或预算不足时失败关闭，不返回部分宽松结果。
            operation_count = _reserve_operations(
                operation_count,
                2,
                self._budget,
                started,
            )
            keyword_candidates = unit_of_work.search_index.keyword_search(
                scope,
                keyword_query(variant.text),
                self._budget.per_channel_candidates,
            )
            vector_candidates = unit_of_work.search_index.vector_search(
                scope,
                embedding,
                self._budget.per_channel_candidates,
            )
            if (
                len(keyword_candidates) > self._budget.per_channel_candidates
                or len(vector_candidates) > self._budget.per_channel_candidates
            ):
                raise RetrievalConfigurationError
            keyword_count += len(keyword_candidates)
            vector_count += len(vector_candidates)
            fused = reciprocal_rank_fusion(
                tuple(item for item in keyword_candidates if _chunk_allowed(item.chunk, scope)),
                tuple(item for item in vector_candidates if _chunk_allowed(item.chunk, scope)),
                self._budget.rrf_constant,
            )
            weight = 1.0 if variant.kind == "original" else 0.8
            for candidate in fused:
                key = (candidate.chunk.index_version_id, candidate.chunk.chunk_id)
                item = accumulators.setdefault(key, _CandidateAccumulator(candidate.chunk))
                item.score += candidate.hybrid_score * weight
                assert item.query_hits is not None
                item.query_hits.add(variant.sequence_no)
                item.keyword_hits += int(candidate.keyword_rank is not None)
                item.vector_hits += int(candidate.vector_rank is not None)
        # 3. 结束前统一检查时间预算，再生成不含正文的稳定候选快照。
        _require_time_budget(started, self._budget)
        return (
            _candidate_snapshots(accumulators, scope, self._budget.final_candidate_limit),
            operation_count,
            keyword_count,
            vector_count,
        )


def _require_runtime_compatibility(
    run: RetrievalRunInput,
    embedding_provider: EmbeddingProvider,
) -> None:
    if (
        run.retrieval_strategy_version != SUPPORTED_RETRIEVAL_STRATEGY
        or run.embedding_model_version != embedding_provider.model_version
        or run.tokenizer_version != TOKENIZER_VERSION
        or embedding_provider.dimension != 1024
    ):
        raise RetrievalConfigurationError


def _normalize_query(query: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", query).split())


def _classify(query: str) -> QueryClassification:
    if _EXACT_PATTERN.search(query) or any(mark in query for mark in ('"', "“", "”", "《", "》")):
        return "exact_lookup"
    if any(keyword in query for keyword in ("总结", "概括", "摘要", "归纳")):
        return "summary"
    if any(keyword in query for keyword in ("比较", "对比", "区别", "差异")):
        return "comparison"
    return "knowledge"


def _comparison_variants(query: str) -> tuple[str, ...]:
    focused = _focused_query(query, "comparison")
    match = _COMPARISON_PATTERN.search(focused)
    if match is None:
        return (focused,) if focused != query else ()
    return tuple(
        value
        for value in (
            _trim_query(match.group("left")),
            _trim_query(match.group("right")),
        )
        if len(value) >= 2
    )


def _focused_query(query: str, classification: QueryClassification) -> str:
    value = re.sub(r"^(?:请问|请|帮我|请帮我|麻烦)(?:查询|说明|介绍|看看)?", "", query)
    if classification == "summary":
        value = re.sub(r"^(?:总结|概括|归纳)(?:一下|下)?", "", value)
    elif classification == "comparison":
        value = re.sub(r"^(?:比较|对比)(?:一下|下)?", "", value)
    else:
        value = re.sub(r"^(?:查询|说明|介绍)", "", value)
    return _trim_query(value)


def _trim_query(value: str) -> str:
    return value.strip(" \uff0c,\u3002.!\uff01?\uff1f:\uff1a;\uff1b")


def _chunk_allowed(chunk: StoredChunk, scope: AuthorizedSearchScope) -> bool:
    if (
        chunk.workspace_id != scope.workspace_id
        or chunk.index_version_id not in scope.index_version_ids
        or chunk.security_level not in scope.security_levels
    ):
        return False
    if (
        scope.knowledge_base_ids is not None
        and chunk.knowledge_base_id not in scope.knowledge_base_ids
    ):
        return False
    if scope.document_ids is not None and chunk.document_id not in scope.document_ids:
        return False
    if chunk.visibility == "private":
        return "private" in scope.visibilities and chunk.document_id in scope.private_document_ids
    if chunk.visibility == "departments":
        return "departments" in scope.visibilities and (
            scope.allow_all_departments or bool(set(chunk.department_ids) & scope.department_ids)
        )
    return chunk.visibility in scope.visibilities


def _reserve_operations(
    current: int,
    requested: int,
    budget: RetrievalPlannerBudget,
    started: float,
) -> int:
    _require_time_budget(started, budget)
    if current + requested > budget.max_search_operations:
        raise RetrievalConfigurationError
    return current + requested


def _require_time_budget(started: float, budget: RetrievalPlannerBudget) -> None:
    if (monotonic() - started) * 1000 > budget.max_elapsed_ms:
        raise RetrievalConfigurationError


def _candidate_snapshots(
    accumulators: dict[tuple[UUID, UUID], _CandidateAccumulator],
    scope: AuthorizedSearchScope,
    limit: int,
) -> tuple[RetrievalCandidateSnapshot, ...]:
    ranked = sorted(
        accumulators.values(),
        key=lambda item: (-item.score, str(item.chunk.index_version_id), str(item.chunk.chunk_id)),
    )[:limit]
    snapshots: list[RetrievalCandidateSnapshot] = []
    for rank, item in enumerate(ranked, start=1):
        projected = item.chunk.apply_field_mask(scope.field_mask)
        snapshots.append(
            RetrievalCandidateSnapshot(
                rank=rank,
                chunk_id=projected.chunk_id,
                index_version_id=projected.index_version_id,
                knowledge_base_id=projected.knowledge_base_id,
                document_id=projected.document_id,
                document_version_id=projected.document_version_id,
                content_hash=projected.content_hash,
                source_position=projected.source_position,
                score=item.score,
                query_hit_count=len(item.query_hits or ()),
                keyword_hit_count=item.keyword_hits,
                vector_hit_count=item.vector_hits,
            )
        )
    return tuple(snapshots)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
