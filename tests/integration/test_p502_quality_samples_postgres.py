"""验证 P5-02 质量样本版本、授权投影和 PostgreSQL 不可变边界。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.quality.application.errors import (
    QualityConflictError,
    QualityDeniedError,
    QualityValidationError,
)
from ai_platform_api.modules.quality.application.service import QualitySampleService
from ai_platform_api.modules.quality.domain.models import (
    QualitySampleCapture,
    QualitySampleDeletion,
)
from ai_platform_api.modules.quality.infrastructure.sqlalchemy import (
    SqlAlchemyQualityUnitOfWork,
)
from ai_platform_api.persistence.tables import (
    audit_records,
    outbox_events,
    quality_dataset_members,
    quality_dataset_versions,
    quality_sample_versions,
)
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError

from tests.support.p502_quality import QualityHarness, authorized_context, register

pytest_plugins = ("tests.support.p502_quality_plugin",)

INPUT_TEXT = "合成问题:退款规则是什么?"
OUTPUT_TEXT = "合成回答:可以无限期退款。"
FEEDBACK_TEXT = "合成反馈:规则与引用不一致。"


def test_authorized_feedback_enters_versioned_quality_dataset(
    quality_database: QualityHarness,
) -> None:
    owner = register(quality_database, "feedback-owner")
    feedback_id = uuid4()
    message_id = uuid4()
    captured = quality_database.quality.capture(
        authorized_context(owner, "assistant.feedback.manage"),
        QualitySampleCapture(
            source_type="user_feedback",
            source_workspace_id=owner.workspace_id,
            source_id=feedback_id,
            source_version=1,
            resource_id=message_id,
            signal_code="unhelpful",
            reason_codes=("incorrect", "missing_source"),
            input_text=INPUT_TEXT,
            output_text=OUTPUT_TEXT,
            feedback_text=FEEDBACK_TEXT,
            correction_text=None,
            source_security_level="INTERNAL",
        ),
    )

    snapshot = quality_database.quality.get_latest_dataset(
        authorized_context(owner, "operations.records.read")
    )

    assert captured.sample.source_type == "user_feedback"
    assert captured.sample.source_id == feedback_id
    assert captured.sample.source_version == 1
    assert captured.sample.input_digest == (
        "e933e3e24bc479bac9f721130c5193bbcbd7cbbf53a7ab841258de7e607313f4"
    )
    assert captured.sample.output_digest == (
        "8c63bf3410e77568cdf4a43be18837d45b909273d4df092d07bdb4966de676aa"
    )
    assert captured.sample.feedback_digest == (
        "8cc439cdba2079d6c98a7e0c923160c2a70b97ab4bb865dff4113592bebf1bb4"
    )
    assert captured.sample.authorized_permission_code == "assistant.feedback.manage"
    assert captured.sample.policy_version == 1
    assert captured.sample.workspace_scope is True
    assert captured.dataset.version_number == 1
    assert captured.dataset.sample_count == 1
    assert snapshot == captured
    assert snapshot is not None
    assert snapshot.samples == (captured.sample,)
    serialized = repr(snapshot)
    assert INPUT_TEXT not in serialized
    assert OUTPUT_TEXT not in serialized
    assert FEEDBACK_TEXT not in serialized


def test_source_revision_is_idempotent_and_preserves_dataset_history(
    quality_database: QualityHarness,
) -> None:
    owner = register(quality_database, "revision-owner")
    capture_context = authorized_context(owner, "assistant.feedback.manage")
    read_context = authorized_context(owner, "operations.records.read")
    source = QualitySampleCapture(
        source_type="user_feedback",
        source_workspace_id=owner.workspace_id,
        source_id=uuid4(),
        source_version=1,
        resource_id=uuid4(),
        signal_code="unhelpful",
        reason_codes=("incorrect",),
        input_text=INPUT_TEXT,
        output_text=OUTPUT_TEXT,
        feedback_text=FEEDBACK_TEXT,
        correction_text=None,
        source_security_level="INTERNAL",
    )

    first = quality_database.quality.capture(capture_context, source)
    repeated = quality_database.quality.capture(
        authorized_context(owner, "assistant.feedback.manage"),
        source,
    )
    with pytest.raises(QualityConflictError) as conflict:
        quality_database.quality.capture(
            authorized_context(owner, "assistant.feedback.manage"),
            replace(source, output_text="合成回答:同版本发生内容漂移。"),
        )
    revised = quality_database.quality.capture(
        authorized_context(owner, "assistant.feedback.manage"),
        replace(
            source,
            source_version=2,
            output_text="合成回答:退款期限为七天。",
            feedback_text="合成反馈:修订后的回答与规则一致。",
        ),
    )
    historical = quality_database.quality.get_dataset(
        read_context,
        first.dataset.dataset_version_id,
    )

    assert repeated == first
    assert conflict.value.error_code == "IDEMPOTENCY_CONFLICT"
    assert revised.dataset.version_number == 2
    assert revised.dataset.previous_dataset_version_id == first.dataset.dataset_version_id
    assert revised.sample.supersedes_sample_version_id == first.sample.sample_version_id
    assert revised.samples == (revised.sample,)
    assert historical == first


def test_cross_workspace_and_incomplete_projection_fail_closed(
    quality_database: QualityHarness,
) -> None:
    owner = register(quality_database, "security-owner")
    outsider = register(quality_database, "security-outsider")
    source = QualitySampleCapture(
        source_type="user_feedback",
        source_workspace_id=owner.workspace_id,
        source_id=uuid4(),
        source_version=1,
        resource_id=uuid4(),
        signal_code="unhelpful",
        reason_codes=("unsafe",),
        input_text=INPUT_TEXT,
        output_text=OUTPUT_TEXT,
        feedback_text=FEEDBACK_TEXT,
        correction_text=None,
        source_security_level="INTERNAL",
    )
    owner_context = authorized_context(owner, "assistant.feedback.manage")
    created = quality_database.quality.capture(owner_context, source)

    assert (
        quality_database.quality.get_dataset(
            authorized_context(outsider, "operations.records.read"),
            created.dataset.dataset_version_id,
        )
        is None
    )
    denied_contexts = (
        replace(owner_context, authorized_policy_decision_id=None),
        replace(owner_context, authorized_field_mask=frozenset({"content"})),
        replace(owner_context, authorized_maximum_security_level="PUBLIC"),
        authorized_context(outsider, "assistant.feedback.manage"),
    )
    for denied_context in denied_contexts:
        with pytest.raises(QualityDeniedError):
            quality_database.quality.capture(denied_context, source)

    assert (
        quality_database.quality.get_latest_dataset(
            authorized_context(outsider, "operations.records.read")
        )
        is None
    )


def test_source_deletion_creates_tombstone_and_removes_active_member(
    quality_database: QualityHarness,
) -> None:
    owner = register(quality_database, "deletion-owner")
    source = QualitySampleCapture(
        source_type="user_feedback",
        source_workspace_id=owner.workspace_id,
        source_id=uuid4(),
        source_version=1,
        resource_id=uuid4(),
        signal_code="unhelpful",
        reason_codes=("incorrect",),
        input_text=INPUT_TEXT,
        output_text=OUTPUT_TEXT,
        feedback_text=FEEDBACK_TEXT,
        correction_text=None,
        source_security_level="INTERNAL",
    )
    first = quality_database.quality.capture(
        authorized_context(owner, "assistant.feedback.manage"),
        source,
    )
    deletion = QualitySampleDeletion(
        source_type=source.source_type,
        source_workspace_id=owner.workspace_id,
        source_id=source.source_id,
        source_version=2,
        resource_id=source.resource_id,
        reason_code="source_deleted",
        source_security_level="INTERNAL",
    )

    deleted = quality_database.quality.delete_source(
        authorized_context(owner, "assistant.feedback.manage"),
        deletion,
    )
    repeated = quality_database.quality.delete_source(
        authorized_context(owner, "assistant.feedback.manage"),
        deletion,
    )
    historical = quality_database.quality.get_dataset(
        authorized_context(owner, "operations.records.read"),
        first.dataset.dataset_version_id,
    )

    assert deleted == repeated
    assert deleted.sample.operation == "deleted"
    assert deleted.sample.supersedes_sample_version_id == first.sample.sample_version_id
    assert deleted.sample.input_digest is None
    assert deleted.sample.output_digest is None
    assert deleted.sample.feedback_digest is None
    assert deleted.sample.correction_digest is None
    assert deleted.dataset.version_number == 2
    assert deleted.dataset.sample_count == 0
    assert deleted.samples == ()
    assert historical == first


def test_run_failure_and_human_correction_enforce_source_contracts(
    quality_database: QualityHarness,
) -> None:
    owner = register(quality_database, "source-contract-owner")
    run_failure = QualitySampleCapture(
        source_type="run_failure",
        source_workspace_id=owner.workspace_id,
        source_id=uuid4(),
        source_version=1,
        resource_id=uuid4(),
        signal_code="model_timeout",
        reason_codes=("timeout",),
        input_text=INPUT_TEXT,
        output_text=None,
        feedback_text=None,
        correction_text=None,
        source_security_level="INTERNAL",
    )
    failed_run_snapshot = quality_database.quality.capture(
        authorized_context(owner, "operations.records.read"),
        run_failure,
    )
    correction = QualitySampleCapture(
        source_type="human_correction",
        source_workspace_id=owner.workspace_id,
        source_id=uuid4(),
        source_version=1,
        resource_id=uuid4(),
        signal_code="corrected",
        reason_codes=("incorrect",),
        input_text=INPUT_TEXT,
        output_text=OUTPUT_TEXT,
        feedback_text=None,
        correction_text=None,
        source_security_level="INTERNAL",
    )

    with pytest.raises(QualityValidationError):
        quality_database.quality.capture(
            authorized_context(owner, "agent.test.execute"),
            correction,
        )
    corrected_snapshot = quality_database.quality.capture(
        authorized_context(owner, "agent.test.execute"),
        replace(correction, correction_text="合成人工修正:退款期限为七天。"),
    )

    assert failed_run_snapshot.sample.output_digest is None
    assert corrected_snapshot.sample.correction_digest is not None
    assert corrected_snapshot.dataset.sample_count == 2


def test_quality_facts_exclude_raw_content_and_reject_mutation(
    quality_database: QualityHarness,
) -> None:
    owner = register(quality_database, "storage-owner")
    credential_marker = "sk-synthetic-p502-not-a-real-credential"
    source = QualitySampleCapture(
        source_type="user_feedback",
        source_workspace_id=owner.workspace_id,
        source_id=uuid4(),
        source_version=1,
        resource_id=uuid4(),
        signal_code="unhelpful",
        reason_codes=("unsafe",),
        input_text=INPUT_TEXT,
        output_text=OUTPUT_TEXT,
        feedback_text=f"{FEEDBACK_TEXT}{credential_marker}",
        correction_text=None,
        source_security_level="INTERNAL",
    )
    snapshot = quality_database.quality.capture(
        authorized_context(owner, "assistant.feedback.manage"),
        source,
    )

    with quality_database.sessions() as session:
        sample_row = (
            session.execute(
                select(quality_sample_versions).where(
                    quality_sample_versions.c.sample_version_id == snapshot.sample.sample_version_id
                )
            )
            .mappings()
            .one()
        )
        audit_values = tuple(
            session.scalars(
                select(audit_records.c.attributes).where(
                    audit_records.c.workspace_id == owner.workspace_id
                )
            )
        )
        outbox_values = tuple(
            session.scalars(
                select(outbox_events.c.payload).where(
                    outbox_events.c.workspace_id == owner.workspace_id
                )
            )
        )
    persisted = json.dumps(
        {"sample": dict(sample_row), "audit": audit_values, "outbox": outbox_values},
        ensure_ascii=False,
        default=str,
    )
    for forbidden in (INPUT_TEXT, OUTPUT_TEXT, FEEDBACK_TEXT, credential_marker):
        assert forbidden not in persisted

    immutable_statements = (
        update(quality_sample_versions)
        .where(quality_sample_versions.c.sample_version_id == snapshot.sample.sample_version_id)
        .values(signal_code="tampered"),
        delete(quality_sample_versions).where(
            quality_sample_versions.c.sample_version_id == snapshot.sample.sample_version_id
        ),
        update(quality_dataset_versions)
        .where(quality_dataset_versions.c.dataset_version_id == snapshot.dataset.dataset_version_id)
        .values(sample_count=0),
        delete(quality_dataset_members).where(
            quality_dataset_members.c.dataset_version_id == snapshot.dataset.dataset_version_id
        ),
    )
    for statement in immutable_statements:
        with quality_database.sessions() as session:
            with pytest.raises(DBAPIError, match="quality facts are immutable"):
                session.execute(statement)
            session.rollback()


def test_concurrent_same_source_capture_reuses_one_dataset(
    quality_database: QualityHarness,
) -> None:
    owner = register(quality_database, "concurrent-owner")
    source = QualitySampleCapture(
        source_type="user_feedback",
        source_workspace_id=owner.workspace_id,
        source_id=uuid4(),
        source_version=1,
        resource_id=uuid4(),
        signal_code="unhelpful",
        reason_codes=("incorrect",),
        input_text=INPUT_TEXT,
        output_text=OUTPUT_TEXT,
        feedback_text=FEEDBACK_TEXT,
        correction_text=None,
        source_security_level="INTERNAL",
    )
    barrier = Barrier(6)

    def capture_once() -> UUID:
        barrier.wait()
        service = QualitySampleService(SqlAlchemyQualityUnitOfWork(quality_database.sessions))
        snapshot = service.capture(
            authorized_context(owner, "assistant.feedback.manage"),
            source,
        )
        return snapshot.dataset.dataset_version_id

    with ThreadPoolExecutor(max_workers=6) as executor:
        dataset_ids = tuple(executor.map(lambda _: capture_once(), range(6)))

    assert len(set(dataset_ids)) == 1
    latest = quality_database.quality.get_latest_dataset(
        authorized_context(owner, "operations.records.read")
    )
    assert latest is not None
    assert latest.dataset.version_number == 1
    assert latest.dataset.sample_count == 1
