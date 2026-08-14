from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.application.menu_releases import (
    MenuReleaseConflictError,
    MenuReleaseDeniedError,
    MenuReleaseService,
)
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.domain.menus import (
    MenuRelease,
    RoleMenuVisibility,
    WorkspaceMenuOverride,
)

ROOT = Path(__file__).parents[2]
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000094")
OTHER_WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000093")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000094")
ROLE_ID = UUID("70000000-0000-4000-8000-000000000094")
DIRECTORY_ID = UUID("82000000-0000-4000-8000-000000000001")
OVERVIEW_ID = UUID("82000000-0000-4000-8000-000000000002")
TRACE = TraceContext.continue_from("00-8123456789abcdef0123456789abcdef-8123456789abcdef-01")


class FactWriter:
    def __init__(self) -> None:
        self.values: list[object] = []

    def add(self, value: object) -> None:
        self.values.append(value)


class ReleaseRepository:
    def __init__(self, *, membership_type: str = "owner") -> None:
        registry = load_resource_registry(
            ROOT / "contracts/authorization/resource-registry.v1.json"
        )
        self.menu_version = 1
        self.overrides: tuple[WorkspaceMenuOverride, ...] = ()
        self.role_entries: tuple[RoleMenuVisibility, ...] = ()
        self.releases: dict[UUID, MenuRelease] = {}
        self.current_release_id: UUID | None = None
        self.membership_type = membership_type
        self.bindings = tuple(
            sorted(
                (item.menu_id, item.api_resource_id, item.action_type)
                for item in registry.workspace_menu_api_bindings
            )
        )

    def get_workspace_access(
        self,
        workspace_id: UUID,
        account_id: UUID,
    ) -> tuple[str, str, str] | None:
        if workspace_id == WORKSPACE_ID and account_id == ACCOUNT_ID:
            return "personal", "active", self.membership_type
        return None

    def get_menu_version(self, workspace_id: UUID, *, for_update: bool = False) -> int | None:
        return self.menu_version if workspace_id == WORKSPACE_ID else None

    def bump_menu_version(self, workspace_id: UUID, expected_version: int) -> int:
        self.menu_version += 1
        return self.menu_version

    def get_role(self, workspace_id: UUID, role_id: UUID) -> tuple[str, str] | None:
        return ("synthetic", "active") if role_id == ROLE_ID else None

    def list_registered_bindings(self) -> tuple[tuple[UUID, UUID, str], ...]:
        return self.bindings

    def list_overrides(self, workspace_id: UUID) -> tuple[WorkspaceMenuOverride, ...]:
        return self.overrides

    def replace_overrides(
        self,
        workspace_id: UUID,
        overrides: tuple[WorkspaceMenuOverride, ...],
    ) -> None:
        self.overrides = overrides

    def list_role_menus(
        self,
        workspace_id: UUID,
        role_id: UUID,
    ) -> tuple[RoleMenuVisibility, ...]:
        return self.role_entries

    def replace_role_menus(
        self,
        workspace_id: UUID,
        role_id: UUID,
        entries: tuple[RoleMenuVisibility, ...],
    ) -> None:
        self.role_entries = entries

    def list_all_role_menus(self, workspace_id: UUID) -> tuple[RoleMenuVisibility, ...]:
        return self.role_entries

    def next_release_number(self, workspace_id: UUID) -> int:
        return len(self.releases) + 1

    def add_release(self, release: MenuRelease) -> None:
        self.releases[release.release_id] = release

    def get_release(self, workspace_id: UUID, release_id: UUID) -> MenuRelease | None:
        release = self.releases.get(release_id)
        return release if release is not None and release.workspace_id == workspace_id else None

    def list_releases(self, workspace_id: UUID) -> tuple[MenuRelease, ...]:
        return tuple(
            sorted(
                (item for item in self.releases.values() if item.workspace_id == workspace_id),
                key=lambda item: item.release_number,
                reverse=True,
            )
        )

    def save_release(self, release: MenuRelease) -> None:
        current = self.releases[release.release_id]
        assert current.snapshot == release.snapshot
        assert current.snapshot_digest == release.snapshot_digest
        self.releases[release.release_id] = release

    def get_current_release_id(self, workspace_id: UUID) -> UUID | None:
        return self.current_release_id

    def set_current_release(
        self,
        workspace_id: UUID,
        release_id: UUID,
        *,
        published_at: object,
    ) -> None:
        self.current_release_id = release_id


@dataclass
class ReleaseUnitOfWork:
    menus: ReleaseRepository

    def __post_init__(self) -> None:
        self.audit = FactWriter()
        self.outbox = FactWriter()
        self.commit_count = 0

    def __enter__(self) -> ReleaseUnitOfWork:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def commit(self) -> None:
        self.commit_count += 1


def context(workspace_id: UUID = WORKSPACE_ID) -> RequestContext:
    return RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )


def release_service(
    *,
    membership_type: str = "owner",
) -> tuple[MenuReleaseService, ReleaseRepository, ReleaseUnitOfWork]:
    repository = ReleaseRepository(membership_type=membership_type)
    unit_of_work = ReleaseUnitOfWork(repository)
    registry = load_resource_registry(ROOT / "contracts/authorization/resource-registry.v1.json")
    return MenuReleaseService(registry, unit_of_work), repository, unit_of_work


