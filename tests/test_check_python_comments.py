"""验证 Python 注释门禁可以拒绝缺失说明并保留生成边界。"""

from pathlib import Path

from scripts.check_python_comments import collect_python_comment_violations


def _write(root: Path, relative_path: str, content: str) -> None:
    """在临时合成仓库中创建待检查源码。"""

    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_accepts_documented_module_public_api_and_boundary_method(tmp_path: Path) -> None:
    """完整中文模块、公开类和边界方法应通过检查。"""

    _write(
        tmp_path,
        "apps/api/src/example/service.py",
        '''"""合成服务模块。"""

class ExampleService:
    """提供合成业务能力。"""

    def execute(self) -> bool:
        """执行一次无副作用的合成操作。"""
        return True
''',
    )
    assert collect_python_comment_violations(tmp_path) == []


def test_rejects_missing_module_and_public_api_docs(tmp_path: Path) -> None:
    """缺少模块及公开接口说明时应同时报告。"""

    _write(tmp_path, "apps/api/src/example/service.py", "def execute():\n    return True\n")
    messages = [item.message for item in collect_python_comment_violations(tmp_path)]
    assert any("模块" in message for message in messages)
    assert any("公开接口 execute" in message for message in messages)


def test_rejects_undocumented_boundary_method(tmp_path: Path) -> None:
    """跨层 Service 的公开方法必须具有独立调用契约说明。"""

    _write(
        tmp_path,
        "apps/worker/src/example/service.py",
        '''"""合成 Worker 服务。"""

class JobService:
    """管理合成任务。"""

    def run(self):
        return None
''',
    )
    violations = collect_python_comment_violations(tmp_path)
    assert any("JobService.run" in item.message for item in violations)


def test_protocol_uses_type_level_contract_without_repeated_method_docs(tmp_path: Path) -> None:
    """Protocol 的类型级契约已覆盖抽象方法，不应制造重复方法说明。"""

    _write(
        tmp_path,
        "apps/api/src/example/protocol.py",
        '''"""合成网关契约模块。"""

from typing import Protocol

class ExampleGateway(Protocol):
    """约束合成调用的输入与失败边界。"""

    def invoke(self) -> str: ...
''',
    )
    assert collect_python_comment_violations(tmp_path) == []


def test_rejects_invalid_task_marker_and_english_comment(tmp_path: Path) -> None:
    """无责任信息任务标记和纯英文业务注释都应被拒绝。"""

    _write(
        tmp_path,
        "apps/api/src/example/task.py",
        '''"""合成任务模块。"""
# TODO: later
# business fallback
value = True
''',
    )
    messages = [item.message for item in collect_python_comment_violations(tmp_path)]
    assert any("责任角色" in message for message in messages)
    assert any("中文业务说明" in message for message in messages)


def test_header_only_roots_require_module_docs_but_not_test_function_docs(tmp_path: Path) -> None:
    """测试与 Migration 只强制文件职责，不制造逐用例模板注释。"""

    _write(
        tmp_path,
        "apps/api/tests/test_example.py",
        '''"""验证合成业务边界。"""

def test_example():
    assert True
''',
    )
    assert collect_python_comment_violations(tmp_path) == []


def _long_function_source(
    assignment_count: int,
    *,
    stage_comments: tuple[tuple[int, int, str], ...] = (),
    branches: bool = False,
    reason: str | None = None,
) -> str:
    """构造具有稳定有效代码行数的合成长函数。"""

    comments_by_index = {
        assignment_index: (number, description)
        for assignment_index, number, description in stage_comments
    }
    lines = ['"""合成长函数模块。"""', "", "def _workflow(enabled: bool = True):"]
    if reason is not None:
        lines.append(f"    # 长函数保留原因: {reason}")
    for index in range(assignment_count):
        if index in comments_by_index:
            number, description = comments_by_index[index]
            lines.append(f"    # {number}. {description}")
        lines.append(f"    value_{index} = {index}")
    if branches:
        lines.extend(
            [
                "    if enabled:",
                "        value_0 += 1",
                "    if not enabled:",
                "        value_0 -= 1",
            ]
        )
    lines.append(f"    return value_{assignment_count - 1}")
    return "\n".join(lines) + "\n"


