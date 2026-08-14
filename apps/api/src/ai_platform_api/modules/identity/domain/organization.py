"""定义多级部门、岗位、成员归属和闭包重建领域规则。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter

from ai_platform_api.modules.identity.domain.enterprise import (
    WorkspaceMembership,
    WorkspaceRecord,
)

OrganizationStatus = Literal["active", "disabled"]


@dataclass(frozen=True)
class Department:
    """表示企业空间部门节点及其父子关系和并发版本。"""

    department_id: UUID
    workspace_id: UUID
    parent_department_id: UUID | None
    name: str
    status: OrganizationStatus
    created_at: datetime
    updated_at: datetime
    version: int

    def move(self, *, parent_department_id: UUID | None, occurred_at: datetime) -> Department:
        if self.parent_department_id == parent_department_id:
            raise InvalidOrganizationTransitionError
        return replace(
            self,
            parent_department_id=parent_department_id,
            updated_at=occurred_at,
            version=self.version + 1,
        )

    def disable(self, *, occurred_at: datetime) -> Department:
        if self.status != "active":
            raise InvalidOrganizationTransitionError
        return replace(
            self,
            status="disabled",
            updated_at=occurred_at,
            version=self.version + 1,
        )

    def activate(self, *, occurred_at: datetime) -> Department:
        if self.status != "disabled":
            raise InvalidOrganizationTransitionError
        return replace(
            self,
            status="active",
            updated_at=occurred_at,
            version=self.version + 1,
        )


@dataclass(frozen=True)
class DepartmentClosure:
    """保存部门祖先到后代的闭包关系和层级距离。"""

    ancestor_department_id: UUID
    descendant_department_id: UUID
    depth: int


@dataclass(frozen=True)
class DepartmentSummary:
    """提供部门有效状态与树深度的稳定只读投影。"""

    department_id: UUID
    parent_department_id: UUID | None
    name: str
    status: OrganizationStatus
    effective_active: bool
    depth: int
    version: int


@dataclass(frozen=True)
class Position:
    """表示企业空间内归属于指定部门的可分配职位。"""

    position_id: UUID
    workspace_id: UUID
    department_id: UUID
    name: str
    status: OrganizationStatus
    created_at: datetime
    updated_at: datetime
    version: int

    def disable(self, *, occurred_at: datetime) -> Position:
        if self.status != "active":
            raise InvalidOrganizationTransitionError
        return replace(
            self,
            status="disabled",
            updated_at=occurred_at,
            version=self.version + 1,
        )

    def activate(self, *, occurred_at: datetime) -> Position:
        if self.status != "disabled":
            raise InvalidOrganizationTransitionError
        return replace(
            self,
            status="active",
            updated_at=occurred_at,
            version=self.version + 1,
        )


@dataclass(frozen=True)
class PositionSummary:
    """提供职位状态、名称和有效状态的只读投影。"""

    position_id: UUID
    department_id: UUID
    name: str
    status: OrganizationStatus
    effective_active: bool
    version: int


@dataclass(frozen=True)
class OrganizationAssignment:
    """汇总成员的主部门、全部部门及职位归属。"""

    account_id: UUID
    department_ids: tuple[UUID, ...]
    primary_department_id: UUID | None
    position_ids: tuple[UUID, ...]
    membership_version: int


class InvalidOrganizationTransitionError(Exception):
    """组织状态、同级名称或成员归属不允许当前转换。"""


class DepartmentCycleError(Exception):
    """部门父子关系形成环或引用了不在当前树中的父部门。"""


class OrganizationWriteConflictError(Exception):
    """数据库约束关闭并发创建、移动和成员归属竞态。"""


def build_department_closure(
    departments: tuple[Department, ...],
) -> tuple[DepartmentClosure, ...]:
    """从邻接关系重建闭包；任何悬空父节点或环都会失败关闭。"""

    by_id = {department.department_id: department for department in departments}
    if len(by_id) != len(departments):
        raise DepartmentCycleError
    rows: list[DepartmentClosure] = []
    for department in departments:
        rows.append(DepartmentClosure(department.department_id, department.department_id, 0))
        visited = {department.department_id}
        parent_id = department.parent_department_id
        depth = 1
        while parent_id is not None:
            if parent_id in visited:
                raise DepartmentCycleError
            parent = by_id.get(parent_id)
            if parent is None:
                raise DepartmentCycleError
            rows.append(DepartmentClosure(parent_id, department.department_id, depth))
            visited.add(parent_id)
            parent_id = parent.parent_department_id
            depth += 1
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row.descendant_department_id.int,
                row.depth,
                row.ancestor_department_id.int,
            ),
        )
    )


def summarize_departments(
    departments: tuple[Department, ...],
) -> tuple[DepartmentSummary, ...]:
    """结合闭包和祖先状态生成稳定排序的部门摘要。"""

    closures = build_department_closure(departments)
    by_id = {department.department_id: department for department in departments}
    ancestor_ids: dict[UUID, set[UUID]] = {
        department.department_id: set() for department in departments
    }
    depth_by_id: dict[UUID, int] = {department.department_id: 0 for department in departments}
    for closure in closures:
        ancestor_ids[closure.descendant_department_id].add(closure.ancestor_department_id)
        depth_by_id[closure.descendant_department_id] = max(
            depth_by_id[closure.descendant_department_id], closure.depth
        )
    summaries = tuple(
        DepartmentSummary(
            department_id=department.department_id,
            parent_department_id=department.parent_department_id,
            name=department.name,
            status=department.status,
            effective_active=all(
                by_id[ancestor_id].status == "active"
                for ancestor_id in ancestor_ids[department.department_id]
            ),
            depth=depth_by_id[department.department_id],
            version=department.version,
        )
        for department in departments
    )
    return tuple(
        sorted(summaries, key=lambda item: (item.depth, item.name, item.department_id.int))
    )


def active_descendant_ids(
    departments: tuple[Department, ...],
    department_id: UUID,
) -> tuple[UUID, ...]:
    """角色继承只覆盖自身及有效启用的后代，停用祖先会关闭整棵子树。"""

    summaries = {item.department_id: item for item in summarize_departments(departments)}
    if department_id not in summaries:
        raise DepartmentCycleError
    closures = build_department_closure(departments)
    descendants = {
        row.descendant_department_id
        for row in closures
        if row.ancestor_department_id == department_id
        and summaries[row.descendant_department_id].effective_active
    }
    return tuple(sorted(descendants, key=lambda value: value.int))


class OrganizationRepository(Protocol):
    """在工作空间边界内维护组织树、职位和成员归属。"""

    def get_workspace(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> WorkspaceRecord | None: ...

    def get_membership(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceMembership | None: ...

    def list_departments(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> tuple[Department, ...]: ...

    def add_department(self, department: Department) -> None: ...

    def save_department(self, department: Department) -> None: ...

    def replace_department_closure(
        self,
        workspace_id: UUID,
        closures: tuple[DepartmentClosure, ...],
    ) -> None: ...

    def list_positions(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> tuple[Position, ...]: ...

    def add_position(self, position: Position) -> None: ...

    def save_position(self, position: Position) -> None: ...

    def replace_assignment(
        self,
        *,
        workspace_id: UUID,
        membership: WorkspaceMembership,
        department_ids: tuple[UUID, ...],
        primary_department_id: UUID | None,
        position_ids: tuple[UUID, ...],
        occurred_at: datetime,
    ) -> WorkspaceMembership: ...

    def get_assignment(
        self,
        workspace_id: UUID,
        membership: WorkspaceMembership,
    ) -> OrganizationAssignment: ...

    def bump_role_version(self, workspace_id: UUID) -> int: ...


class OrganizationUnitOfWork(Protocol):
    """保证组织变更、角色版本、审计与 Outbox 原子提交。"""

    @property
    def organization(self) -> OrganizationRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> OrganizationUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
