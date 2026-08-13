"""检查 Python 后端正式分层目录的依赖方向。"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parents[1]
PYTHON_ROOTS = (
    ROOT / "apps/api/src/ai_platform_api",
    ROOT / "apps/worker/src/ai_platform_worker",
)


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    message: str


def imported_modules(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, "." * node.level + node.module))
    return imports


def layer_for(path: Path) -> str | None:
    parts = path.parts
    if "modules" not in parts:
        return None
    module_index = parts.index("modules")
    if len(parts) <= module_index + 2:
        return None
    layer = parts[module_index + 2]
    return layer if layer in {"api", "application", "domain", "infrastructure"} else None


def violations_for_file(path: Path, root: Path) -> list[Violation]:
    layer = layer_for(path.relative_to(root))
    if layer is None:
        return []

    forbidden_prefixes = {
        "domain": ("fastapi", "celery", "sqlalchemy", "redis", "pydantic"),
        "application": ("fastapi", "celery", "sqlalchemy", "redis"),
        "api": ("celery", "sqlalchemy", "redis"),
        "infrastructure": ("fastapi", "celery"),
    }[layer]
    violations: list[Violation] = []
    for line, imported in imported_modules(path.read_text(encoding="utf-8")):
        if imported.startswith(forbidden_prefixes):
            violations.append(
                Violation(path, line, f"{layer} 层禁止依赖 {imported}"),
            )
        if layer == "domain" and any(
            segment in imported for segment in (".api", ".application", ".infrastructure")
        ):
            violations.append(
                Violation(path, line, "domain 层不能反向依赖外层实现"),
            )
        if layer in {"api", "application"} and ".infrastructure" in imported:
            violations.append(
                Violation(path, line, f"{layer} 层禁止直接依赖 infrastructure 实现"),
            )
        if layer == "api" and ".domain" in imported:
            violations.append(
                Violation(path, line, "api 层通过 application 接口使用领域能力"),
            )
        if layer == "infrastructure" and any(
            segment in imported for segment in (".api", ".application")
        ):
            violations.append(
                Violation(path, line, "infrastructure 层只能向 domain 接口提供 Adapter"),
            )
    return violations


def collect_violations(roots: tuple[Path, ...] = PYTHON_ROOTS) -> list[Violation]:
    violations: list[Violation] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            violations.extend(violations_for_file(path, root))
    return violations


def main() -> int:
    violations = collect_violations()
    if not violations:
        print("Python 模块依赖检查通过")
        return 0
    for violation in violations:
        relative = violation.path.relative_to(ROOT)
        print(f"{relative}:{violation.line}: {violation.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
