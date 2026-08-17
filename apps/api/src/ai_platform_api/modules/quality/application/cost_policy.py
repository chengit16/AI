"""冻结 P5-05 成本组件、来源映射、对账原因和采集器版本。"""

from __future__ import annotations

from ai_platform_api.modules.quality.domain.costs import (
    CostComponent,
    CostSourceKind,
    CostUsageUnit,
)

COST_COLLECTOR_VERSION = "cost-attribution-v1"
COST_COMPONENTS: tuple[CostComponent, ...] = (
    "model",
    "retrieval",
    "ocr",
    "indexing",
    "embedding",
    "reranker",
    "tool",
)
SOURCE_COMPONENTS: dict[CostSourceKind, CostComponent] = {
    "model_attempt": "model",
    "retrieval_run": "retrieval",
    "ocr_attempt": "ocr",
    "index_build": "indexing",
    "embedding_batch": "embedding",
    "reranker_request": "reranker",
    "tool_attempt": "tool",
}
COMPONENT_UNITS: dict[CostComponent, frozenset[CostUsageUnit]] = {
    "model": frozenset({"token"}),
    "retrieval": frozenset({"request", "millisecond"}),
    "ocr": frozenset({"page"}),
    "indexing": frozenset({"chunk"}),
    "embedding": frozenset({"token", "chunk"}),
    "reranker": frozenset({"pair", "request"}),
    "tool": frozenset({"request", "millisecond"}),
}
DIFFERENCE_REASON_CODES = frozenset(
    {
        "billing_window_timing",
        "provider_rounding",
        "provider_discount",
        "provider_credit",
        "provider_tax",
    }
)
COST_REASON_CODES = frozenset(
    {
        "collector_identity_drift",
        "release_identity_drift",
        "price_version_mismatch",
        "currency_mismatch",
        "usage_not_run",
        "real_price_not_configured",
        "real_price_not_verified",
        "supplier_statement_not_configured",
        "supplier_statement_not_run",
        "supplier_statement_currency_mismatch",
        "supplier_statement_unexplained_difference",
        *DIFFERENCE_REASON_CODES,
    }
)

MAXIMUM_WINDOW_DAYS = 31
MAXIMUM_ENTRY_COUNT = 100_000
MAXIMUM_QUANTITY = 1_000_000_000_000
MAXIMUM_AMOUNT_MINOR = 9_000_000_000_000_000
