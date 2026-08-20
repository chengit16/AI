"""验证 P1E-03 当前来源复核、精读、引用和不可变证据的 PostgreSQL 闭环。"""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from ai_platform_api.modules.authorization.application.field_registry import (
    load_field_policy_registry,
)
from ai_platform_api.modules.retrieval.application.evidence import RetrievalEvidenceService
from ai_platform_api.modules.retrieval.domain.errors import RetrievalScopeDeniedError
from ai_platform_api.modules.retrieval.infrastructure.evidence_sqlalchemy import (
    SqlAlchemyEvidenceProcessingUnitOfWork,
)
from ai_platform_api.modules.retrieval.infrastructure.reranking import (
    DeterministicLexicalReranker,
)
from ai_platform_api.persistence.tables import retrieval_evidence_items, retrieval_evidence_sets
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError

from tests.integration.test_p1e02_retrieval_planning_postgres import (
    ROOT,
    AllowInternalWorkspacePolicy,
    RetrievalHarness,
    context,
    create_indexed_document,
    create_retrieval_harness,
    publish_runtime_config,
    register,
)


@pytest.fixture(scope="module")
def retrieval_database() -> Iterator[RetrievalHarness]:
    """使用独占 Schema 验证证据撤权，不复用 P1E-02 的可变测试数据。"""

    yield from create_retrieval_harness(schema_prefix="p1e03_test")


def test_current_evidence_revocation_and_immutable_snapshots(
    retrieval_database: RetrievalHarness,
) -> None:
    owner = register(retrieval_database, "evidence-owner")
    outsider = register(retrieval_database, "evidence-outsider")
    publish_runtime_config(retrieval_database, owner.account_id)
    owner_context = context(owner)
    knowledge_base = retrieval_database.knowledge.create_knowledge_base(
        owner_context,
        name="P1E-03 合成证据知识库",
        default_visibility="workspace",
        default_security_level="INTERNAL",
    )
    document_id = create_indexed_document(
        retrieval_database,
        owner,
        knowledge_base.knowledge_base_id,
        title="差旅报销制度",
        content="差旅报销必须在三十天内提交,并附带合成票据。",
        visibility="workspace",
        security_level="INTERNAL",
    )
    evidence_service = RetrievalEvidenceService(
        SqlAlchemyEvidenceProcessingUnitOfWork(retrieval_database.sessions),
        AllowInternalWorkspacePolicy(),
        load_field_policy_registry(ROOT / "contracts/authorization/field-policy-registry.v1.json"),
        DeterministicLexicalReranker(),
    )

    # 1. 活动发布版本经过检索、当前来源复核和引用签发后形成可重复读取的证据快照。
    first_conversation = retrieval_database.assistant.create_conversation(
        owner_context,
        title="P1E-03 合成证据会话",
    )
    first_submission = retrieval_database.assistant.create_user_message(
        owner_context,
        conversation_id=first_conversation.conversation_id,
        texts=("差旅报销期限",),
        idempotency_key=f"p1e03-evidence-{uuid4()}",
    )
    retrieval_database.planning.retrieve(owner_context, first_submission.run.run_id)
    first = evidence_service.prepare(owner_context, first_submission.run.run_id)
    claimed = retrieval_database.assistant.claim_run(
        owner_context,
        run_id=first_submission.run.run_id,
    )
    assert claimed is not None
    retrieval_database.assistant.complete_run(
        owner_context,
        run_id=first_submission.run.run_id,
        text="合成回答",
    )
    repeated = evidence_service.prepare(owner_context, first_submission.run.run_id)

    # 完成态来源读取只能重新授权既有快照，不能被生成阶段的状态门禁误拒绝。
    assert repeated == first
    assert first.status == "sufficient"
    assert first.items[0].document_id == document_id
    assert first.items[0].security_level == "INTERNAL"
    assert first.items[0].quote in first.items[0].context_text
    assert first.items[0].source_position == {}
    with pytest.raises(RetrievalScopeDeniedError):
        evidence_service.prepare(context(outsider), first_submission.run.run_id)

    # 2. 第二个计划生成后撤销文档，证据阶段必须降级且不能读取候选快照中的旧正文。
    second_conversation = retrieval_database.assistant.create_conversation(
        owner_context,
        title="P1E-03 撤权会话",
    )
    second_submission = retrieval_database.assistant.create_user_message(
        owner_context,
        conversation_id=second_conversation.conversation_id,
        texts=("差旅报销期限",),
        idempotency_key=f"p1e03-revoked-{uuid4()}",
    )
    retrieval_database.planning.retrieve(owner_context, second_submission.run.run_id)
    retrieval_database.knowledge.delete_document(
        owner_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        document_id=document_id,
    )
    revoked = evidence_service.prepare(owner_context, second_submission.run.run_id)

    assert revoked.status == "uncertain"
    assert revoked.degradation_reason == "no_current_evidence"
    assert revoked.items == ()
    with pytest.raises(RetrievalScopeDeniedError):
        evidence_service.prepare(owner_context, first_submission.run.run_id)

    # 3. 证据集和证据项均只追加，维护 SQL 不能篡改已签发的原文与降级结论。
    with retrieval_database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(retrieval_evidence_sets)) == 2
        assert session.scalar(select(func.count()).select_from(retrieval_evidence_items)) == 1
    with pytest.raises(DBAPIError), retrieval_database.sessions.begin() as session:
        session.execute(
            update(retrieval_evidence_sets)
            .where(retrieval_evidence_sets.c.evidence_set_id == first.evidence_set_id)
            .values(status="uncertain", degradation_reason="no_current_evidence")
        )
    with pytest.raises(DBAPIError), retrieval_database.sessions.begin() as session:
        session.execute(
            delete(retrieval_evidence_items).where(
                retrieval_evidence_items.c.evidence_set_id == first.evidence_set_id
            )
        )
