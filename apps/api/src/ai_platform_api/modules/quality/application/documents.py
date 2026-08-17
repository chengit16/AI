"""规范质量采集文档并构建只含摘要的不可变领域事实。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from uuid import UUID, uuid5

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.quality.application.errors import (
    QualityDeniedError,
    QualityValidationError,
)
from ai_platform_api.modules.quality.domain.models import (
    QualityDatasetVersion,
    QualitySampleCapture,
    QualitySampleDeletion,
    QualitySampleOperation,
    QualitySampleVersion,
    QualitySourceType,
)

QUALITY_NAMESPACE = UUID("55000000-0000-4000-8000-000000000502")
CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
MAX_TEXT_CHARACTERS = 128_000


def normalize_capture(capture: QualitySampleCapture) -> QualitySampleCapture:
    """规范低基数原因码，并限制只在内存短暂存在的正文大小。"""

    # 1. 先约束版本、低基数码和瞬时正文大小，避免无界内容进入后续摘要阶段。
    if capture.source_version < 1 or CODE_PATTERN.fullmatch(capture.signal_code) is None:
        raise QualityValidationError
    reasons = tuple(sorted(set(capture.reason_codes)))
    if len(reasons) > 16 or any(CODE_PATTERN.fullmatch(code) is None for code in reasons):
        raise QualityValidationError
    texts = (
        capture.input_text,
        capture.output_text,
        capture.feedback_text,
        capture.correction_text,
    )
    if any(
        value is not None and (not value.strip() or len(value) > MAX_TEXT_CHARACTERS)
        for value in texts
    ) or all(value is None for value in texts):
        raise QualityValidationError
    # 2. 再按来源类型校验必需正文，防止空失败或空修正成为有效运营样本。
    if capture.source_type == "run_failure" and capture.input_text is None:
        raise QualityValidationError
    if capture.source_type == "user_feedback" and (
        capture.input_text is None or capture.output_text is None
    ):
        raise QualityValidationError
    if capture.source_type == "human_correction" and (
        capture.input_text is None or capture.output_text is None or capture.correction_text is None
    ):
        raise QualityValidationError
    return QualitySampleCapture(
        source_type=capture.source_type,
        source_workspace_id=capture.source_workspace_id,
        source_id=capture.source_id,
        source_version=capture.source_version,
        resource_id=capture.resource_id,
        signal_code=capture.signal_code,
        reason_codes=reasons,
        input_text=capture.input_text,
        output_text=capture.output_text,
        feedback_text=capture.feedback_text,
        correction_text=capture.correction_text,
        source_security_level=capture.source_security_level,
    )


def normalize_deletion(deletion: QualitySampleDeletion) -> QualitySampleDeletion:
    """删除传播只接受单调正版本和一个稳定低基数原因码。"""

    if deletion.source_version < 1 or CODE_PATTERN.fullmatch(deletion.reason_code) is None:
        raise QualityValidationError
    return deletion


def build_sample_version(
    context: RequestContext,
    capture: QualitySampleCapture,
    created_at: datetime,
) -> QualitySampleVersion:
    """先生成各正文摘要，再用低敏元数据形成来源版本内容身份。"""

    digests = (
        _text_digest(capture.input_text),
        _text_digest(capture.output_text),
        _text_digest(capture.feedback_text),
        _text_digest(capture.correction_text),
    )
    return _sample_version(
        context,
        source_type=capture.source_type,
        source_id=capture.source_id,
        source_version=capture.source_version,
        resource_id=capture.resource_id,
        operation="upsert",
        signal_code=capture.signal_code,
        reason_codes=capture.reason_codes,
        digests=digests,
        created_at=created_at,
    )


def build_deletion_version(
    context: RequestContext,
    deletion: QualitySampleDeletion,
    created_at: datetime,
) -> QualitySampleVersion:
    """构建不含正文摘要的 tombstone，来源身份仍可确定性去重。"""

    return _sample_version(
        context,
        source_type=deletion.source_type,
        source_id=deletion.source_id,
        source_version=deletion.source_version,
        resource_id=deletion.resource_id,
        operation="deleted",
        signal_code="deleted",
        reason_codes=(deletion.reason_code,),
        digests=(None, None, None, None),
        created_at=created_at,
    )


def build_dataset_version(
    context: RequestContext,
    previous: QualityDatasetVersion | None,
    trigger: QualitySampleVersion,
    samples: tuple[QualitySampleVersion, ...],
    created_at: datetime,
) -> QualityDatasetVersion:
    """按稳定成员顺序生成单调数据集版本及内容摘要。"""

    version = 1 if previous is None else previous.version_number + 1
    digest = _document_digest(
        {
            "workspace_id": str(context.workspace_id),
            "members": [str(item.sample_version_id) for item in samples],
        }
    )
    dataset_id = uuid5(QUALITY_NAMESPACE, f"dataset:{context.workspace_id}:{version}:{digest}")
    return QualityDatasetVersion(
        dataset_version_id=dataset_id,
        workspace_id=context.workspace_id,
        version_number=version,
        previous_dataset_version_id=(previous.dataset_version_id if previous is not None else None),
        trigger_sample_version_id=trigger.sample_version_id,
        sample_count=len(samples),
        dataset_digest=digest,
        created_by_actor_id=context.actor_id,
        created_at=created_at,
    )


def _sample_version(
    context: RequestContext,
    *,
    source_type: QualitySourceType,
    source_id: UUID,
    source_version: int,
    resource_id: UUID,
    operation: QualitySampleOperation,
    signal_code: str,
    reason_codes: tuple[str, ...],
    digests: tuple[str | None, str | None, str | None, str | None],
    created_at: datetime,
) -> QualitySampleVersion:
    """统一生成 upsert 与 tombstone 身份，授权投影来自可信上下文。"""

    input_digest, output_digest, feedback_digest, correction_digest = digests
    source_digest = _document_digest(
        {
            "source_type": source_type,
            "source_id": str(source_id),
            "source_version": source_version,
            "operation": operation,
            "signal_code": signal_code,
            "reason_codes": reason_codes,
            "input_digest": input_digest,
            "output_digest": output_digest,
            "feedback_digest": feedback_digest,
            "correction_digest": correction_digest,
        }
    )
    logical_id = uuid5(
        QUALITY_NAMESPACE,
        f"logical:{context.workspace_id}:{source_type}:{source_id}",
    )
    authorization = context.audit_authorization
    if authorization is None:
        raise QualityDeniedError
    return QualitySampleVersion(
        sample_version_id=uuid5(QUALITY_NAMESPACE, f"version:{logical_id}:{source_digest}"),
        logical_sample_id=logical_id,
        workspace_id=context.workspace_id,
        source_type=source_type,
        source_id=source_id,
        source_version=source_version,
        resource_id=resource_id,
        operation=operation,
        signal_code=signal_code,
        reason_codes=reason_codes,
        source_digest=source_digest,
        input_digest=input_digest,
        output_digest=output_digest,
        feedback_digest=feedback_digest,
        correction_digest=correction_digest,
        supersedes_sample_version_id=None,
        authorized_permission_code=authorization.permission_code,
        policy_decision_id=authorization.policy_decision_id,
        policy_version=authorization.policy_version,
        workspace_scope=context.authorized_workspace,
        department_scope_ids=_sorted_uuids(context.authorized_department_ids),
        account_scope_ids=_sorted_uuids(context.authorized_account_ids),
        resource_scope_ids=_sorted_uuids(context.authorized_resource_ids),
        field_mask=tuple(sorted(context.authorized_field_mask)),
        maximum_security_level=context.authorized_maximum_security_level,
        created_by_actor_id=context.actor_id,
        created_at=created_at,
    )


def _text_digest(value: str | None) -> str | None:
    return None if value is None else hashlib.sha256(value.encode()).hexdigest()


def _document_digest(document: object) -> str:
    payload = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _sorted_uuids(values: frozenset[UUID]) -> tuple[UUID, ...]:
    return tuple(sorted(values, key=lambda item: item.hex))
