"""从冻结 OpenAPI 生成 React 与 Python 消费类型。"""

from __future__ import annotations

import argparse
import json
import keyword
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
OPENAPI = ROOT / "contracts" / "openapi" / "platform-api.v1.json"
OPENAPI_TYPESCRIPT = ROOT / "node_modules" / ".bin" / "openapi-typescript"
PRETTIER = ROOT / "apps" / "web" / "node_modules" / ".bin" / "prettier"
TYPESCRIPT_OUTPUT = ROOT / "apps" / "web" / "src" / "api" / "generated" / "platform-api.v1.ts"
PYTHON_OUTPUT = (
    ROOT / "packages" / "contracts" / "src" / "ai_platform_contracts" / "platform_api_v1.py"
)


def load_openapi(path: Path = OPENAPI) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError("OpenAPI 顶层必须是对象")
    return document


def python_type(schema: object) -> str:
    if not isinstance(schema, dict):
        return "object"
    reference = schema.get("$ref")
    if isinstance(reference, str):
        return reference.rsplit("/", maxsplit=1)[-1]
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        return " | ".join(dict.fromkeys(python_type(item) for item in any_of))
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        values = ", ".join(
            json.dumps(value, ensure_ascii=False) if isinstance(value, str) else repr(value)
            for value in enum
        )
        return f"typing.Literal[{values}]"
    schema_type = schema.get("type")
    if schema_type == "string":
        return "str"
    if schema_type == "integer":
        return "int"
    if schema_type == "number":
        return "float"
    if schema_type == "boolean":
        return "bool"
    if schema_type == "null":
        return "None"
    if schema_type == "array":
        return f"list[{python_type(schema.get('items'))}]"
    if schema_type == "object":
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return f"dict[str, {python_type(additional)}]"
        return "dict[str, object]"
    return "object"


def require_identifier(value: str, *, kind: str) -> None:
    if not value.isidentifier() or keyword.iskeyword(value):
        raise ValueError(f"{kind} 不是合法 Python 标识符: {value}")


def generate_python(document: dict[str, Any]) -> str:
    schemas = document.get("components", {}).get("schemas", {})
    if not isinstance(schemas, dict):
        raise TypeError("OpenAPI components.schemas 必须是对象")
    lines = [
        '"""由 scripts/generate_contract_types.py 自动生成。请勿手工修改。"""',
        "",
        "from __future__ import annotations",
        "",
        "import typing",
        "",
        "",
    ]
    for name in sorted(schemas):
        require_identifier(name, kind="Schema 名称")
        schema = schemas[name]
        if not isinstance(schema, dict):
            raise TypeError(f"Schema 必须是对象: {name}")
        if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
            lines.extend([f"{name}: typing.TypeAlias = {python_type(schema)}", "", ""])
            continue
        required = set(schema.get("required", []))
        lines.append(f"class {name}(typing.TypedDict):")
        properties = schema["properties"]
        if not properties:
            lines.append("    pass")
        for field_name in sorted(properties):
            require_identifier(field_name, kind=f"{name} 字段")
            annotation = python_type(properties[field_name])
            if field_name not in required:
                annotation = f"typing.NotRequired[{annotation}]"
            lines.append(f"    {field_name}: {annotation}")
        lines.extend(["", ""])
    return "\n".join(lines).rstrip() + "\n"


def generate_typescript(output: Path) -> None:
    missing = [path.name for path in (OPENAPI_TYPESCRIPT, PRETTIER) if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            f"缺少固定生成工具 {', '.join(missing)}。请先执行 pnpm install --frozen-lockfile"
        )
    subprocess.run(
        [
            str(OPENAPI_TYPESCRIPT),
            str(OPENAPI),
            "--immutable",
            "--alphabetize",
            "--export-type",
            "-o",
            str(output),
        ],
        cwd=ROOT,
        check=True,
    )
    # 生成器原始缩进与项目 Prettier 规则不同，写入前统一格式化以避免提交后再产生漂移。
    subprocess.run([str(PRETTIER), "--write", str(output)], cwd=ROOT, check=True)


def stale_outputs() -> list[Path]:
    with tempfile.TemporaryDirectory(prefix="ai-platform-contracts-") as directory:
        generated_typescript = Path(directory) / "platform-api.v1.ts"
        generate_typescript(generated_typescript)
        expected = {
            TYPESCRIPT_OUTPUT: generated_typescript.read_text(encoding="utf-8"),
            PYTHON_OUTPUT: generate_python(load_openapi()),
        }
    return [
        path
        for path, content in expected.items()
        if not path.is_file() or path.read_text(encoding="utf-8") != content
    ]


def write_outputs() -> None:
    TYPESCRIPT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    PYTHON_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    generate_typescript(TYPESCRIPT_OUTPUT)
    PYTHON_OUTPUT.write_text(generate_python(load_openapi()), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查生成产物是否漂移")
    arguments = parser.parse_args()
    if not arguments.check:
        write_outputs()
        print("React 与 Python 契约类型已生成")
        return 0
    stale = stale_outputs()
    if stale:
        print("契约消费类型需要重新生成:")
        print("\n".join(f"- {path.relative_to(ROOT)}" for path in stale))
        return 1
    print("React 与 Python 契约类型漂移检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
