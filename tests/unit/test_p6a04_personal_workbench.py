"""验证 P6A-04 工作台、搜索和最近访问应用边界。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.knowledge.application.facts import KnowledgeNotFoundError
from ai_platform_api.modules.knowledge.application.management import KnowledgeManagementService
from ai_platform_api.modules.knowledge.domain.models import (
    KnowledgeSearchPage,
    KnowledgeUnitOfWork,
    PersonalKnowledgeWorkbench,
    PersonalWorkbenchStatistics,
)
from ai_platform_api.modules.knowledge.infrastructure.sqlalchemy import (
    SqlAlchemyKnowledgeRepository,
)

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000804")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000804")
DOCUMENT_ID = UUID("40000000-0000-4000-8000-000000000804")


class RecordingWriter:
    """收集审计记录，避免单元测试依赖数据库。"""

    def __init__(self) -> None:
        self.records: list[Any] = []

    def add(self, value: Any) -> None:
        self.records.append(value)


class WorkbenchRepositoryScenario:
    """记录应用服务下传的资源范围、字段遮罩和分页边界。"""

    def __init__(self) -> None:
        self.workbench_arguments: dict[str, object] = {}
        self.search_arguments: dict[str, object] = {}
        self.access_visible = True

    def get_workspace_access(self, workspace_id: UUID, account_id: UUID) -> tuple[str, str]:
        assert workspace_id == WORKSPACE_ID
        assert account_id == ACCOUNT_ID
        return ("active", "owner")

    def get_personal_workbench(
        self, workspace_id: UUID, **arguments: object
    ) -> PersonalKnowledgeWorkbench:
        assert workspace_id == WORKSPACE_ID
        self.workbench_arguments = arguments
        return PersonalKnowledgeWorkbench(
            statistics=PersonalWorkbenchStatistics(0, 0, 0, 0, 0, 0),
            recent_documents=(),
            favorite_documents=(),
        )

    def search_published_documents(
        self, workspace_id: UUID, **arguments: object
    ) -> KnowledgeSearchPage:
        assert workspace_id == WORKSPACE_ID
        self.search_arguments = arguments
        return KnowledgeSearchPage((), 0, 2, bool(arguments["include_content"]))

    def record_document_access(
        self, workspace_id: UUID, document_id: UUID, **arguments: object
    ) -> bool:
        assert workspace_id == WORKSPACE_ID
        assert document_id == DOCUMENT_ID
        assert arguments["viewer_account_id"] == ACCOUNT_ID
        return self.access_visible


class WorkbenchUnitOfWorkScenario:
    """提供工作台服务所需的最小事务和审计端口。"""

    def __init__(self) -> None:
        self.knowledge = WorkbenchRepositoryScenario()
        self.audit = RecordingWriter()
        self.outbox = RecordingWriter()
        self.usage = cast(Any, None)
        self.committed = False

    def __enter__(self) -> WorkbenchUnitOfWorkScenario:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def commit(self) -> None:
        self.committed = True


class SqlRecordingSession:
    """记录 Repository 构造的 SQL，不连接数据库即可验证字段遮罩边界。"""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def scalar(self, statement: object) -> int:
        self.statements.append(str(statement))
        return 0

    def execute(self, statement: object) -> tuple[()]:
        self.statements.append(str(statement))
        return ()


def browser_context() -> RequestContext:
    """构造通过浏览器会话建立的可信个人空间上下文。"""

    return RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        authentication_method="browser_session",
        trace=TraceContext("a" * 32, "b" * 16),
    )


def service(scenario: WorkbenchUnitOfWorkScenario) -> KnowledgeManagementService:
    """装配不访问对象存储的工作台应用服务。"""

    return KnowledgeManagementService(
        cast(KnowledgeUnitOfWork, scenario),
        cast(Any, None),
    )


def test_workbench_uses_document_resource_scope_without_reinterpreting_ids() -> None:
    """文档资源 ID 必须原样下传，不能被当作知识库 ID 统计。"""

    scenario = WorkbenchUnitOfWorkScenario()
    context = replace(
        browser_context(),
        authorized_resource_ids=frozenset({DOCUMENT_ID}),
        authorized_maximum_security_level="CONFIDENTIAL",
    )

    result = service(scenario).get_personal_workbench(
        context,
        recent_limit=6,
        favorite_limit=4,
    )

    assert result.statistics.document_count == 0
    assert scenario.knowledge.workbench_arguments["resource_ids"] == frozenset({DOCUMENT_ID})
    assert scenario.knowledge.workbench_arguments["maximum_security_level"] == "CONFIDENTIAL"


def test_search_rejects_offset_above_contract_limit_before_repository_call() -> None:
    """应用服务直调与 HTTP Query 必须共享 10000 的偏移上限。"""

    scenario = WorkbenchUnitOfWorkScenario()

    with pytest.raises(ValueError, match="0 到 10000"):
        service(scenario).search_published_documents(
            replace(browser_context(), authorized_workspace=True),
            query="合成发布内容",
            knowledge_base_id=None,
            match_type="all",
            favorite_only=False,
            limit=20,
            offset=10_001,
        )

    assert scenario.knowledge.search_arguments == {}


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (replace(browser_context(), authorized_maximum_security_level="PUBLIC"), False),
        (
            replace(
                browser_context(),
                authorized_maximum_security_level="RESTRICTED",
                authorized_field_mask=frozenset({"content"}),
            ),
            False,
        ),
        (replace(browser_context(), authorized_maximum_security_level="INTERNAL"), True),
    ],
)
def test_search_content_respects_clearance_and_field_mask(
    context: RequestContext,
    expected: bool,
) -> None:
    """正文搜索只有同时满足最低密级和字段投影时才能读取 Chunk。"""

    scenario = WorkbenchUnitOfWorkScenario()

    result = service(scenario).search_published_documents(
        context,
        query="合成发布内容",
        knowledge_base_id=None,
        match_type="all",
        favorite_only=True,
        limit=20,
        offset=0,
    )

    assert scenario.knowledge.search_arguments["include_content"] is expected
    assert result.content_search_available is expected


@pytest.mark.parametrize(
    ("match_type", "include_content"),
    [("all", False), ("title", False), ("title", True)],
)
def test_repository_title_only_sql_never_references_chunks(
    match_type: str,
    include_content: bool,
) -> None:
    """纯标题路径必须在 SQL 层避开正文表，不能只在响应阶段隐藏摘要。"""

    session = SqlRecordingSession()
    repository = SqlAlchemyKnowledgeRepository(cast(Any, session))

    result = repository.search_published_documents(
        WORKSPACE_ID,
        viewer_account_id=ACCOUNT_ID,
        query="合成标题",
        knowledge_base_id=None,
        match_type=cast(Any, match_type),
        favorite_only=False,
        include_content=include_content,
        limit=20,
        offset=0,
        authorized_workspace=True,
        department_ids=frozenset(),
        account_ids=frozenset(),
        resource_ids=frozenset(),
        maximum_security_level="RESTRICTED",
    )

    assert result.items == ()
    assert "retrieval_chunks" not in "\n".join(session.statements)


def test_repository_content_sql_requires_active_index_version() -> None:
    """正文命中只能来自当前发布指针对应的活动索引版本。"""

    session = SqlRecordingSession()
    repository = SqlAlchemyKnowledgeRepository(cast(Any, session))

    repository.search_published_documents(
        WORKSPACE_ID,
        viewer_account_id=ACCOUNT_ID,
        query="合成正文",
        knowledge_base_id=None,
        match_type="content",
        favorite_only=False,
        include_content=True,
        limit=20,
        offset=0,
        authorized_workspace=True,
        department_ids=frozenset(),
        account_ids=frozenset(),
        resource_ids=frozenset(),
        maximum_security_level="RESTRICTED",
    )

    search_sql = "\n".join(session.statements)
    assert "retrieval_chunks" in search_sql
    assert "index_versions.status" in search_sql


def test_record_access_is_audited_and_invisible_document_fails_closed() -> None:
    """最近访问只有在资源复核成功后提交，撤权后统一表现为不可见。"""

    scenario = WorkbenchUnitOfWorkScenario()
    context = replace(browser_context(), authorized_resource_ids=frozenset({DOCUMENT_ID}))

    service(scenario).record_document_access(context, document_id=DOCUMENT_ID)

    assert scenario.committed is True
    assert scenario.audit.records[0].action == "knowledge.document.access"
    assert scenario.audit.records[0].attributes == {}

    denied = WorkbenchUnitOfWorkScenario()
    denied.knowledge.access_visible = False
    with pytest.raises(KnowledgeNotFoundError):
        service(denied).record_document_access(context, document_id=DOCUMENT_ID)
    assert denied.committed is False
    assert denied.audit.records == []
