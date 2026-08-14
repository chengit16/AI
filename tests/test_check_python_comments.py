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
