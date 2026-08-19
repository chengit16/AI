"""验证运行配置 PostgreSQL Adapter 对历史组件快照的读取兼容边界。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

from ai_platform_api.modules.model_gateway.infrastructure.runtime_sqlalchemy import (
    SqlAlchemyRuntimeConfigurationRepository,
)
from sqlalchemy.orm import Session


def test_list_configurations_preserves_unversioned_legacy_interfaces() -> None:
    """早期快照缺少后置接口字段时，整张运行配置列表仍应可读取。"""

    runtime_id = UUID("10000000-0000-4000-8000-000000000661")
    row = SimpleNamespace(
        runtime_config_version_id=runtime_id,
        version_number=1,
        display_name="合成历史运行配置",
        content_hash="a" * 64,
        system_prompt_template="只依据合成证据回答。",
        system_prompt_hash="b" * 64,
        component_versions={
            "chunking": "recursive-cjk-v1",
            "embedding": "deterministic-hash-1024-v1",
            "index_schema": "index-v1",
            "reranker": "deterministic-lexical-reranker-v1",
            "retrieval": "hybrid-rrf-v1",
            "source_ranking": "source-priority-v1",
            "safety": "rag-safety-v2",
        },
        attempt_timeout_ms=500,
        total_timeout_ms=2_000,
        max_attempts_per_route=1,
        max_prompt_characters=32_000,
        max_output_tokens=2_048,
        max_response_characters=64_000,
        circuit_failure_threshold=3,
        circuit_recovery_ms=30_000,
        rule_degradation_message="合成模型暂不可用",
        max_estimated_cost_microunits=5_000_000,
        created_by_account_id=UUID("10000000-0000-4000-8000-000000000662"),
        created_at=datetime(2026, 8, 14, tzinfo=UTC),
    )
    session = MagicMock(spec=Session)
    session.execute.side_effect = ([row], [])

    configurations = SqlAlchemyRuntimeConfigurationRepository(session).list_configurations()

    assert len(configurations) == 1
    assert configurations[0].runtime_config_version_id == runtime_id
    assert configurations[0].components.data_source_interface == "legacy-unversioned"
    assert configurations[0].components.relevance_grader_interface == "legacy-unversioned"
    assert configurations[0].components.multimodal_router_interface == "legacy-unversioned"


def test_get_configuration_tolerates_sparse_legacy_component_snapshot() -> None:
    """当前发布指针读取稀疏旧快照时，不应因缺失或废弃组件字段返回内部错误。"""

    runtime_id = UUID("10000000-0000-4000-8000-000000000671")
    row = SimpleNamespace(
        runtime_config_version_id=runtime_id,
        version_number=1,
        display_name="合成稀疏历史配置",
        content_hash="c" * 64,
        system_prompt_template="只依据合成证据回答。",
        system_prompt_hash="d" * 64,
        component_versions={
            "chunking": "recursive-cjk-v0",
            "embedding": "deterministic-hash-1024-v0",
            "deprecated_parser": "synthetic-parser-v0",
        },
        attempt_timeout_ms=500,
        total_timeout_ms=2_000,
        max_attempts_per_route=1,
        max_prompt_characters=32_000,
        max_output_tokens=2_048,
        max_response_characters=64_000,
        circuit_failure_threshold=3,
        circuit_recovery_ms=30_000,
        rule_degradation_message="合成模型暂不可用",
        max_estimated_cost_microunits=5_000_000,
        created_by_account_id=UUID("10000000-0000-4000-8000-000000000672"),
        created_at=datetime(2026, 8, 14, tzinfo=UTC),
    )
    configuration_result = MagicMock()
    configuration_result.one_or_none.return_value = row
    session = MagicMock(spec=Session)
    session.execute.side_effect = (configuration_result, [])

    configuration = SqlAlchemyRuntimeConfigurationRepository(session).get_configuration(runtime_id)

    assert configuration is not None
    assert configuration.components.chunking == "recursive-cjk-v0"
    assert configuration.components.embedding == "deterministic-hash-1024-v0"
    assert configuration.components.index_schema == "legacy-unversioned"
    assert configuration.components.safety == "legacy-unversioned"
    assert configuration.components.multimodal_router_interface == "legacy-unversioned"
