"""从构建系统提供的不可变元数据生成并校验 ReleaseManifest V1。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
API_SOURCE = ROOT / "apps" / "api" / "src"
# 脚本需要在尚未安装项目包的构建容器中运行，因此显式加载仓库内 API 源码。
sys.path.insert(0, str(API_SOURCE))

from ai_platform_api.modules.release.application import (  # noqa: E402
    serialization as release_serialization,
)
from ai_platform_api.modules.release.application.manifest import (  # noqa: E402
    ReleaseManifestService,
)

load_json = release_serialization.load_json
parse_inputs = release_serialization.parse_inputs
parse_matrix = release_serialization.parse_matrix

DEFAULT_MATRIX = ROOT / "contracts" / "release" / "compatibility-matrix.v1.json"


def stable_json(document: dict[str, object]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True, help="构建系统生成的不可变元数据")
    parser.add_argument("--output", type=Path, required=True, help="发布清单输出路径")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX, help="兼容矩阵路径")
    parser.add_argument("--check", action="store_true", help="只检查目标清单是否需要重新生成")
    arguments = parser.parse_args()

    service = ReleaseManifestService()
    manifest = service.build(parse_inputs(load_json(arguments.inputs)))
    matrix = parse_matrix(load_json(arguments.matrix))
    result = service.validate(manifest, matrix)
    if not result.compatible:
        print("发布组合不兼容:")
        print("\n".join(f"- {reason}" for reason in result.reasons))
        return 1
    content = stable_json(manifest.to_dict())
    if arguments.check:
        if (
            not arguments.output.is_file()
            or arguments.output.read_text(encoding="utf-8") != content
        ):
            print(f"发布清单需要重新生成: {arguments.output}")
            return 1
        print(f"发布清单漂移检查通过: {arguments.output}")
        return 0
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(content, encoding="utf-8")
    print(f"已生成兼容的发布清单: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
