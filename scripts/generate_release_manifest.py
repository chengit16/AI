"""从构建系统提供的不可变元数据生成并校验 ReleaseManifest V1。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).parents[1]
API_SOURCE = ROOT / "apps" / "api" / "src"
# 脚本需要在尚未安装项目包的构建容器中运行，因此显式加载仓库内 API 源码。
sys.path.insert(0, str(API_SOURCE))

from ai_platform_api.modules.release.application.manifest import (  # noqa: E402
    ReleaseManifestService,
)
from ai_platform_api.modules.release.domain.models import (  # noqa: E402
    CompatibilityMatrix,
    ComponentRule,
    ComponentVersion,
    DatabaseVersion,
    ImageArtifact,
    ManifestInputs,
    RuntimeRule,
    RuntimeVersions,
)

DEFAULT_MATRIX = ROOT / "contracts" / "release" / "compatibility-matrix.v1.json"


def load_json(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], loaded)


def parse_inputs(document: dict[str, Any]) -> ManifestInputs:
    database = document["database"]
    runtimes = document["runtimes"]
    return ManifestInputs(
        release_version=str(document["release_version"]),
        compatibility_matrix_version=str(document["compatibility_matrix_version"]),
        components=tuple(
            ComponentVersion(
                name=str(item["name"]),
                version=str(item["version"]),
                source_digest=str(item["source_digest"]),
            )
            for item in document["components"]
        ),
        database=DatabaseVersion(schema_revision=str(database["schema_revision"])),
        runtimes=RuntimeVersions(node=str(runtimes["node"]), python=str(runtimes["python"])),
        images=tuple(
            ImageArtifact(
                name=str(item["name"]),
                reference=str(item["reference"]),
                digest=str(item["digest"]),
                platforms=tuple(str(platform) for platform in item["platforms"]),
            )
            for item in document["images"]
        ),
    )


def parse_matrix(document: dict[str, Any]) -> CompatibilityMatrix:
    return CompatibilityMatrix(
        schema_version=int(document["schema_version"]),
        matrix_version=str(document["matrix_version"]),
        manifest_schema_version=int(document["manifest_schema_version"]),
        component_rules=tuple(
            ComponentRule(
                name=str(item["name"]),
                minimum_version=str(item["minimum_version"]),
                maximum_exclusive_version=str(item["maximum_exclusive_version"]),
            )
            for item in document["component_rules"]
        ),
        database_revisions=tuple(str(item) for item in document["database_revisions"]),
        runtime_rules=tuple(
            RuntimeRule(name=str(item["name"]), version_prefix=str(item["version_prefix"]))
            for item in document["runtime_rules"]
        ),
        required_images=tuple(str(item) for item in document["required_images"]),
    )


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
