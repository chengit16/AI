"""实现检索计划、授权索引范围和候选快照的 PostgreSQL Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from types import TracebackType
from typing import Any, cast
from uuid import UUID

from ai_platform_backend.indexing.tokenization import TOKENIZER_VERSION
from sqlalchemy import and_, select, text
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from ai_platform_api.modules.retrieval.domain.models import (
    AuthorizedSearchScope,
    SearchIndex,
    SecurityLevel,
    Visibility,
)
from ai_platform_api.modules.retrieval.domain.planning import (
    QueryVariant,
    RetrievalAuthorization,
    RetrievalCandidateSnapshot,
    RetrievalPlannerBudget,
    RetrievalPlanningRepository,
    RetrievalPlanningUnitOfWork,
    RetrievalPlanSnapshot,
    RetrievalRunInput,
)
from ai_platform_api.modules.retrieval.infrastructure.sqlalchemy import SqlAlchemySearchIndex
from ai_platform_api.persistence.tables import (
    ai_runtime_config_versions,
    assistant_runs,
    document_index_publications,
    documents,
    index_versions,
    message_parts,
    messages,
    retrieval_candidate_snapshots,
    retrieval_plans,
    retrieval_query_variants,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyRetrievalPlanningRepository(RetrievalPlanningRepository):
    """在单事务快照内恢复排队 Run、授权索引和检索结果事实。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_run(self, run_id: UUID) -> RetrievalRunInput | None:
        # 1. 运行锁与数据库 advisory lock 配合，避免同一 Run 并发生成两份检索计划。
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:run_id), 58102)"),
            {"run_id": str(run_id)},
        )
        row = self._session.execute(
            select(assistant_runs, messages.c.message_id.label("input_message_id"))
            .join(messages, messages.c.message_id == assistant_runs.c.user_message_id)
            .where(assistant_runs.c.run_id == run_id)
            .with_for_update(of=assistant_runs)
        ).one_or_none()
        if row is None:
            return None
        # 2. 按消息 Part 顺序恢复原问题，避免依赖可变的客户端请求体。
        part_rows = self._session.execute(
            select(message_parts.c.sequence_no, message_parts.c.text_content)
            .where(message_parts.c.message_id == row.user_message_id)
            .order_by(message_parts.c.sequence_no)
        )
        query = "\n".join(
            str(part.text_content) for part in part_rows if part.text_content is not None
        )
        # 3. 只读取 Run 冻结的运行配置版本，当前发布指针变化不能影响本次 Run。
        runtime = self._session.execute(
            select(
                assistant_runs.c.runtime_config_version_id,
                ai_runtime_config_versions.c.component_versions,
            )
            .join(
                ai_runtime_config_versions,
                ai_runtime_config_versions.c.runtime_config_version_id
                == assistant_runs.c.runtime_config_version_id,
            )
            .where(assistant_runs.c.run_id == run_id)
        ).one()
        components = cast("dict[str, str]", runtime.component_versions)
        return RetrievalRunInput(
            run_id=row.run_id,
            workspace_id=row.workspace_id,
            requested_by_account_id=row.requested_by_account_id,
            runtime_config_version_id=runtime.runtime_config_version_id,
            status=row.status,
            query=query,
            embedding_model_version=components.get("embedding", ""),
            retrieval_strategy_version=components.get("retrieval", ""),
            tokenizer_version=TOKENIZER_VERSION,
        )

    def get_plan(self, run_id: UUID) -> RetrievalPlanSnapshot | None:
        # 1. 计划按 Run 唯一读取；不存在时由调用方进入首次规划路径。
        row = self._session.execute(
            select(retrieval_plans).where(retrieval_plans.c.run_id == run_id)
        ).one_or_none()
        if row is None:
            return None
        # 2. 变体和候选均按稳定序号读取，重试只能还原原始快照。
        variants = tuple(
            _query_variant(item)
            for item in self._session.execute(
                select(retrieval_query_variants)
                .where(retrieval_query_variants.c.retrieval_plan_id == row.retrieval_plan_id)
                .order_by(retrieval_query_variants.c.sequence_no)
            )
        )
        candidates = tuple(
            _candidate(item)
            for item in self._session.execute(
                select(retrieval_candidate_snapshots)
                .where(retrieval_candidate_snapshots.c.retrieval_plan_id == row.retrieval_plan_id)
                .order_by(retrieval_candidate_snapshots.c.rank)
            )
        )
        # 3. 领域对象只包含审计事实，不从快照表反向读取 Chunk 正文。
        return RetrievalPlanSnapshot(
            retrieval_plan_id=row.retrieval_plan_id,
            run_id=row.run_id,
            workspace_id=row.workspace_id,
            requested_by_account_id=row.requested_by_account_id,
            original_query_hash=row.original_query_hash,
            classification=row.classification,
            strategy_version=row.strategy_version,
            policy_decision_id=row.policy_decision_id,
            policy_version=row.policy_version,
            maximum_security_level=row.maximum_security_level,
            field_mask=frozenset(row.field_mask),
            embedding_model_version=row.embedding_model_version,
            tokenizer_version=row.tokenizer_version,
            budget=RetrievalPlannerBudget(
                max_query_characters=row.max_query_characters,
                max_query_variants=row.max_query_variants,
                max_search_operations=row.max_search_operations,
                per_channel_candidates=row.per_channel_candidates,
                final_candidate_limit=row.final_candidate_limit,
                rrf_constant=row.rrf_constant,
                max_elapsed_ms=row.max_elapsed_ms,
            ),
            variants=variants,
            candidates=candidates,
            search_operation_count=row.search_operation_count,
            keyword_candidate_count=row.keyword_candidate_count,
            vector_candidate_count=row.vector_candidate_count,
            created_at=row.created_at,
            completed_at=row.completed_at,
            duration_ms=row.duration_ms,
        )

    def resolve_search_scope(
        self,
        run: RetrievalRunInput,
        authorization: RetrievalAuthorization,
    ) -> AuthorizedSearchScope | None:
        # 1. 只读取索引和文档权限元数据；正文必须等候选已通过所有范围条件后才可读取。
        security_ranks: dict[SecurityLevel, int] = {
            "PUBLIC": 0,
            "INTERNAL": 1,
            "CONFIDENTIAL": 2,
            "RESTRICTED": 3,
        }
        maximum_rank = security_ranks[authorization.maximum_security_level]
        allowed_security_levels = tuple(
            level for level, rank in security_ranks.items() if rank <= maximum_rank
        )
        statement = (
            select(
                index_versions.c.index_version_id,
                index_versions.c.knowledge_base_id,
                index_versions.c.document_id,
                index_versions.c.document_version_id,
                index_versions.c.visibility,
                index_versions.c.department_ids,
                index_versions.c.security_level,
                documents.c.created_by_account_id,
            )
            .join(
                document_index_publications,
                and_(
                    document_index_publications.c.workspace_id == index_versions.c.workspace_id,
                    document_index_publications.c.document_id == index_versions.c.document_id,
                    document_index_publications.c.index_version_id
                    == index_versions.c.index_version_id,
                ),
            )
            .join(
                documents,
                and_(
                    documents.c.workspace_id == index_versions.c.workspace_id,
                    documents.c.document_id == index_versions.c.document_id,
                ),
            )
            .where(
                index_versions.c.workspace_id == run.workspace_id,
                index_versions.c.status == "active",
                index_versions.c.embedding_model_version == run.embedding_model_version,
                index_versions.c.security_level.in_(allowed_security_levels),
                documents.c.status == "active",
            )
        )
        # 2. 先按工作空间、活动索引、模型版本、密级和文档状态收敛候选索引。
        rows = list(self._session.execute(statement))
        if authorization.workspace_wide:
            rows = [
                row
                for row in rows
                if row.visibility != "private"
                or row.created_by_account_id == run.requested_by_account_id
            ]
        else:
            rows = [row for row in rows if _row_in_restricted_scope(row, authorization)]
        if not rows:
            return None

        # 3. 把授权并集编码为精确索引 ID 和可见性条件，避免不同 Grant 被错误做成交集。
        index_ids = frozenset(row.index_version_id for row in rows)
        private_ids = frozenset(row.document_id for row in rows if row.visibility == "private")
        # 索引版本集合已经是授权并集，不能再用某一种 Grant 的文档集合做全局 AND 收窄。
        visibilities = frozenset(row.visibility for row in rows)
        department_ids = authorization.department_ids
        return AuthorizedSearchScope(
            workspace_id=run.workspace_id,
            index_version_ids=index_ids,
            knowledge_base_ids=None,
            document_ids=None,
            department_ids=department_ids,
            visibilities=cast(frozenset[Visibility], visibilities),
            security_levels=cast(frozenset[SecurityLevel], frozenset(allowed_security_levels)),
            field_mask=authorization.field_mask,
            private_document_ids=private_ids,
            allow_all_departments=authorization.workspace_wide,
        )

    def add_plan(self, plan: RetrievalPlanSnapshot) -> None:
        # 1. 计划主事实只追加一次，数据库触发器拒绝后续修改或删除。
        self._session.execute(
            retrieval_plans.insert().values(
                retrieval_plan_id=plan.retrieval_plan_id,
                run_id=plan.run_id,
                workspace_id=plan.workspace_id,
                requested_by_account_id=plan.requested_by_account_id,
                original_query_hash=plan.original_query_hash,
                classification=plan.classification,
                strategy_version=plan.strategy_version,
                policy_decision_id=plan.policy_decision_id,
                policy_version=plan.policy_version,
                maximum_security_level=plan.maximum_security_level,
                field_mask=list(plan.field_mask),
                embedding_model_version=plan.embedding_model_version,
                tokenizer_version=plan.tokenizer_version,
                max_query_characters=plan.budget.max_query_characters,
                max_query_variants=plan.budget.max_query_variants,
                max_search_operations=plan.budget.max_search_operations,
                per_channel_candidates=plan.budget.per_channel_candidates,
                final_candidate_limit=plan.budget.final_candidate_limit,
                rrf_constant=plan.budget.rrf_constant,
                max_elapsed_ms=plan.budget.max_elapsed_ms,
                search_operation_count=plan.search_operation_count,
                keyword_candidate_count=plan.keyword_candidate_count,
                vector_candidate_count=plan.vector_candidate_count,
                status="completed",
                created_at=plan.created_at,
                completed_at=plan.completed_at,
                duration_ms=plan.duration_ms,
            )
        )
        # 2. 查询变体只记录规范化文本和 Hash，便于重试和审计而不依赖模型改写。
        self._session.execute(
            retrieval_query_variants.insert(),
            [
                {
                    "retrieval_plan_id": plan.retrieval_plan_id,
                    "sequence_no": variant.sequence_no,
                    "kind": variant.kind,
                    "query_text": variant.text,
                    "query_hash": variant.query_hash,
                }
                for variant in plan.variants
            ],
        )
        # 3. 候选快照只保存来源标识、Hash 和分数，不复制可被字段策略限制的正文。
        if plan.candidates:
            self._session.execute(
                retrieval_candidate_snapshots.insert(),
                [
                    {
                        "retrieval_plan_id": plan.retrieval_plan_id,
                        "rank": candidate.rank,
                        "chunk_id": candidate.chunk_id,
                        "index_version_id": candidate.index_version_id,
                        "knowledge_base_id": candidate.knowledge_base_id,
                        "document_id": candidate.document_id,
                        "document_version_id": candidate.document_version_id,
                        "content_hash": candidate.content_hash,
                        "source_position": candidate.source_position,
                        "score": candidate.score,
                        "query_hit_count": candidate.query_hit_count,
                        "keyword_hit_count": candidate.keyword_hit_count,
                        "vector_hit_count": candidate.vector_hit_count,
                    }
                    for candidate in plan.candidates
                ],
            )


