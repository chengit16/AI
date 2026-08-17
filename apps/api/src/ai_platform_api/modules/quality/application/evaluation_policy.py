"""冻结 P5-03 六层确定性评估策略和低基数原因码。"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from ai_platform_api.modules.quality.domain.evaluation import (
    QualityEvaluationLayer,
    QualityEvaluationPolicy,
    QualityLayerPolicy,
)

QUALITY_EVALUATION_LAYERS: tuple[QualityEvaluationLayer, ...] = (
    "retrieval",
    "citation",
    "model",
    "prompt",
    "workflow",
    "tool",
)
HARD_FAILURE_REASON_CODES = frozenset(
    {
        "unauthorized_access",
        "restricted_field_leakage",
        "invalid_citation",
        "unauthorized_tool_call",
    }
)
COMMON_REASON_CODES = frozenset(
    {
        "evaluation_failed",
        "evaluation_timeout",
        "evaluation_skipped",
    }
)
AGGREGATE_REASON_CODES = frozenset(
    {
        "sample_insufficient",
        "observation_failed",
        "observation_timeout",
        "observation_skipped",
        "score_below_threshold",
        "workspace_identity_drift",
        "dataset_identity_drift",
        "service_identity_drift",
        "release_identity_drift",
        "configuration_identity_drift",
        "evaluator_identity_drift",
        "sample_identity_drift",
        "batch_integrity_drift",
    }
)
LAYER_REASON_CODES: dict[QualityEvaluationLayer, frozenset[str]] = {
    "retrieval": frozenset({"retrieval_miss", "retrieval_low_relevance"}),
    "citation": frozenset({"citation_missing", "citation_mismatch"}),
    "model": frozenset({"answer_incorrect", "output_contract_violation"}),
    "prompt": frozenset({"prompt_instruction_loss", "prompt_injection_bypass"}),
    "workflow": frozenset({"workflow_incomplete", "workflow_transition_invalid"}),
    "tool": frozenset({"tool_call_failed", "tool_result_incorrect"}),
}


def _layer(
    layer: QualityEvaluationLayer,
    minimum_score_bps: int,
) -> QualityLayerPolicy:
    return QualityLayerPolicy(
        layer=layer,
        minimum_sample_count=2,
        minimum_score_bps=minimum_score_bps,
        allowed_reason_codes=(
            LAYER_REASON_CODES[layer] | COMMON_REASON_CODES | HARD_FAILURE_REASON_CODES
        ),
    )


def _policy_digest(layers: tuple[QualityLayerPolicy, ...]) -> str:
    document = {
        "policy_version_id": "55000000-0000-4000-8000-000000000503",
        "version_number": 1,
        "evaluator_version": "deterministic-layered-v1",
        "hard_failure_reason_codes": sorted(HARD_FAILURE_REASON_CODES),
        "layers": [
            {
                "layer": item.layer,
                "minimum_sample_count": item.minimum_sample_count,
                "minimum_score_bps": item.minimum_score_bps,
                "allowed_reason_codes": sorted(item.allowed_reason_codes),
            }
            for item in layers
        ],
    }
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


_LAYERS = (
    _layer("retrieval", 8_000),
    _layer("citation", 9_000),
    _layer("model", 8_000),
    _layer("prompt", 10_000),
    _layer("workflow", 9_000),
    _layer("tool", 10_000),
)
QUALITY_EVALUATION_POLICY = QualityEvaluationPolicy(
    policy_version_id=UUID("55000000-0000-4000-8000-000000000503"),
    version_number=1,
    evaluator_version="deterministic-layered-v1",
    layers=_LAYERS,
    hard_failure_reason_codes=HARD_FAILURE_REASON_CODES,
    policy_digest=_policy_digest(_LAYERS),
)