def publish_release(
    service: MenuReleaseService,
    release_id: UUID,
) -> MenuRelease:
    service.validate(context(), workspace_id=WORKSPACE_ID, release_id=release_id)
    service.decide(
        context(),
        workspace_id=WORKSPACE_ID,
        release_id=release_id,
        approved=True,
        reason=None,
    )
    return service.publish(context(), workspace_id=WORKSPACE_ID, release_id=release_id)


def test_draft_freezes_configuration_and_publish_switches_current_pointer() -> None:
    service, repository, unit_of_work = release_service()
    repository.overrides = (
        WorkspaceMenuOverride(
            WORKSPACE_ID,
            OVERVIEW_ID,
            DIRECTORY_ID,
            "首版工作台",
            "panel-top",
            10,
            True,
        ),
    )
    repository.role_entries = (RoleMenuVisibility(WORKSPACE_ID, ROLE_ID, OVERVIEW_ID, False),)

    draft = service.create_draft(context(), workspace_id=WORKSPACE_ID)
    repository.overrides = (
        WorkspaceMenuOverride(
            WORKSPACE_ID,
            OVERVIEW_ID,
            DIRECTORY_ID,
            "后续修改",
            "panel-top",
            20,
            True,
        ),
    )
    published = publish_release(service, draft.release_id)

    frozen = next(item for item in published.snapshot.menus if item.menu_id == OVERVIEW_ID)
    assert frozen.name == "首版工作台"
    assert published.status == "published"
    assert repository.current_release_id == published.release_id
    assert len(unit_of_work.audit.values) == len(unit_of_work.outbox.values) == 4


def test_unapproved_and_repeated_publish_do_not_replace_current_release() -> None:
    service, repository, _ = release_service()
    first = service.create_draft(context(), workspace_id=WORKSPACE_ID)
    first = publish_release(service, first.release_id)
    second = service.create_draft(context(), workspace_id=WORKSPACE_ID)

    with pytest.raises(MenuReleaseConflictError):
        service.publish(context(), workspace_id=WORKSPACE_ID, release_id=second.release_id)
    assert repository.current_release_id == first.release_id
    with pytest.raises(MenuReleaseConflictError):
        service.publish(context(), workspace_id=WORKSPACE_ID, release_id=first.release_id)
    assert repository.current_release_id == first.release_id


def test_failed_validation_keeps_draft_and_current_pointer_unchanged() -> None:
    service, repository, _ = release_service()
    current = service.create_draft(context(), workspace_id=WORKSPACE_ID)
    current = publish_release(service, current.release_id)
    draft = service.create_draft(context(), workspace_id=WORKSPACE_ID)
    repository.releases[draft.release_id] = replace(draft, snapshot_digest="0" * 64)

    invalid = service.validate(
        context(),
        workspace_id=WORKSPACE_ID,
        release_id=draft.release_id,
    )

    assert invalid.status == "draft"
    assert invalid.validation_errors == ("snapshot_digest 校验失败",)
    assert repository.current_release_id == current.release_id


def test_rejection_requires_reason_and_cannot_be_published() -> None:
    service, repository, _ = release_service()
    draft = service.create_draft(context(), workspace_id=WORKSPACE_ID)
    service.validate(context(), workspace_id=WORKSPACE_ID, release_id=draft.release_id)

    with pytest.raises(MenuReleaseConflictError):
        service.decide(
            context(),
            workspace_id=WORKSPACE_ID,
            release_id=draft.release_id,
            approved=False,
            reason=" ",
        )
    rejected = service.decide(
        context(),
        workspace_id=WORKSPACE_ID,
        release_id=draft.release_id,
        approved=False,
        reason="合成校验不通过",
    )
    with pytest.raises(MenuReleaseConflictError):
        service.publish(context(), workspace_id=WORKSPACE_ID, release_id=rejected.release_id)
    assert repository.current_release_id is None


def test_rollback_copies_published_snapshot_into_new_history_record() -> None:
    service, repository, _ = release_service()
    first = service.create_draft(context(), workspace_id=WORKSPACE_ID)
    first = publish_release(service, first.release_id)
    rollback = service.rollback(
        context(),
        workspace_id=WORKSPACE_ID,
        source_release_id=first.release_id,
    )

    assert rollback.release_id != first.release_id
    assert rollback.release_number == 2
    assert rollback.release_kind == "rollback"
    assert rollback.source_release_id == first.release_id
    assert rollback.snapshot == first.snapshot
    assert repository.current_release_id == rollback.release_id


def test_cross_workspace_and_api_key_governance_are_denied() -> None:
    service, _, _ = release_service()
    with pytest.raises(MenuReleaseDeniedError):
        service.create_draft(context(OTHER_WORKSPACE_ID), workspace_id=WORKSPACE_ID)
    api_context = RequestContext.trusted(
        actor_id=uuid4(),
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TRACE,
        authentication_method="api_key",
    )
    with pytest.raises(MenuReleaseDeniedError):
        service.create_draft(api_context, workspace_id=WORKSPACE_ID)


def test_active_member_can_read_current_release_but_cannot_govern_history() -> None:
    owner_service, repository, _ = release_service()
    draft = owner_service.create_draft(context(), workspace_id=WORKSPACE_ID)
    published = publish_release(owner_service, draft.release_id)
    repository.membership_type = "member"

    assert owner_service.get_current(context(), workspace_id=WORKSPACE_ID) == published
    with pytest.raises(MenuReleaseDeniedError):
        owner_service.list(context(), workspace_id=WORKSPACE_ID)
