"""校验统一权限资源注册表，并生成 React 只读消费产物。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).parents[1]
API_SOURCE = ROOT / "apps/api/src"
BACKEND_SOURCE = ROOT / "packages/backend/src"
# 构建容器只复制源码，生成器必须在项目包尚未安装时也能装载领域校验器。
sys.path[:0] = [str(API_SOURCE), str(BACKEND_SOURCE)]

from ai_platform_api.modules.authorization.application.resources import (  # noqa: E402
    load_resource_registry,
)

REGISTRY = ROOT / "contracts/authorization/resource-registry.v1.json"
OPENAPI = ROOT / "contracts/openapi/platform-api.v1.json"
OUTPUT = ROOT / "apps/web/src/config/resourceRegistry.generated.ts"
PRETTIER = ROOT / "apps/web/node_modules/.bin/prettier"
PRETTIER_CONFIG = ROOT / "apps/web/.prettierrc.json"


def load_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def openapi_operations(document: dict[str, Any]) -> dict[tuple[str, str], str]:
    operations: dict[tuple[str, str], str] = {}
    for path, path_item in document.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() not in {"delete", "get", "patch", "post", "put"}:
                continue
            operations[(method.upper(), path)] = operation["operationId"]
    return operations


def registry_openapi_violations() -> tuple[str, ...]:
    registry = load_resource_registry(REGISTRY)
    expected = openapi_operations(load_json(OPENAPI))
    actual: dict[tuple[str, str], str] = {
        (resource.method, resource.path_pattern): resource.operation_id
        for resource in registry.api_resources
        if resource.status == "active"
    }
    violations: list[str] = []
    for key in sorted(expected.keys() - actual.keys()):
        violations.append(f"OpenAPI 操作未注册: {key[0]} {key[1]}")
    for key in sorted(actual.keys() - expected.keys()):
        violations.append(f"注册表接口不存在于 OpenAPI: {key[0]} {key[1]}")
    for key in sorted(expected.keys() & actual.keys()):
        if expected[key] != actual[key]:
            violations.append(
                f"operation_id 不一致: {key[0]} {key[1]}, "
                f"OpenAPI={expected[key]}, registry={actual[key]}"
            )
    return tuple(violations)


def generated_typescript() -> str:
    registry = load_json(REGISTRY)
    serialized = json.dumps(registry, ensure_ascii=False, indent=2)
    return (
        "// 由 scripts/generate_resource_registry.py 自动生成, 请勿手工修改。\n"
        f"export const resourceRegistry = {serialized} as const;\n\n"
        "export type PageResource = (typeof resourceRegistry.page_resources)[number];\n"
        "export type MenuResource = (typeof resourceRegistry.menus)[number];\n"
    )


def format_typescript(path: Path) -> None:
    if not PRETTIER.is_file():
        raise FileNotFoundError("缺少固定 Prettier, 请先执行 pnpm install --frozen-lockfile")
    subprocess.run(
        [str(PRETTIER), "--config", str(PRETTIER_CONFIG), "--write", str(path)],
        cwd=ROOT,
        check=True,
    )


def render(path: Path) -> None:
    violations = registry_openapi_violations()
    if violations:
        raise ValueError("\n".join(violations))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generated_typescript(), encoding="utf-8")
    format_typescript(path)


def check() -> bool:
    with tempfile.TemporaryDirectory(prefix="ai-platform-resource-registry-") as directory:
        candidate = Path(directory) / OUTPUT.name
        render(candidate)
        return OUTPUT.is_file() and OUTPUT.read_text(encoding="utf-8") == candidate.read_text(
            encoding="utf-8"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查注册表与生成产物漂移")
    arguments = parser.parse_args()
    if arguments.check:
        if not check():
            print(f"权限资源生成产物需要更新: {OUTPUT.relative_to(ROOT)}")
            return 1
        print("权限资源注册表、OpenAPI 覆盖和 React 生成产物检查通过")
        return 0
    render(OUTPUT)
    print(f"已生成权限资源 React 产物: {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