class SqlAlchemyRetrievalPlanningUnitOfWork(RetrievalPlanningUnitOfWork):
    """把检索读写、候选快照和事务提交收敛到一个 Session。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[Session, SqlAlchemyRetrievalPlanningRepository, SearchIndex] | None
        ] = ContextVar(
            "retrieval_planning_unit_of_work",
            default=None,
        )

    def __enter__(self) -> SqlAlchemyRetrievalPlanningUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Retrieval Planning Unit of Work 不允许重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyRetrievalPlanningRepository(session),
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
    def planning(self) -> SqlAlchemyRetrievalPlanningRepository:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Retrieval Planning Unit of Work 尚未进入事务范围")
        return state[1]

    @property
    def search_index(self) -> SearchIndex:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Retrieval Planning Unit of Work 尚未进入事务范围")
        return state[2]

    def commit(self) -> None:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Retrieval Planning Unit of Work 尚未进入事务范围")
        state[0].commit()


def _row_in_restricted_scope(row: Row[Any], authorization: RetrievalAuthorization) -> bool:
    if authorization.resource_ids and row.document_id in authorization.resource_ids:
        return True
    if authorization.account_ids and row.created_by_account_id in authorization.account_ids:
        return True
    return bool(
        authorization.department_ids
        and row.visibility == "departments"
        and bool(set(row.department_ids) & authorization.department_ids)
    )


def _query_variant(row: Row[Any]) -> QueryVariant:
    return QueryVariant(row.sequence_no, row.kind, row.query_text, row.query_hash)


def _candidate(row: Row[Any]) -> RetrievalCandidateSnapshot:
    return RetrievalCandidateSnapshot(
        rank=row.rank,
        chunk_id=row.chunk_id,
        index_version_id=row.index_version_id,
        knowledge_base_id=row.knowledge_base_id,
        document_id=row.document_id,
        document_version_id=row.document_version_id,
        content_hash=row.content_hash,
        source_position=row.source_position,
        score=row.score,
        query_hit_count=row.query_hit_count,
        keyword_hit_count=row.keyword_hit_count,
        vector_hit_count=row.vector_hit_count,
    )