def test_rejects_multistage_function_from_thirty_effective_lines(tmp_path: Path) -> None:
    """30～59 行函数具有多个控制阶段时必须提供内部编号说明。"""

    _write(
        tmp_path,
        "apps/api/src/example/workflow.py",
        _long_function_source(28, branches=True),
    )
    messages = [item.message for item in collect_python_comment_violations(tmp_path)]
    assert any("至少需要 2 个" in message for message in messages)


def test_accepts_numbered_stages_for_multistage_function(tmp_path: Path) -> None:
    """连续编号的阶段说明应满足 30 行多阶段函数门禁。"""

    _write(
        tmp_path,
        "apps/api/src/example/workflow.py",
        _long_function_source(
            28,
            branches=True,
            stage_comments=(
                (0, 1, "准备分支共同使用的合成事实。"),
                (20, 2, "根据输入状态执行互斥调整。"),
            ),
        ),
    )
    assert collect_python_comment_violations(tmp_path) == []


def test_rejects_sixty_line_function_with_missing_or_broken_stages(tmp_path: Path) -> None:
    """60 行函数必须具有至少三个从 1 开始连续编号的阶段。"""

    _write(
        tmp_path,
        "apps/api/src/example/workflow.py",
        _long_function_source(
            61,
            stage_comments=((0, 1, "建立第一组事实。"), (30, 3, "标记错误编号阶段。")),
        ),
    )
    messages = [item.message for item in collect_python_comment_violations(tmp_path)]
    assert any("编号必须从 1 连续递增" in message for message in messages)
    assert any("至少需要 3 个" in message for message in messages)


def test_accepts_sixty_line_function_with_complete_stages(tmp_path: Path) -> None:
    """三个连续编号阶段应满足 60 行函数的强制分段要求。"""

    _write(
        tmp_path,
        "apps/api/src/example/workflow.py",
        _long_function_source(
            61,
            stage_comments=(
                (0, 1, "建立第一组事实。"),
                (20, 2, "建立第二组事实。"),
                (40, 3, "建立第三组事实。"),
            ),
        ),
    )
    assert collect_python_comment_violations(tmp_path) == []


def test_rejects_hundred_line_function_without_retention_reason(tmp_path: Path) -> None:
    """100 行函数即使已有阶段说明，也必须解释为何不拆分。"""

    _write(
        tmp_path,
        "apps/api/src/example/workflow.py",
        _long_function_source(
            101,
            stage_comments=(
                (0, 1, "建立第一组事实。"),
                (35, 2, "建立第二组事实。"),
                (70, 3, "建立第三组事实。"),
            ),
        ),
    )
    messages = [item.message for item in collect_python_comment_violations(tmp_path)]
    assert any("长函数保留原因" in message for message in messages)


def test_accepts_hundred_line_function_with_reason_and_complete_stages(tmp_path: Path) -> None:
    """保留原因和完整阶段应共同满足 100 行函数的例外要求。"""

    _write(
        tmp_path,
        "apps/api/src/example/workflow.py",
        _long_function_source(
            101,
            reason="合成测试必须在同一作用域覆盖超长函数验收边界。",
            stage_comments=(
                (0, 1, "建立第一组事实。"),
                (35, 2, "建立第二组事实。"),
                (70, 3, "建立第三组事实。"),
            ),
        ),
    )
    assert collect_python_comment_violations(tmp_path) == []


def test_nested_function_is_checked_without_counting_it_in_outer_function(tmp_path: Path) -> None:
    """嵌套函数应独立报告，外层不能重复继承其长度和阶段要求。"""

    assignments = "\n".join(f"        value_{index} = {index}" for index in range(61))
    _write(
        tmp_path,
        "apps/api/src/example/nested.py",
        f'''"""合成嵌套流程模块。"""

def _outer():
    def _inner():
{assignments}
        return value_60
    return _inner()
''',
    )
    violations = collect_python_comment_violations(tmp_path)
    assert len(violations) == 1
    assert "长函数 _inner" in violations[0].message
    assert "至少需要 3 个" in violations[0].message
