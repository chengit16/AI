"""检查工作区契约相对指定 Git 基线的明显破坏性变化。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
CONTRACTS = ROOT / "contracts"
CONTRACT_DIRS = ("openapi", "domain", "policy", "sse", "events", "errors", "release")


def git_json(base_ref: str, relative_path: Path) -> dict[str, Any] | None:
    result = subprocess.run(
        ["git", "show", f"{base_ref}:{relative_path.as_posix()}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    loaded = json.loads(result.stdout)
    return loaded if isinstance(loaded, dict) else None


def compare_schema(old: Any, new: Any, location: str, errors: list[str]) -> None:
    if not isinstance(old, dict) or not isinstance(new, dict):
        return
    if old.get("type") != new.get("type") and "type" in old:
        errors.append(f"{location}: type 从 {old.get('type')} 变为 {new.get('type')}")
    old_enum = set(old.get("enum", []))
    new_enum = set(new.get("enum", []))
    if old_enum and not old_enum.issubset(new_enum):
        errors.append(f"{location}: enum 删除了 {sorted(old_enum - new_enum)}")
    old_required = set(old.get("required", []))
    new_required = set(new.get("required", []))
    if not new_required.issubset(old_required):
        errors.append(f"{location}: 新增必填字段 {sorted(new_required - old_required)}")
    if not old_required.issubset(new_required):
        errors.append(f"{location}: 原必填字段变为可选 {sorted(old_required - new_required)}")
    old_properties = old.get("properties", {})
    new_properties = new.get("properties", {})
    if isinstance(old_properties, dict) and isinstance(new_properties, dict):
        for name, old_property in old_properties.items():
            if name not in new_properties:
                errors.append(f"{location}: 删除属性 {name}")
                continue
            compare_schema(old_property, new_properties[name], f"{location}.{name}", errors)
    if "items" in old:
        if "items" not in new:
            errors.append(f"{location}: 删除数组元素约束")
        else:
            compare_schema(old["items"], new["items"], f"{location}[]", errors)
    if (
        old.get("additionalProperties", True) is not False
        and new.get("additionalProperties") is False
    ):
        errors.append(f"{location}: 禁止了原先允许的附加属性")


def compare_openapi(old: dict[str, Any], new: dict[str, Any], errors: list[str]) -> None:
    old_paths = old.get("paths", {})
    new_paths = new.get("paths", {})
    for route, old_path in old_paths.items():
        if route not in new_paths:
            errors.append(f"OpenAPI: 删除路径 {route}")
            continue
        for method, old_operation in old_path.items():
            if method not in new_paths[route]:
                errors.append(f"OpenAPI: 删除操作 {method.upper()} {route}")
                continue
            new_operation = new_paths[route][method]
            old_responses = old_operation.get("responses", {})
            new_responses = new_operation.get("responses", {})
            for status_code in old_responses:
                if status_code not in new_responses:
                    errors.append(f"OpenAPI: {method.upper()} {route} 删除响应 {status_code}")
    old_schemas = old.get("components", {}).get("schemas", {})
    new_schemas = new.get("components", {}).get("schemas", {})
    for name, old_schema in old_schemas.items():
        if name not in new_schemas:
            errors.append(f"OpenAPI: 删除 Schema {name}")
            continue
        compare_schema(old_schema, new_schemas[name], f"OpenAPI.{name}", errors)


def compare_error_catalog(old: dict[str, Any], new: dict[str, Any], errors: list[str]) -> None:
    old_codes = {item["code"] for item in old.get("errors", [])}
    new_codes = {item["code"] for item in new.get("errors", [])}
    if not old_codes.issubset(new_codes):
        errors.append(f"错误码目录: 删除错误码 {sorted(old_codes - new_codes)}")


def collect_errors(base_ref: str) -> list[str]:
    errors: list[str] = []
    for directory in CONTRACT_DIRS:
        base_files_result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", base_ref, f"contracts/{directory}"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if base_files_result.returncode == 0:
            for base_file in base_files_result.stdout.splitlines():
                if base_file.endswith(".json") and not (ROOT / base_file).exists():
                    errors.append(f"删除契约文件 {base_file}")
        for current_path in sorted((CONTRACTS / directory).glob("*.json")):
            relative_path = current_path.relative_to(ROOT)
            old = git_json(base_ref, relative_path)
            if old is None:
                continue
            loaded = json.loads(current_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                continue
            if directory == "openapi":
                compare_openapi(old, loaded, errors)
            elif current_path.name == "catalog.v1.json":
                compare_error_catalog(old, loaded, errors)
            else:
                compare_schema(old, loaded, relative_path.as_posix(), errors)
    return errors


def main() -> int:
    base_ref = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    errors = collect_errors(base_ref)
    if not errors:
        print(f"契约兼容性检查通过, 基线: {base_ref}")
        return 0
    for error in errors:
        print(error)
    print("如需破坏性调整, 请发布新主版本并提供迁移和回滚方案。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
