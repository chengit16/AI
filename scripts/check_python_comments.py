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
STAGE_COMMENT_PATTERN = re.compile(r"#\s*(\d+)[.\u3001\uFF0E]\s*(\S.*)")
LONG_FUNCTION_REASON_PATTERN = re.compile(r"#\s*长函数保留原因(?:\uFF1A|:)\s*\S")
LONG_FUNCTION_REVIEW_LINES = 30
LONG_FUNCTION_REQUIRED_LINES = 60
LONG_FUNCTION_SPLIT_LINES = 100
LAYOUT_ONLY_TOKENS = frozenset("()[]{}.,:;")
COMPLEXITY_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.Match,
    ast.With,
    ast.AsyncWith,
    ast.Await,
)
STAGE_CALL_NAMES = frozenset(
    {
        "commit",
        "consume_usage",
        "execute",
        "invoke",
        "publish",
        "record",
        "rollback",
        "send",
    }
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


def _source_tokens(source: str) -> tuple[tokenize.TokenInfo, ...]:
    """解析一次源码 Token，供有效代码行和函数内部注释复用。"""

    return tuple(tokenize.generate_tokens(io.StringIO(source).readline))


def _effective_code_lines(tokens: tuple[tokenize.TokenInfo, ...]) -> frozenset[int]:
    """返回包含有效代码的行，过滤空白、注释和仅用于排版的分隔符。"""

    tokens_by_line: dict[int, list[tokenize.TokenInfo]] = {}
    ignored_types = {
        tokenize.ENCODING,
        tokenize.ENDMARKER,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.NEWLINE,
        tokenize.NL,
        tokenize.COMMENT,
    }
    for token in tokens:
        if token.type in ignored_types:
            continue
        tokens_by_line.setdefault(token.start[0], []).append(token)
    return frozenset(
        line
        for line, line_tokens in tokens_by_line.items()
        if any(
            token.type != tokenize.OP or token.string not in LAYOUT_ONLY_TOKENS
            for token in line_tokens
        )
    )


def _owned_nodes(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[ast.AST, ...]:
    """遍历当前函数直接拥有的逻辑，不把嵌套函数复杂度计入外层。"""

    owned: list[ast.AST] = []

    def visit(current: ast.AST) -> None:
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            owned.append(child)
            visit(child)

    visit(node)
    return tuple(owned)


def _function_body_bounds(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[int, int] | None:
    """取得排除函数签名和 docstring 后的函数体行范围。"""

    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if not body or node.end_lineno is None:
        return None
    return body[0].lineno, node.end_lineno


def _nested_function_ranges(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[tuple[int, int], ...]:
    """收集嵌套函数范围，防止外层长度和阶段注释重复计算。"""

    ranges: list[tuple[int, int]] = []
    for child in ast.walk(node):
        if child is node or not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if child.end_lineno is not None:
            ranges.append((child.lineno, child.end_lineno))
    return tuple(ranges)


def _is_nested_line(line: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    """判断源码行是否属于当前函数内部的另一函数。"""

    return any(start <= line <= end for start, end in ranges)


def _stage_signal_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """统计控制流、事务和外部调用信号，用于识别 30～59 行多阶段函数。"""

    count = 0
    for child in _owned_nodes(node):
        if isinstance(child, COMPLEXITY_NODES):
            count += 1
        if isinstance(child, ast.Call):
            function = child.func
            name = function.id if isinstance(function, ast.Name) else ""
            if isinstance(function, ast.Attribute):
                name = function.attr
            if name in STAGE_CALL_NAMES:
                count += 1
    return count


def _check_long_functions(
    file_path: Path,
    module: ast.Module,
    tokens: tuple[tokenize.TokenInfo, ...],
) -> list[CommentViolation]:
    """检查长函数的重要阶段、连续编号及超长函数保留原因。"""

    violations: list[CommentViolation] = []
    code_lines = _effective_code_lines(tokens)
    comments = tuple(token for token in tokens if token.type == tokenize.COMMENT)
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bounds = _function_body_bounds(node)
        if bounds is None:
            continue
        start_line, end_line = bounds
        nested_ranges = _nested_function_ranges(node)
        effective_lines = sum(
            start_line <= line <= end_line and not _is_nested_line(line, nested_ranges)
            for line in code_lines
        )
        if effective_lines < LONG_FUNCTION_REVIEW_LINES:
            continue

        owned_comments = tuple(
            token
            for token in comments
            if node.lineno <= token.start[0] <= end_line
            and not _is_nested_line(token.start[0], nested_ranges)
        )
        numbered_comments = []
        for token in owned_comments:
            match = STAGE_COMMENT_PATTERN.search(token.string)
            if match:
                numbered_comments.append((token.start[0], int(match.group(1))))
        numbered_comments.sort()
        numbers = [number for _, number in numbered_comments]
        expected_numbers = list(range(1, len(numbers) + 1))
        if numbers and numbers != expected_numbers:
            violations.append(
                CommentViolation(
                    file_path,
                    node.lineno,
                    f"长函数 {node.name} 的阶段注释编号必须从 1 连续递增",
                )
            )

        required_stages = 0
        if effective_lines >= LONG_FUNCTION_REQUIRED_LINES:
            required_stages = 3
        elif _stage_signal_count(node) >= 2:
            required_stages = 2
        if required_stages and len(numbers) < required_stages:
            violations.append(
                CommentViolation(
                    file_path,
                    node.lineno,
                    f"长函数 {node.name} 有 {effective_lines} 行有效代码, "
                    f"至少需要 {required_stages} 个连续编号的内部阶段注释",
                )
            )
        if effective_lines >= LONG_FUNCTION_SPLIT_LINES and not any(
            LONG_FUNCTION_REASON_PATTERN.search(token.string) for token in owned_comments
        ):
            violations.append(
                CommentViolation(
                    file_path,
                    node.lineno,
                    f"长函数 {node.name} 有 {effective_lines} 行有效代码, "
                    "必须拆分或使用“长函数保留原因:”说明取舍",
                )
            )
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
        try:
            source_tokens = _source_tokens(source)
        except tokenize.TokenError as error:
            violations.append(CommentViolation(file_path, 1, f"无法解析 Python Token: {error}"))
            continue
        violations.extend(_check_long_functions(file_path, module, source_tokens))
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
