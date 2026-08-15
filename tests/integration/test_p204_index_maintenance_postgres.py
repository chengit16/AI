"""验证 P2-04 索引巡检、差异修复、全量重建和失败产物清理。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from ai_platform_backend.indexing.persistence import (
    document_index_publications,
    index_inspection_findings,
    index_maintenance_runs,
    index_versions,
    retrieval_chunks,
)
from ai_platform_backend.indexing.tokenization import TOKENIZER_VERSION
from ai_platform_worker.modules.indexing.application.build import (
    IndexCommitProcessor,
    IndexEmbeddingProcessor,
)
from ai_platform_worker.modules.indexing.application.maintenance import (
    IndexMaintenanceProcessor,
)
from ai_platform_worker.modules.indexing.infrastructure.embeddings import (
    DeterministicHashEmbeddingAdapter,
)
from ai_platform_worker.modules.indexing.infrastructure.maintenance_sqlalchemy import (
    SqlAlchemyIndexMaintenanceStore,
)
from sqlalchemy import func, select, update

from tests.integration import test_p1d04_index_versions_postgres as index_support

index_database = index_support.index_database


@dataclass(frozen=True)
class PublishedIndexScenario:
    workspace_id: UUID
    actor_id: UUID
    document_id: UUID
    document_version_id: UUID
    active_index_id: UUID
    payloads: dict[str, bytes]
    embedding: IndexEmbeddingProcessor
    indexing: IndexCommitProcessor
    current: datetime


def _maintenance(harness: index_support.IndexHarness) -> IndexMaintenanceProcessor:
    embedding = DeterministicHashEmbeddingAdapter()
    return IndexMaintenanceProcessor(
        SqlAlchemyIndexMaintenanceStore(harness.sessions),
        max_attempts=3,
        chunker_version="structural-char-v1",
        embedding_model_version=embedding.model_version,
        tokenizer_version=TOKENIZER_VERSION,
    )


def _published_index(harness: index_support.IndexHarness) -> PublishedIndexScenario:
    """建立全合成已发布索引，供每个维护场景独立破坏和恢复。"""

    owner = index_support.register(harness)
    request_context = index_support.context(owner, owner.personal_workspace_id)
    knowledge_base = harness.knowledge.create_knowledge_base(
        request_context,
        name=f"合成 P2-04 知识库 {uuid4().hex}",
    )
    current = datetime.now(UTC) + timedelta(seconds=1)

    # 1. 文档、入库和发布均走正式事实服务，测试不手工伪造来源链。
    document_id, document_version_id = index_support.upload_version(
        harness,
        request_context,
        knowledge_base.knowledge_base_id,
        None,
        source_content=b"synthetic-p204-index-maintenance",
    )
    artifact_key, payload = index_support.complete_ingestion(
        harness,
        document_version_id,
        text_content="P2-04 合成索引巡检与重建证据。",
        completed_at=current,
    )
    harness.knowledge.mark_document_version_ready(
        request_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        document_id=document_id,
        document_version_id=document_version_id,
        content_hash="0" * 64,
    )
    harness.knowledge.publish_document_version(
        request_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        document_id=document_id,
        document_version_id=document_version_id,
    )

    # 2. Embedding 和提交使用真实 Worker 处理器，确保活动指针与 Chunk 结构一致。
    payloads = {artifact_key: payload}
    embedding, indexing = index_support.processors(harness, payloads)
    assert embedding.run_batch(limit=1, now=current).succeeded == 1
    assert indexing.run_batch(limit=1, now=current).succeeded == 1
    with harness.engine.connect() as connection:
        active_index_id = connection.scalar(
            select(document_index_publications.c.index_version_id).where(
                document_index_publications.c.workspace_id == owner.personal_workspace_id,
                document_index_publications.c.document_id == document_id,
            )
        )
    assert isinstance(active_index_id, UUID)
    return PublishedIndexScenario(
        owner.personal_workspace_id,
        owner.account_id,
        document_id,
        document_version_id,
        active_index_id,
        payloads,
        embedding,
        indexing,
        current,
    )


def test_healthy_index_inspection_persists_zero_difference(
    index_database: index_support.IndexHarness,
) -> None:
    scenario = _published_index(index_database)
    report, repair = _maintenance(index_database).inspect_and_repair(
        now=scenario.current + timedelta(seconds=1),
        workspace_id=scenario.workspace_id,
    )

    assert report.scanned_document_count == 1
    assert report.inconsistency_count == 0
    assert report.findings == ()
    assert repair.repaired_document_count == 0
    assert repair.rebuild_queued_count == 0
    with index_database.engine.connect() as connection:
        run = connection.execute(
            select(
                index_maintenance_runs.c.inconsistency_count,
                index_maintenance_runs.c.result_digest,
            ).where(index_maintenance_runs.c.maintenance_run_id == report.maintenance_run_id)
        ).one()
        finding_count = connection.scalar(
            select(func.count())
            .select_from(index_inspection_findings)
            .where(index_inspection_findings.c.maintenance_run_id == report.maintenance_run_id)
        )
    assert run.inconsistency_count == 0
    assert len(run.result_digest) == 64
    assert finding_count == 0


def test_corrupt_active_index_switches_to_complete_retired_candidate(
    index_database: index_support.IndexHarness,
) -> None:
    scenario = _published_index(index_database)
    maintenance = _maintenance(index_database)

    # 1. 先完成一次全量重建，形成同一文档版本的完整 retired 候选。
    rebuild = maintenance.enqueue_full_rebuild(
        uuid4(),
        requested_by_actor_id=scenario.actor_id,
        workspace_id=scenario.workspace_id,
        now=scenario.current + timedelta(seconds=1),
    )
    assert rebuild.rebuild_queued_count == 1
    assert scenario.embedding.run_batch(
        limit=1, now=scenario.current + timedelta(seconds=2)
    ).succeeded
    assert scenario.indexing.run_batch(
        limit=1, now=scenario.current + timedelta(seconds=2)
    ).succeeded
    with index_database.engine.connect() as connection:
        newest_index_id = connection.scalar(
            select(document_index_publications.c.index_version_id).where(
                document_index_publications.c.document_id == scenario.document_id
            )
        )
    assert isinstance(newest_index_id, UUID) and newest_index_id != scenario.active_index_id

    # 2. 删除最新活动索引的一个 Chunk，巡检必须改切完整旧候选而非暴露残缺版本。
    with index_database.engine.begin() as connection:
        chunk_id = connection.scalar(
            select(retrieval_chunks.c.chunk_id)
            .where(retrieval_chunks.c.index_version_id == newest_index_id)
            .limit(1)
        )
        assert isinstance(chunk_id, UUID)
        connection.execute(
            retrieval_chunks.delete().where(
                retrieval_chunks.c.index_version_id == newest_index_id,
                retrieval_chunks.c.chunk_id == chunk_id,
            )
        )
    report, repair = maintenance.inspect_and_repair(
        now=scenario.current + timedelta(seconds=3),
        workspace_id=scenario.workspace_id,
    )

    assert "INDEX_CHUNK_COUNT_MISMATCH" in {finding.code for finding in report.findings}
    assert repair.repaired_document_count == 1
    assert repair.rebuild_queued_count == 0
    with index_database.engine.connect() as connection:
        publication = connection.scalar(
            select(document_index_publications.c.index_version_id).where(
                document_index_publications.c.document_id == scenario.document_id
            )
        )
        restored_status = connection.scalar(
            select(index_versions.c.status).where(
                index_versions.c.index_version_id == scenario.active_index_id
            )
        )
        corrupt_status = connection.scalar(
            select(index_versions.c.status).where(
                index_versions.c.index_version_id == newest_index_id
            )
        )
    assert publication == scenario.active_index_id
    assert restored_status == "active"
    assert corrupt_status == "retired"


def test_missing_complete_candidate_disables_bad_surface_and_rebuilds(
    index_database: index_support.IndexHarness,
) -> None:
    scenario = _published_index(index_database)
    maintenance = _maintenance(index_database)
    with index_database.engine.begin() as connection:
        connection.execute(
            retrieval_chunks.delete().where(
                retrieval_chunks.c.index_version_id == scenario.active_index_id
            )
        )

    # 1. 没有完整候选时先撤销异常活动面；新构建在提交前保持不可见。
    report, repair = maintenance.inspect_and_repair(
        now=scenario.current + timedelta(seconds=1),
        workspace_id=scenario.workspace_id,
    )
    assert report.inconsistency_count > 0
    assert repair.repaired_document_count == 0
    assert repair.rebuild_queued_count == 1
    with index_database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(document_index_publications)
                .where(document_index_publications.c.document_id == scenario.document_id)
            )
            == 0
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(retrieval_chunks)
                .where(
                    retrieval_chunks.c.document_id == scenario.document_id,
                    retrieval_chunks.c.active.is_(True),
                )
            )
            == 0
        )

    # 2. 只有完整构建提交后，新的活动索引和 Chunk 才在同一事务中重新可见。
    assert scenario.embedding.run_batch(
        limit=1, now=scenario.current + timedelta(seconds=2)
    ).succeeded
    with index_database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(document_index_publications)
                .where(document_index_publications.c.document_id == scenario.document_id)
            )
            == 0
        )
    assert scenario.indexing.run_batch(
        limit=1, now=scenario.current + timedelta(seconds=2)
    ).succeeded
    with index_database.engine.connect() as connection:
        new_active = connection.scalar(
            select(document_index_publications.c.index_version_id).where(
                document_index_publications.c.document_id == scenario.document_id
            )
        )
    assert isinstance(new_active, UUID) and new_active != scenario.active_index_id


def test_full_rebuild_is_idempotent_and_keeps_healthy_index_serving(
    index_database: index_support.IndexHarness,
) -> None:
    scenario = _published_index(index_database)
    maintenance = _maintenance(index_database)
    maintenance_run_id = uuid4()
    first = maintenance.enqueue_full_rebuild(
        maintenance_run_id,
        requested_by_actor_id=scenario.actor_id,
        workspace_id=scenario.workspace_id,
        now=scenario.current + timedelta(seconds=1),
    )
    repeated = maintenance.enqueue_full_rebuild(
        maintenance_run_id,
        requested_by_actor_id=scenario.actor_id,
        workspace_id=scenario.workspace_id,
        now=scenario.current + timedelta(seconds=2),
    )

    assert repeated == first
    assert first.scanned_document_count == 1
    assert first.rebuild_queued_count == 1
    with index_database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(document_index_publications.c.index_version_id).where(
                    document_index_publications.c.document_id == scenario.document_id
                )
            )
            == scenario.active_index_id
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(index_versions)
                .where(index_versions.c.document_id == scenario.document_id)
            )
            == 2
        )

    assert scenario.embedding.run_batch(
        limit=1, now=scenario.current + timedelta(seconds=3)
    ).succeeded
    with index_database.engine.connect() as connection:
        # 新构建已有完整 staged Chunk 时，提交事务前仍必须由健康旧索引提供服务。
        assert (
            connection.scalar(
                select(document_index_publications.c.index_version_id).where(
                    document_index_publications.c.document_id == scenario.document_id
                )
            )
            == scenario.active_index_id
        )
        active_chunk_count = connection.scalar(
            select(func.count())
            .select_from(retrieval_chunks)
            .where(
                retrieval_chunks.c.index_version_id == scenario.active_index_id,
                retrieval_chunks.c.active.is_(True),
            )
        )
        assert isinstance(active_chunk_count, int) and active_chunk_count > 0
    assert scenario.indexing.run_batch(
        limit=1, now=scenario.current + timedelta(seconds=3)
    ).succeeded
    with index_database.engine.connect() as connection:
        replacement = connection.scalar(
            select(document_index_publications.c.index_version_id).where(
                document_index_publications.c.document_id == scenario.document_id
            )
        )
    assert isinstance(replacement, UUID) and replacement != scenario.active_index_id


def test_cleanup_only_removes_unrecoverable_invisible_chunks(
    index_database: index_support.IndexHarness,
) -> None:
    scenario = _published_index(index_database)
    maintenance = _maintenance(index_database)

    # 1. 建立两份不可见 staged Chunk，分别模拟恢复预算耗尽和仍可恢复的死信。
    doomed_id = index_database.store.enqueue_rebuild(
        workspace_id=scenario.workspace_id,
        document_version_id=scenario.document_version_id,
        now=scenario.current + timedelta(seconds=1),
        max_attempts=3,
        chunker_version="structural-char-v1",
        embedding_model_version=DeterministicHashEmbeddingAdapter.model_version,
        tokenizer_version=TOKENIZER_VERSION,
    )
    assert scenario.embedding.run_batch(
        limit=1, now=scenario.current + timedelta(seconds=2)
    ).succeeded
    assert isinstance(doomed_id, UUID)
    doomed_at = scenario.current + timedelta(seconds=2)
    with index_database.engine.begin() as connection:
        connection.execute(
            update(index_versions)
            .where(index_versions.c.index_version_id == doomed_id)
            .values(
                status="dead_letter",
                failure_stage="index",
                error_code="INDEX_SYNTHETIC_DEAD_LETTER",
                error_message="合成恢复预算耗尽",
                completed_at=doomed_at,
                dead_lettered_at=doomed_at,
                manual_recovery_count=3,
                last_recovered_by_actor_id=scenario.actor_id,
                last_recovered_at=doomed_at,
            )
        )
    recoverable_id = index_database.store.enqueue_rebuild(
        workspace_id=scenario.workspace_id,
        document_version_id=scenario.document_version_id,
        now=scenario.current + timedelta(seconds=3),
        max_attempts=3,
        chunker_version="structural-char-v1",
        embedding_model_version=DeterministicHashEmbeddingAdapter.model_version,
        tokenizer_version=TOKENIZER_VERSION,
    )
    assert scenario.embedding.run_batch(
        limit=1, now=scenario.current + timedelta(seconds=4)
    ).succeeded
    assert isinstance(recoverable_id, UUID)
    recoverable_at = scenario.current + timedelta(seconds=4)
    with index_database.engine.begin() as connection:
        connection.execute(
            update(index_versions)
            .where(index_versions.c.index_version_id == recoverable_id)
            .values(
                status="dead_letter",
                failure_stage="index",
                error_code="INDEX_SYNTHETIC_DEAD_LETTER",
                error_message="合成仍可恢复死信",
                completed_at=recoverable_at,
                dead_lettered_at=recoverable_at,
                manual_recovery_count=2,
                last_recovered_by_actor_id=scenario.actor_id,
                last_recovered_at=recoverable_at,
            )
        )

    # 2. 清理运行可安全重放，活动发布与仍可恢复的 staged Chunk 均不得受影响。
    maintenance_run_id = uuid4()
    first = maintenance.cleanup_unrecoverable_chunks(
        maintenance_run_id,
        requested_by_actor_id=scenario.actor_id,
        workspace_id=scenario.workspace_id,
        now=scenario.current + timedelta(seconds=5),
    )
    repeated = maintenance.cleanup_unrecoverable_chunks(
        maintenance_run_id,
        requested_by_actor_id=scenario.actor_id,
        workspace_id=scenario.workspace_id,
        now=scenario.current + timedelta(seconds=6),
    )
    assert repeated == first
    assert first.cleaned_chunk_count > 0
    with index_database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(retrieval_chunks)
                .where(retrieval_chunks.c.index_version_id == doomed_id)
            )
            == 0
        )
        recoverable_count = connection.scalar(
            select(func.count())
            .select_from(retrieval_chunks)
            .where(retrieval_chunks.c.index_version_id == recoverable_id)
        )
        assert isinstance(recoverable_count, int) and recoverable_count > 0
        assert (
            connection.scalar(
                select(document_index_publications.c.index_version_id).where(
                    document_index_publications.c.document_id == scenario.document_id
                )
            )
            == scenario.active_index_id
        )
