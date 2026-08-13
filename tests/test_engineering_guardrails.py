from pathlib import Path

from scripts.check_architecture import violations_for_file
from scripts.check_contract_compatibility import compare_schema
from scripts.check_repository_policy import collect_violations


def test_domain_cannot_import_fastapi(tmp_path: Path) -> None:
    root = tmp_path / "package"
    path = root / "modules" / "workspace" / "domain" / "workspace.py"
    path.parent.mkdir(parents=True)
    path.write_text("from fastapi import Depends\n", encoding="utf-8")

    violations = violations_for_file(path, root)

    assert len(violations) == 1
    assert "domain 层禁止依赖 fastapi" in violations[0].message


def test_domain_cannot_import_valkey_client(tmp_path: Path) -> None:
    root = tmp_path / "package"
    path = root / "modules" / "workspace" / "domain" / "cache.py"
    path.parent.mkdir(parents=True)
    path.write_text("from valkey import Valkey\n", encoding="utf-8")

    violations = violations_for_file(path, root)

    assert len(violations) == 1
    assert "domain 层禁止依赖 valkey" in violations[0].message


def test_shared_backend_domain_cannot_import_sqlalchemy(tmp_path: Path) -> None:
    root = tmp_path / "ai_platform_backend"
    path = root / "integration" / "domain.py"
    path.parent.mkdir(parents=True)
    path.write_text("from sqlalchemy import Table\n", encoding="utf-8")

    violations = violations_for_file(path, root)

    assert len(violations) == 1
    assert "domain 层禁止依赖 sqlalchemy" in violations[0].message


def test_api_cannot_import_infrastructure(tmp_path: Path) -> None:
    root = tmp_path / "package"
    path = root / "modules" / "workspace" / "api" / "routes.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "from ai_platform_api.modules.workspace.infrastructure.repository import Repository\n",
        encoding="utf-8",
    )

    violations = violations_for_file(path, root)

    assert len(violations) == 1
    assert "api 层禁止直接依赖 infrastructure" in violations[0].message


def test_api_cannot_import_sqlalchemy(tmp_path: Path) -> None:
    root = tmp_path / "package"
    path = root / "modules" / "workspace" / "api" / "routes.py"
    path.parent.mkdir(parents=True)
    path.write_text("from sqlalchemy.orm import Session\n", encoding="utf-8")

    violations = violations_for_file(path, root)

    assert len(violations) == 1
    assert "api 层禁止依赖 sqlalchemy" in violations[0].message


def test_application_cannot_import_infrastructure(tmp_path: Path) -> None:
    root = tmp_path / "package"
    path = root / "modules" / "workspace" / "application" / "create.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "from package.modules.workspace.infrastructure.repository import Repository\n",
        encoding="utf-8",
    )

    violations = violations_for_file(path, root)

    assert len(violations) == 1
    assert "application 层禁止直接依赖 infrastructure" in violations[0].message


def test_module_cannot_import_another_modules_infrastructure(tmp_path: Path) -> None:
    root = tmp_path / "package"
    path = root / "modules" / "workspace" / "infrastructure" / "repository.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "from package.modules.integration.infrastructure.outbox import Outbox\n",
        encoding="utf-8",
    )

    violations = violations_for_file(path, root)

    assert len(violations) == 1
    assert "业务模块禁止依赖其他模块的 infrastructure 实现" in violations[0].message


def test_contract_check_rejects_removed_enum_value() -> None:
    errors: list[str] = []

    compare_schema(
        {"type": "string", "enum": ["allow", "deny"]},
        {"type": "string", "enum": ["allow"]},
        "decision",
        errors,
    )

    assert errors == ["decision: enum 删除了 ['deny']"]


def test_contract_check_rejects_new_required_field() -> None:
    errors: list[str] = []

    compare_schema(
        {"type": "object", "properties": {"name": {"type": "string"}}},
        {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        },
        "request",
        errors,
    )

    assert errors == ["request: 新增必填字段 ['name']"]


def test_repository_policy_rejects_private_key(tmp_path: Path) -> None:
    key_file = tmp_path / "fixture.txt"
    key_file.write_text(
        "-----BEGIN " + "PRIVATE KEY-----\nsynthetic-test-only\n",
        encoding="utf-8",
    )

    violations = collect_violations([key_file])

    assert violations == ["发现疑似真实凭证: fixture.txt"]
