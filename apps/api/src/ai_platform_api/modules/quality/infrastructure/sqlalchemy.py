"""使用工作空间复合条件持久化不可变质量样本和数据集快照。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import cast
from uuid import UUID

from sqlalchemy import desc, func, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from ai_platform_api.modules.quality.domain.models import (
    QualityDatasetSnapshot,
    QualityDatasetVersion,
    QualitySampleOperation,
    QualitySampleVersion,
    QualitySecurityLevel,
    QualitySourceType,
)
from ai_platform_api.persistence.tables import (
    quality_dataset_members,
    quality_dataset_versions,
    quality_sample_versions,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyQualityRepository:
    """把每个读写条件绑定到可信工作空间，避免直接 ID 猜测泄漏。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_workspace(self, workspace_id: UUID) -> None:
        """串行化同空间的数据集版本分配，锁随当前事务提交或回滚释放。"""

        self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(str(workspace_id), 0)))
        )

    def get_by_source_version(
        self,
        workspace_id: UUID,
        source_type: QualitySourceType,
        source_id: UUID,
        source_version: int,
    ) -> QualitySampleVersion | None:
        row = (
            self._session.execute(
                select(quality_sample_versions).where(
                    quality_sample_versions.c.workspace_id == workspace_id,
                    quality_sample_versions.c.source_type == source_type,
                    quality_sample_versions.c.source_id == source_id,
                    quality_sample_versions.c.source_version == source_version,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _sample(row) if row is not None else None

    def get_latest_source(
        self,
        workspace_id: UUID,
        source_type: QualitySourceType,
        source_id: UUID,
    ) -> QualitySampleVersion | None:
        row = (
            self._session.execute(
                select(quality_sample_versions)
                .where(
                    quality_sample_versions.c.workspace_id == workspace_id,
                    quality_sample_versions.c.source_type == source_type,
                    quality_sample_versions.c.source_id == source_id,
                )
                .order_by(desc(quality_sample_versions.c.source_version))
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        return _sample(row) if row is not None else None

    def get_latest_dataset(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> QualityDatasetVersion | None:
        statement = (
            select(quality_dataset_versions)
            .where(quality_dataset_versions.c.workspace_id == workspace_id)
            .order_by(desc(quality_dataset_versions.c.version_number))
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).mappings().one_or_none()
        return _dataset(row) if row is not None else None

    def get_snapshot(
        self,
        workspace_id: UUID,
        dataset_version_id: UUID,
    ) -> QualityDatasetSnapshot | None:
        # 1. 先按工作空间读取数据集与触发样本，跨空间 ID 统一表现为不可见。
        dataset_row = (
            self._session.execute(
                select(quality_dataset_versions).where(
                    quality_dataset_versions.c.workspace_id == workspace_id,
                    quality_dataset_versions.c.dataset_version_id == dataset_version_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if dataset_row is None:
            return None
        dataset = _dataset(dataset_row)
        trigger_row = (
            self._session.execute(
                select(quality_sample_versions).where(
                    quality_sample_versions.c.workspace_id == workspace_id,
                    quality_sample_versions.c.sample_version_id
                    == dataset.trigger_sample_version_id,
                )
            )
            .mappings()
            .one()
        )
        # 2. 成员按冻结位置装配，Session 与数据库 Row 不逃逸到 Application 层。
        member_rows = (
            self._session.execute(
                select(quality_sample_versions)
                .select_from(
                    quality_dataset_members.join(
                        quality_sample_versions,
                        (
                            quality_sample_versions.c.sample_version_id
                            == quality_dataset_members.c.sample_version_id
                        )
                        & (
                            quality_sample_versions.c.workspace_id
                            == quality_dataset_members.c.workspace_id
                        ),
                    )
                )
                .where(
                    quality_dataset_members.c.workspace_id == workspace_id,
                    quality_dataset_members.c.dataset_version_id == dataset_version_id,
                )
                .order_by(quality_dataset_members.c.position)
            )
            .mappings()
            .all()
        )
        return QualityDatasetSnapshot(
            dataset,
            tuple(_sample(row) for row in member_rows),
            _sample(trigger_row),
        )

    def get_snapshot_by_trigger(
        self,
        workspace_id: UUID,
        sample_version_id: UUID,
    ) -> QualityDatasetSnapshot | None:
        dataset_id = self._session.scalar(
            select(quality_dataset_versions.c.dataset_version_id).where(
                quality_dataset_versions.c.workspace_id == workspace_id,
                quality_dataset_versions.c.trigger_sample_version_id == sample_version_id,
            )
        )
        if dataset_id is None:
            return None
        return self.get_snapshot(workspace_id, cast(UUID, dataset_id))

    def add_sample(self, sample: QualitySampleVersion) -> None:
        """只执行 INSERT；数据库 Trigger 会阻断任何后续改写。"""

        self._session.execute(
            insert(quality_sample_versions).values(
                sample_version_id=sample.sample_version_id,
                logical_sample_id=sample.logical_sample_id,
                workspace_id=sample.workspace_id,
                source_type=sample.source_type,
                source_id=sample.source_id,
                source_version=sample.source_version,
                resource_id=sample.resource_id,
                operation=sample.operation,
                signal_code=sample.signal_code,
                reason_codes=list(sample.reason_codes),
                source_digest=sample.source_digest,
                input_digest=sample.input_digest,
                output_digest=sample.output_digest,
                feedback_digest=sample.feedback_digest,
                correction_digest=sample.correction_digest,
                supersedes_sample_version_id=sample.supersedes_sample_version_id,
                authorized_permission_code=sample.authorized_permission_code,
                policy_decision_id=sample.policy_decision_id,
                policy_version=sample.policy_version,
                workspace_scope=sample.workspace_scope,
                department_scope_ids=list(sample.department_scope_ids),
                account_scope_ids=list(sample.account_scope_ids),
                resource_scope_ids=list(sample.resource_scope_ids),
                field_mask=list(sample.field_mask),
                maximum_security_level=sample.maximum_security_level,
                created_by_actor_id=sample.created_by_actor_id,
                created_at=sample.created_at,
            )
        )

    def add_dataset(
        self,
        dataset: QualityDatasetVersion,
        samples: tuple[QualitySampleVersion, ...],
    ) -> None:
        """原子插入快照和有序成员，成员数由 Application 独立复算。"""

        self._session.execute(
            insert(quality_dataset_versions).values(
                dataset_version_id=dataset.dataset_version_id,
                workspace_id=dataset.workspace_id,
                version_number=dataset.version_number,
                previous_dataset_version_id=dataset.previous_dataset_version_id,
                trigger_sample_version_id=dataset.trigger_sample_version_id,
                sample_count=dataset.sample_count,
                dataset_digest=dataset.dataset_digest,
                created_by_actor_id=dataset.created_by_actor_id,
                created_at=dataset.created_at,
            )
        )
        if samples:
            self._session.execute(
                insert(quality_dataset_members),
                [
                    {
                        "dataset_version_id": dataset.dataset_version_id,
                        "sample_version_id": sample.sample_version_id,
                        "logical_sample_id": sample.logical_sample_id,
                        "workspace_id": dataset.workspace_id,
                        "position": position,
                    }
                    for position, sample in enumerate(samples, start=1)
                ],
            )


class SqlAlchemyQualityUnitOfWork:
    """为质量样本、数据集版本和成员关系提供显式短事务。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._quality: SqlAlchemyQualityRepository | None = None

    @property
    def quality(self) -> SqlAlchemyQualityRepository:
        if self._quality is None:
            raise RuntimeError("Quality Unit of Work 尚未进入事务范围")
        return self._quality

    def __enter__(self) -> SqlAlchemyQualityUnitOfWork:
        if self._session is not None:
            raise RuntimeError("Quality Unit of Work 不允许嵌套事务")
        self._session = self._session_factory()
        self._quality = SqlAlchemyQualityRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is not None:
            if exc_type is not None:
                self._session.rollback()
            self._session.close()
        self._quality = None
        self._session = None

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Quality Unit of Work 尚未进入事务范围")
        self._session.commit()


def _sample(row: RowMapping) -> QualitySampleVersion:
    return QualitySampleVersion(
        sample_version_id=cast(UUID, row["sample_version_id"]),
        logical_sample_id=cast(UUID, row["logical_sample_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        source_type=cast(QualitySourceType, row["source_type"]),
        source_id=cast(UUID, row["source_id"]),
        source_version=cast(int, row["source_version"]),
        resource_id=cast(UUID, row["resource_id"]),
        operation=cast(QualitySampleOperation, row["operation"]),
        signal_code=cast(str, row["signal_code"]),
        reason_codes=tuple(cast(list[str], row["reason_codes"])),
        source_digest=cast(str, row["source_digest"]),
        input_digest=cast(str | None, row["input_digest"]),
        output_digest=cast(str | None, row["output_digest"]),
        feedback_digest=cast(str | None, row["feedback_digest"]),
        correction_digest=cast(str | None, row["correction_digest"]),
        supersedes_sample_version_id=cast(UUID | None, row["supersedes_sample_version_id"]),
        authorized_permission_code=cast(str, row["authorized_permission_code"]),
        policy_decision_id=cast(UUID, row["policy_decision_id"]),
        policy_version=cast(int, row["policy_version"]),
        workspace_scope=cast(bool, row["workspace_scope"]),
        department_scope_ids=tuple(cast(list[UUID], row["department_scope_ids"])),
        account_scope_ids=tuple(cast(list[UUID], row["account_scope_ids"])),
        resource_scope_ids=tuple(cast(list[UUID], row["resource_scope_ids"])),
        field_mask=tuple(cast(list[str], row["field_mask"])),
        maximum_security_level=cast(QualitySecurityLevel, row["maximum_security_level"]),
        created_by_actor_id=cast(UUID, row["created_by_actor_id"]),
        created_at=cast(datetime, row["created_at"]),
    )


def _dataset(row: RowMapping) -> QualityDatasetVersion:
    return QualityDatasetVersion(
        dataset_version_id=cast(UUID, row["dataset_version_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        version_number=cast(int, row["version_number"]),
        previous_dataset_version_id=cast(UUID | None, row["previous_dataset_version_id"]),
        trigger_sample_version_id=cast(UUID, row["trigger_sample_version_id"]),
        sample_count=cast(int, row["sample_count"]),
        dataset_digest=cast(str, row["dataset_digest"]),
        created_by_actor_id=cast(UUID, row["created_by_actor_id"]),
        created_at=cast(datetime, row["created_at"]),
    )
