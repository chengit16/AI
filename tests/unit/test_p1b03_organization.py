"""验证 P1B-03 部门环、有效状态、岗位和成员归属规则。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from ai_platform_api.modules.identity.domain.organization import (
    Department,
    DepartmentCycleError,
    active_descendant_ids,
    build_department_closure,
    summarize_departments,
)

NOW = datetime(2026, 8, 14, tzinfo=UTC)
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000081")
ROOT_ID = UUID("50000000-0000-4000-8000-000000000081")
CHILD_ID = UUID("50000000-0000-4000-8000-000000000082")
GRANDCHILD_ID = UUID("50000000-0000-4000-8000-000000000083")


def department(
    department_id: UUID,
    *,
    parent_id: UUID | None,
    name: str,
) -> Department:
    return Department(
        department_id,
        WORKSPACE_ID,
        parent_id,
        name,
        "active",
        NOW,
        NOW,
        1,
    )


def tree() -> tuple[Department, ...]:
    return (
        department(ROOT_ID, parent_id=None, name="合成总部"),
        department(CHILD_ID, parent_id=ROOT_ID, name="合成研发部"),
        department(GRANDCHILD_ID, parent_id=CHILD_ID, name="合成平台组"),
    )


def test_department_closure_and_move_have_deterministic_scope() -> None:
    closures = build_department_closure(tree())
    assert [
        (row.ancestor_department_id, row.descendant_department_id, row.depth)
        for row in closures
        if row.descendant_department_id == GRANDCHILD_ID
    ] == [
        (GRANDCHILD_ID, GRANDCHILD_ID, 0),
        (CHILD_ID, GRANDCHILD_ID, 1),
        (ROOT_ID, GRANDCHILD_ID, 2),
    ]
    assert active_descendant_ids(tree(), ROOT_ID) == (ROOT_ID, CHILD_ID, GRANDCHILD_ID)

    moved = tree()[2].move(parent_department_id=ROOT_ID, occurred_at=NOW + timedelta(minutes=1))
    moved_tree = (tree()[0], tree()[1], moved)
    assert (
        next(
            item
            for item in summarize_departments(moved_tree)
            if item.department_id == GRANDCHILD_ID
        ).depth
        == 1
    )


def test_cycle_and_orphan_are_rejected() -> None:
    cycle = (
        department(ROOT_ID, parent_id=CHILD_ID, name="合成总部"),
        department(CHILD_ID, parent_id=ROOT_ID, name="合成研发部"),
    )
    with pytest.raises(DepartmentCycleError):
        build_department_closure(cycle)
    with pytest.raises(DepartmentCycleError):
        build_department_closure(
            (
                department(
                    ROOT_ID,
                    parent_id=UUID("50000000-0000-4000-8000-000000000099"),
                    name="合成总部",
                ),
            )
        )


def test_disabled_ancestor_closes_descendant_inheritance_scope() -> None:
    disabled_root = tree()[0].disable(occurred_at=NOW + timedelta(minutes=1))
    disabled_tree = (disabled_root, tree()[1], tree()[2])
    summaries = {item.department_id: item for item in summarize_departments(disabled_tree)}

    assert summaries[ROOT_ID].effective_active is False
    assert summaries[CHILD_ID].status == "active"
    assert summaries[CHILD_ID].effective_active is False
    assert active_descendant_ids(disabled_tree, ROOT_ID) == ()
