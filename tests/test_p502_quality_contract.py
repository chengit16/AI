"""校验 P5-02 质量样本契约、全合成基线和摘要投影。"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.quality.application.documents import (
    build_deletion_version,
    build_sample_version,
    normalize_capture,
    normalize_deletion,
)
from ai_platform_api.modules.quality.application.service import SOURCE_PERMISSIONS
from ai_platform_api.modules.quality.domain.models import (
    QualitySampleCapture,
    QualitySampleDeletion,
    QualitySecurityLevel,
    QualitySourceType,
)
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).parents[1]
CONTRACT_DIR = ROOT / "contracts" / "quality"
SCHEMA_PATH = CONTRACT_DIR / "quality-sample-baseline.v1.schema.json"
BASELINE_PATH = CONTRACT_DIR / "quality-sample-baseline.v1.json"
TRACE = TraceContext("5" * 32, "2" * 16)
CREATED_AT = datetime(2026, 8, 17, tzinfo=UTC)
TEXT_FIELDS = ("input_text", "output_text", "feedback_text", "correction_text")


def load_object(path: Path) -> dict[str, Any]:
    """读取对象型 JSON，避免契约测试接受错误顶层类型。"""

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def request_context(case: dict[str, Any]) -> RequestContext:
    """使用固定合成主体恢复完整服务端授权投影。"""

    actor_id = UUID("55000000-0000-4000-8000-000000000501")
    return replace(
        RequestContext.trusted(
            actor_id=actor_id,
            user_id=actor_id,
            workspace_id=UUID(cast(str, case["source_workspace_id"])),
            trace=TRACE,
            authentication_method="browser_session",
        ),
        authorized_permission_code=cast(str, case["permission_code"]),
        authorized_policy_decision_id=UUID("55000000-0000-4000-8000-000000000502"),
        authorized_policy_version=1,
        authorized_workspace=True,
        authorized_maximum_security_level=cast(
            QualitySecurityLevel,
            case["source_security_level"],
        ),
    )


def capture_document(case: dict[str, Any]) -> QualitySampleCapture:
    """把已通过 Schema 的固定案例映射到 Application 文档。"""

    return QualitySampleCapture(
        source_type=cast(QualitySourceType, case["source_type"]),
        source_workspace_id=UUID(cast(str, case["source_workspace_id"])),
        source_id=UUID(cast(str, case["source_id"])),
        source_version=cast(int, case["source_version"]),
        resource_id=UUID(cast(str, case["resource_id"])),
        signal_code=cast(str, case["signal_code"]),
        reason_codes=tuple(cast(list[str], case["reason_codes"])),
        input_text=cast(str | None, case["input_text"]),
        output_text=cast(str | None, case["output_text"]),
        feedback_text=cast(str | None, case["feedback_text"]),
        correction_text=cast(str | None, case["correction_text"]),
        source_security_level=cast(QualitySecurityLevel, case["source_security_level"]),
    )


def test_p502_baseline_matches_versioned_schema() -> None:
    schema = load_object(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        load_object(BASELINE_PATH)
    )


def test_p502_source_permissions_and_required_fields_match_application() -> None:
    baseline = load_object(BASELINE_PATH)
    contracts = {
        item["source_type"]: item
        for item in cast(list[dict[str, Any]], baseline["source_contracts"])
    }

    assert set(contracts) == set(SOURCE_PERMISSIONS)
    assert {
        source_type: frozenset({cast(str, contract["permission_code"])})
        for source_type, contract in contracts.items()
    } == SOURCE_PERMISSIONS
    assert {
        source_type: tuple(contract["required_text_fields"])
        for source_type, contract in contracts.items()
    } == {
        "run_failure": ("input_text",),
        "user_feedback": ("input_text", "output_text"),
        "human_correction": ("input_text", "output_text", "correction_text"),
    }


def test_p502_fixed_cases_produce_expected_digests_without_persisting_text() -> None:
    baseline = load_object(BASELINE_PATH)
    cases = cast(list[dict[str, Any]], baseline["capture_cases"])

    assert {case["source_type"] for case in cases} == set(SOURCE_PERMISSIONS)
    assert len({case["case_id"] for case in cases}) == len(cases)
    for case in cases:
        capture = normalize_capture(capture_document(case))
        sample = build_sample_version(request_context(case), capture, CREATED_AT)
        expected = cast(dict[str, str | None], case["expected_digests"])

        assert {
            "input_digest": sample.input_digest,
            "output_digest": sample.output_digest,
            "feedback_digest": sample.feedback_digest,
            "correction_digest": sample.correction_digest,
        } == expected
        serialized = repr(sample)
        for field_name in TEXT_FIELDS:
            value = cast(str | None, case[field_name])
            if value is not None:
                assert value not in serialized


def test_p502_deletion_fixture_builds_content_free_tombstone() -> None:
    baseline = load_object(BASELINE_PATH)
    case = cast(dict[str, Any], baseline["deletion_case"])
    deletion = normalize_deletion(
        QualitySampleDeletion(
            source_type=cast(QualitySourceType, case["source_type"]),
            source_workspace_id=UUID(cast(str, case["source_workspace_id"])),
            source_id=UUID(cast(str, case["source_id"])),
            source_version=cast(int, case["source_version"]),
            resource_id=UUID(cast(str, case["resource_id"])),
            reason_code=cast(str, case["reason_code"]),
            source_security_level=cast(
                QualitySecurityLevel,
                case["source_security_level"],
            ),
        )
    )
    sample = build_deletion_version(request_context(case), deletion, CREATED_AT)

    assert sample.operation == case["expected_operation"] == "deleted"
    assert (
        sample.input_digest,
        sample.output_digest,
        sample.feedback_digest,
        sample.correction_digest,
    ) == (None, None, None, None)
    assert baseline["external_evidence"] == {
        "real_online_feedback": "not_run",
        "real_model_quality": "not_run",
    }
