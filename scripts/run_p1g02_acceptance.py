"""按版本化清单运行 P1G-02 联合验收测试集合。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).parents[1]
DEFAULT_MANIFEST = ROOT / "tests/fixtures/e2e/p1g02-acceptance.v1.json"


def load_test_node_ids(path: Path) -> tuple[str, ...]:
    """按清单顺序去重测试节点，保留一个场景映射多个安全边界的能力。"""

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("synthetic") is not True:
        raise ValueError("P1G-02 验收清单必须是全合成对象")
    required_node_ids = cast(list[str], document.get("required_test_node_ids"))
    cases = cast(list[dict[str, Any]], document.get("cases"))
    ordered: list[str] = []
    seen: set[str] = set()
    scenario_node_ids = (
        node_id for case in cases for node_id in cast(list[str], case["test_node_ids"])
    )
    for node_id in [*required_node_ids, *scenario_node_ids]:
        if node_id not in seen:
            ordered.append(node_id)
            seen.add(node_id)
    if not ordered:
        raise ValueError("P1G-02 验收清单不能为空")
    return tuple(ordered)


def main() -> int:
    """先校验清单契约，再以当前 Python 环境运行固定 pytest 节点。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="验收清单路径")
    arguments = parser.parse_args()
    node_ids = load_test_node_ids(arguments.manifest)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_p1g02_acceptance_manifest.py",
        "tests/integration/test_p1g02_dataset_postgres.py",
        *node_ids,
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
