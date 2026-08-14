"""检查 Python 模块、公开接口和关键边界方法的注释结构。"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (
    Path("apps/api/src"),
    Path("apps/worker/src"),
    Path("packages/backend/src"),
)
HEADER_ONLY_ROOTS = (
    Path("apps/api/tests"),
    Path("apps/worker/tests"),
    Path("tests"),
    Path("infra/migrations"),
)
METHOD_DOCUMENTATION_SUFFIXES = (
    "Cipher",
    "Consumer",
    "Dispatcher",
    "Gateway",
    "Parser",
    "Service",
)
CHINESE_PATTERN = re.compile(r"[\u3400-\u9fff]")
TASK_MARKER_PATTERN = re.compile(r"\b(?:TODO|FIXME|HACK)\b")
VALID_TASK_MARKER_PATTERN = re.compile(
    r"\b(?:TODO|FIXME|HACK)\(@[A-Za-z0-9_-]+\s+\d{4}-\d{2}\):\s+\S"
)
INSTRUCTION_COMMENT_PATTERN = re.compile(
    r"(?:noqa|type:\s*ignore|fmt:|pragma|pylint|mypy|pyright|coverage|nosec|coding[=:])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CommentViolation:
    """单个可定位的 Python 注释结构违规。"""

    file_path: Path
    line: int
    message: str


def _python_files(root: Path, relative_roots: tuple[Path, ...]) -> tuple[Path, ...]:
    """按稳定路径顺序收集检查范围内的 Python 文件。"""

    files: list[Path] = []
    for relative_root in relative_roots:
        candidate = root / relative_root
        if candidate.exists():
            files.extend(candidate.rglob("*.py"))
    return tuple(sorted(set(files)))


def _is_public(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """双下划线协议和单下划线实现均不属于跨模块公开接口。"""

    return not node.name.startswith("_")


def _base_name(base: ast.expr) -> str:
    """提取基类尾部名称，用于识别 Protocol 等接口边界。"""

    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return ""


def _requires_method_docs(node: ast.ClassDef) -> bool:
    """业务执行入口逐方法说明，Protocol 与具体 Adapter 由类型级契约统一描述。"""

    is_protocol = any(_base_name(base) == "Protocol" for base in node.bases)
    return not is_protocol and node.name.endswith(METHOD_DOCUMENTATION_SUFFIXES)


def _append_missing_doc(
    violations: list[CommentViolation],
    file_path: Path,
    node: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    subject: str,
) -> None:
    """统一记录缺失或非中文 docstring，保持门禁输出稳定。"""

    docstring = ast.get_docstring(node, clean=False)
    line = getattr(node, "lineno", 1)
    if docstring is None:
        violations.append(CommentViolation(file_path, line, f"{subject} 缺少中文 docstring"))
    elif not CHINESE_PATTERN.search(docstring):
        violations.append(CommentViolation(file_path, line, f"{subject} docstring 必须包含中文"))


def _check_comments(file_path: Path, source: str) -> list[CommentViolation]:
    """检查任务标记和手写行注释的中文要求。"""

    violations: list[CommentViolation] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        comments = (token for token in tokens if token.type == tokenize.COMMENT)
        for token in comments:
            text = token.string
            if TASK_MARKER_PATTERN.search(text) and not VALID_TASK_MARKER_PATTERN.search(text):
                violations.append(
                    CommentViolation(
                        file_path,
                        token.start[0],
                        "TODO/FIXME/HACK 必须包含责任角色、YYYY-MM 和退出条件",
                    )
                )
            if (
                not text.startswith("#!")
                and not INSTRUCTION_COMMENT_PATTERN.search(text)
                and not CHINESE_PATTERN.search(text)
            ):
                violations.append(
                    CommentViolation(file_path, token.start[0], "手写代码注释必须包含中文业务说明")
                )
    except tokenize.TokenError as error:
        violations.append(CommentViolation(file_path, 1, f"无法解析 Python 注释: {error}"))
    return violations


def collect_python_comment_violations(root: Path = PROJECT_ROOT) -> list[CommentViolation]:
    """收集当前仓库全部 Python 注释结构违规。"""

    violations: list[CommentViolation] = []
    strict_files = _python_files(root, SOURCE_ROOTS)
    header_only_files = _python_files(root, HEADER_ONLY_ROOTS)
    for file_path in (*strict_files, *header_only_files):
        source = file_path.read_text(encoding="utf-8")
        try:
            module = ast.parse(source, filename=str(file_path))
        except SyntaxError as error:
            violations.append(
                CommentViolation(file_path, error.lineno or 1, f"无法解析 Python AST: {error.msg}")
            )
            continue
        _append_missing_doc(violations, file_path, module, "模块")
        violations.extend(_check_comments(file_path, source))
        if file_path not in strict_files:
            continue
        for node in module.body:
            if isinstance(
                node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ) and _is_public(node):
                _append_missing_doc(violations, file_path, node, f"公开接口 {node.name}")
            if isinstance(node, ast.ClassDef) and _requires_method_docs(node):
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_public(
                        member
                    ):
                        _append_missing_doc(
                            violations,
                            file_path,
                            member,
                            f"边界方法 {node.name}.{member.name}",
                        )
    return violations


def main() -> int:
    """运行门禁并输出适合本地与 CI 定位的稳定错误格式。"""

    violations = collect_python_comment_violations()
    if not violations:
        print("Python 注释结构检查通过")
        return 0
    for violation in violations:
        relative = violation.file_path.relative_to(PROJECT_ROOT)
        print(f"{relative}:{violation.line}: {violation.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
