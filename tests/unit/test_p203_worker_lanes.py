"""验证 P2-03 Worker Lane 路由和并发配置边界。"""

import pytest
from ai_platform_backend.ingestion.domain import ingestion_lane_for_source
from ai_platform_worker.config import WorkerProcessLane, WorkerSettings
from pydantic import ValidationError


@pytest.mark.parametrize(
    ("source_name", "expected"),
    [
        ("synthetic.txt", "parsing"),
        ("synthetic.md", "parsing"),
        ("synthetic.docx", "parsing"),
        ("synthetic.PDF", "ocr"),
        ("synthetic.jpeg", "ocr"),
        ("synthetic.tiff", "ocr"),
    ],
)
def test_ingestion_lane_is_deterministic_from_source_name(
    source_name: str,
    expected: str,
) -> None:
    assert ingestion_lane_for_source(source_name) == expected


@pytest.mark.parametrize(
    ("lane", "expected"),
    [
        ("control", 2),
        ("parsing", 3),
        ("ocr", 1),
        ("embedding", 1),
        ("indexing", 4),
        ("scheduler", 1),
    ],
)
def test_worker_process_uses_only_its_lane_concurrency(
    lane: WorkerProcessLane,
    expected: int,
) -> None:
    settings = WorkerSettings(
        worker_lane=lane,
        control_worker_concurrency=2,
        parsing_worker_concurrency=3,
        ocr_worker_concurrency=1,
        embedding_worker_concurrency=1,
        indexing_worker_concurrency=4,
    )

    assert settings.worker_concurrency == expected


@pytest.mark.parametrize("value", [0, 9])
def test_worker_lane_concurrency_rejects_unsafe_bounds(value: int) -> None:
    with pytest.raises(ValidationError, match="Worker Lane 并发必须位于 1 到 8 之间"):
        WorkerSettings(ocr_worker_concurrency=value)
