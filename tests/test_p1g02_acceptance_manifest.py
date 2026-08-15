"""校验 P1G-02 联合验收清单覆盖固定场景且只引用真实测试节点。"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).parents[1]
DATASET_PATH = ROOT / "tests/fixtures/e2e/p1g01-v1.json"
MANIFEST_PATH = ROOT / "tests/fixtures/e2e/p1g02-acceptance.v1.json"


def load_object(path: Path) -> dict[str, Any]:
    """读取对象型 JSON 并让错误结构在收集期直接可见。"""

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def _test_functions(path: Path) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def test_p1g02_manifest_covers_each_fixed_scenario_once() -> None:
    dataset = load_object(DATASET_PATH)
    manifest = load_object(MANIFEST_PATH)
    expected = {case["scenario_key"] for case in dataset["scenarios"]}
    actual = [case["scenario_key"] for case in manifest["cases"]]

    assert manifest["schema_version"] == 1
    assert manifest["suite_version"] == "p1g02-v1"
    assert manifest["dataset_version"] == dataset["dataset_version"]
    assert manifest["synthetic"] is True
    assert manifest["required_test_node_ids"]
    assert len(actual) == len(set(actual))
    assert set(actual) == expected
    assert all(case["test_node_ids"] for case in manifest["cases"])


def test_p1g02_manifest_only_references_existing_pytest_functions() -> None:
    manifest = load_object(MANIFEST_PATH)
    checked_files: dict[Path, set[str]] = {}
    required_node_ids = cast(list[str], manifest["required_test_node_ids"])
    scenario_node_ids = [
        node_id
        for case in cast(list[dict[str, Any]], manifest["cases"])
        for node_id in cast(list[str], case["test_node_ids"])
    ]

    for node_id in [*required_node_ids, *scenario_node_ids]:
        relative_path, separator, function_name = node_id.partition("::")
        assert separator == "::", node_id
        assert relative_path.startswith(("tests/", "apps/api/tests/")), node_id
        path = ROOT / relative_path
        assert path.is_file(), node_id
        functions = checked_files.setdefault(path, _test_functions(path))
        assert function_name in functions, node_id
