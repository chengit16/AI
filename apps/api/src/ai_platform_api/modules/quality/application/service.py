"""把授权后的瞬时质量正文沉淀为不可变摘要和版本化数据集。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.quality.application.documents import (
    build_dataset_version,
    build_deletion_version,
    build_sample_version,
    normalize_capture,
    normalize_deletion,
)
from ai_platform_api.modules.quality.application.errors import (
    QualityConflictError,
    QualityDeniedError,
)
from ai_platform_api.modules.quality.domain.models import (
    QualityDatasetSnapshot,
    QualityDatasetVersion,
    QualitySampleCapture,
    QualitySampleDeletion,
    QualitySampleVersion,
    QualitySecurityLevel,
    QualitySourceType,
    QualityUnitOfWork,
)

SOURCE_PERMISSIONS: dict[QualitySourceType, frozenset[str]] = {
    "run_failure": frozenset({"operations.records.read"}),
    "user_feedback": frozenset({"assistant.feedback.manage"}),
    "human_correction": frozenset({"agent.test.execute"}),
}
READ_PERMISSIONS = frozenset({"operations.records.read", "agent.test.read"})
SECURITY_RANK: dict[QualitySecurityLevel, int] = {
    "PUBLIC": 0,
    "INTERNAL": 1,
    "CONFIDENTIAL": 2,
    "RESTRICTED": 3,
}


class QualitySampleService:
    """提供质量样本采集和工作空间级最新数据集查询。"""

    def __init__(self, unit_of_work: QualityUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def capture(
        self,
        context: RequestContext,
        capture: QualitySampleCapture,
    ) -> QualityDatasetSnapshot:
        """采集一个来源版本；同版本同内容幂等，内容漂移失败关闭。"""

        _require_capture_authorization(context, capture)
        normalized = normalize_capture(capture)
        return self._persist_sample(
            context,
            build_sample_version(context, normalized, datetime.now(UTC)),
        )

    def delete_source(
        self,
        context: RequestContext,
        deletion: QualitySampleDeletion,
    ) -> QualityDatasetSnapshot:
        """记录来源删除 tombstone，并从新数据集移除该逻辑样本。"""

        _require_source_authorization(
            context,
            source_type=deletion.source_type,
            source_workspace_id=deletion.source_workspace_id,
            resource_id=deletion.resource_id,
            security_level=deletion.source_security_level,
        )
        normalized = normalize_deletion(deletion)
        return self._persist_sample(
            context,
            build_deletion_version(context, normalized, datetime.now(UTC)),
        )

    def _persist_sample(
        self,
        context: RequestContext,
        sample: QualitySampleVersion,
    ) -> QualityDatasetSnapshot:
        """统一执行来源幂等、单调版本和数据集快照事务。"""

        with self._unit_of_work as unit_of_work:
            # 1. 工作空间锁覆盖首次快照和重试读取，同来源并发只能观察一个已提交版本。
            unit_of_work.quality.lock_workspace(context.workspace_id)
            existing = unit_of_work.quality.get_by_source_version(
                context.workspace_id,
                sample.source_type,
                sample.source_id,
                sample.source_version,
            )
            if existing is not None:
                if existing.source_digest != sample.source_digest:
                    raise QualityConflictError
                snapshot = unit_of_work.quality.get_snapshot_by_trigger(
                    context.workspace_id,
                    existing.sample_version_id,
                )
                if snapshot is None:
                    raise QualityConflictError
                return snapshot

            # 2. 在锁内建立版本链和新成员集合，低版本倒退与损坏的历史快照失败关闭。
            latest_source = unit_of_work.quality.get_latest_source(
                context.workspace_id,
                sample.source_type,
                sample.source_id,
            )
            if latest_source is not None and sample.source_version <= latest_source.source_version:
                raise QualityConflictError
            sample = _with_superseded(sample, latest_source)
            latest_dataset = unit_of_work.quality.get_latest_dataset(
                context.workspace_id,
                for_update=True,
            )
            active_samples = _next_members(
                unit_of_work,
                context.workspace_id,
                latest_dataset,
                sample,
            )
            dataset = build_dataset_version(
                context,
                latest_dataset,
                sample,
                active_samples,
                sample.created_at,
            )
            # 3. 样本、数据集与成员必须同事务插入，任何约束失败都不能留下半成品。
            unit_of_work.quality.add_sample(sample)
            unit_of_work.quality.add_dataset(dataset, active_samples)
            unit_of_work.commit()
            return QualityDatasetSnapshot(dataset, active_samples, sample)

    def get_latest_dataset(self, context: RequestContext) -> QualityDatasetSnapshot | None:
        """读取工作空间最新质量数据集；集合查询只接受完整工作空间授权。"""

        _require_read_authorization(context)
        with self._unit_of_work as unit_of_work:
            dataset = unit_of_work.quality.get_latest_dataset(context.workspace_id)
            if dataset is None:
                return None
            return unit_of_work.quality.get_snapshot(
                context.workspace_id,
                dataset.dataset_version_id,
            )

    def get_dataset(
        self,
        context: RequestContext,
        dataset_version_id: UUID,
    ) -> QualityDatasetSnapshot | None:
        """按工作空间读取历史快照，跨空间标识统一返回不可见。"""

        _require_read_authorization(context)
        with self._unit_of_work as unit_of_work:
            return unit_of_work.quality.get_snapshot(
                context.workspace_id,
                dataset_version_id,
            )


def _require_capture_authorization(
    context: RequestContext,
    capture: QualitySampleCapture,
) -> None:
    """在生成任何正文摘要前验证权限、资源范围、字段遮罩和密级。"""

    _require_source_authorization(
        context,
        source_type=capture.source_type,
        source_workspace_id=capture.source_workspace_id,
        resource_id=capture.resource_id,
        security_level=capture.source_security_level,
    )
    masked_fields = context.authorized_field_mask
    required_fields = {
        field_name
        for field_name, value in (
            ("input_text", capture.input_text),
            ("output_text", capture.output_text),
            ("feedback_text", capture.feedback_text),
            ("correction_text", capture.correction_text),
        )
        if value is not None
    }
    if "content" in masked_fields or not required_fields.isdisjoint(masked_fields):
        raise QualityDeniedError


def _require_source_authorization(
    context: RequestContext,
    *,
    source_type: QualitySourceType,
    source_workspace_id: UUID,
    resource_id: UUID,
    security_level: QualitySecurityLevel,
) -> None:
    """验证来源写入所需的完整决策、空间、资源范围和最高密级。"""

    if (
        context.audit_authorization is None
        or source_workspace_id != context.workspace_id
        or context.authorized_permission_code not in SOURCE_PERMISSIONS[source_type]
        or (not context.authorized_workspace and resource_id not in context.authorized_resource_ids)
        or SECURITY_RANK[security_level] > SECURITY_RANK[context.authorized_maximum_security_level]
    ):
        raise QualityDeniedError


def _require_read_authorization(context: RequestContext) -> None:
    """质量数据集是工作空间集合，资源级授权不能被提升为全量读取。"""

    if (
        context.audit_authorization is None
        or context.authorized_permission_code not in READ_PERMISSIONS
        or not context.authorized_workspace
    ):
        raise QualityDeniedError


def _with_superseded(
    sample: QualitySampleVersion,
    latest: QualitySampleVersion | None,
) -> QualitySampleVersion:
    """建立不可变版本链；旧样本仅从新数据集成员集合退出。"""

    if latest is None:
        return sample
    return replace(sample, supersedes_sample_version_id=latest.sample_version_id)


def _next_members(
    unit_of_work: QualityUnitOfWork,
    workspace_id: UUID,
    latest: QualityDatasetVersion | None,
    sample: QualitySampleVersion,
) -> tuple[QualitySampleVersion, ...]:
    """基于最新快照替换逻辑样本；tombstone 只负责移除。"""

    current = () if latest is None else _snapshot_samples(unit_of_work, workspace_id, latest)
    remaining = tuple(
        item for item in current if item.logical_sample_id != sample.logical_sample_id
    )
    members = remaining + ((sample,) if sample.operation == "upsert" else ())
    return tuple(sorted(members, key=lambda item: item.logical_sample_id.hex))


def _snapshot_samples(
    unit_of_work: QualityUnitOfWork,
    workspace_id: UUID,
    dataset: QualityDatasetVersion,
) -> tuple[QualitySampleVersion, ...]:
    snapshot = unit_of_work.quality.get_snapshot(workspace_id, dataset.dataset_version_id)
    if snapshot is None:
        raise QualityConflictError
    return snapshot.samples
