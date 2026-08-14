from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.menu_documents import (
    menu_snapshot_to_dict,
)
from ai_platform_api.modules.authorization.domain.menus import (
    InvalidMenuReleaseTransitionError,
    MenuConfigurationWriteConflictError,
    MenuRelease,
    MenuReleaseRepository,
    MenuReleaseSnapshot,
    MenuReleaseUnitOfWork,
    MenuSnapshotItem,
    RoleMenuVisibility,
    WorkspaceMenuOverride,
)
from ai_platform_api.modules.authorization.domain.resources import Menu, ResourceRegistry
from ai_platform_api.modules.integration.domain.events import IntegrationEvent

ICON_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

__all__ = ["MenuRelease", "MenuReleaseService"]


class MenuReleaseDeniedError(PlatformError):
    error_code = "POLICY_DENIED"


class MenuReleaseNotFoundError(PlatformError):
    error_code = "RESOURCE_NOT_FOUND"


class MenuReleaseValidationError(PlatformError):
    error_code = "VALIDATION_ERROR"


class MenuReleaseConflictError(PlatformError):
    error_code = "ROLE_CONFLICT"


class MenuReleaseService:
    """管理菜单不可变快照、审批状态和当前发布指针。"""

    def __init__(
        self,
        registry: ResourceRegistry,
        unit_of_work: MenuReleaseUnitOfWork,
    ) -> None:
        self._registry = registry
        self._unit_of_work = unit_of_work

    def create_draft(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
    ) -> MenuRelease:
        account_id = _browser_owner(context, workspace_id)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.menus, workspace_id, account_id)
                _assert_binding_mirror(self._registry, unit_of_work.menus)
                menu_version = unit_of_work.menus.get_menu_version(
                    workspace_id,
                    for_update=True,
                )
                if menu_version is None:
                    raise MenuReleaseNotFoundError
                snapshot = _build_snapshot(
                    self._registry,
                    workspace_id=workspace_id,
                    menu_version=menu_version,
                    overrides=unit_of_work.menus.list_overrides(workspace_id),
                    role_menus=unit_of_work.menus.list_all_role_menus(workspace_id),
                )
                release = MenuRelease(
                    release_id=uuid4(),
                    workspace_id=workspace_id,
                    release_number=unit_of_work.menus.next_release_number(workspace_id),
                    release_kind="standard",
                    source_release_id=None,
                    status="draft",
                    snapshot=snapshot,
                    snapshot_digest=_snapshot_digest(snapshot),
                    validation_errors=(),
                    rejection_reason=None,
                    created_by_account_id=account_id,
                    decided_by_account_id=None,
                    created_at=now,
                    validated_at=None,
                    decided_at=None,
                    published_at=None,
                    version=1,
                )
                unit_of_work.menus.add_release(release)
                _record_release_change(
                    unit_of_work,
                    context=context,
                    release=release,
                    action="authorization.menu_release.create",
                    event_type="authorization.menu_release.created",
                    occurred_at=now,
                )
                unit_of_work.commit()
                return release
        except MenuConfigurationWriteConflictError as error:
            raise MenuReleaseConflictError from error

    def validate(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        release_id: UUID,
    ) -> MenuRelease:
        account_id = _browser_owner(context, workspace_id)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.menus, workspace_id, account_id)
                release = _require_release(unit_of_work.menus, workspace_id, release_id)
                errors = _snapshot_violations(self._registry, release)
                try:
                    release = release.record_validation(errors, occurred_at=now)
                except InvalidMenuReleaseTransitionError as error:
                    raise MenuReleaseConflictError from error
                unit_of_work.menus.save_release(release)
                _record_release_change(
                    unit_of_work,
                    context=context,
                    release=release,
                    action="authorization.menu_release.validate",
                    event_type="authorization.menu_release.validated",
                    occurred_at=now,
                    attributes={"valid": not errors, "error_count": len(errors)},
                )
                unit_of_work.commit()
                return release
        except MenuConfigurationWriteConflictError as error:
            raise MenuReleaseConflictError from error

    def decide(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        release_id: UUID,
        approved: bool,
        reason: str | None,
    ) -> MenuRelease:
        account_id = _browser_owner(context, workspace_id)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.menus, workspace_id, account_id)
                release = _require_release(unit_of_work.menus, workspace_id, release_id)
                try:
                    release = release.decide(
                        approved=approved,
                        account_id=account_id,
                        reason=reason.strip() if reason is not None else None,
                        occurred_at=now,
                    )
                except InvalidMenuReleaseTransitionError as error:
                    raise MenuReleaseConflictError from error
                unit_of_work.menus.save_release(release)
                _record_release_change(
                    unit_of_work,
                    context=context,
                    release=release,
                    action="authorization.menu_release.approve",
                    event_type=(
                        "authorization.menu_release.approved"
                        if approved
                        else "authorization.menu_release.rejected"
                    ),
                    occurred_at=now,
                    attributes={"approved": approved},
                )
                unit_of_work.commit()
                return release
        except MenuConfigurationWriteConflictError as error:
            raise MenuReleaseConflictError from error

    def publish(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        release_id: UUID,
    ) -> MenuRelease:
        account_id = _browser_owner(context, workspace_id)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.menus, workspace_id, account_id)
                release = _require_release(unit_of_work.menus, workspace_id, release_id)
                # 发布前重新验证冻结快照，数据库指针只有全部检查成功后才会在同一事务内切换。
                if _snapshot_violations(self._registry, release):
                    raise MenuReleaseValidationError
                try:
                    release = release.publish(occurred_at=now)
                except InvalidMenuReleaseTransitionError as error:
                    raise MenuReleaseConflictError from error
                unit_of_work.menus.save_release(release)
                unit_of_work.menus.set_current_release(
                    workspace_id,
                    release.release_id,
                    published_at=now,
                )
                _record_release_change(
                    unit_of_work,
                    context=context,
                    release=release,
                    action="authorization.menu_release.publish",
                    event_type="authorization.menu_release.published",
                    occurred_at=now,
                )
                unit_of_work.commit()
                return release
        except MenuConfigurationWriteConflictError as error:
            raise MenuReleaseConflictError from error

    def rollback(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        source_release_id: UUID,
    ) -> MenuRelease:
        account_id = _browser_owner(context, workspace_id)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.menus, workspace_id, account_id)
                menu_version = unit_of_work.menus.get_menu_version(
                    workspace_id,
                    for_update=True,
                )
                if menu_version is None:
                    raise MenuReleaseNotFoundError
                source = _require_release(
                    unit_of_work.menus,
                    workspace_id,
                    source_release_id,
                )
                if source.status != "published" or _snapshot_violations(self._registry, source):
                    raise MenuReleaseConflictError
                release = MenuRelease(
                    release_id=uuid4(),
                    workspace_id=workspace_id,
                    release_number=unit_of_work.menus.next_release_number(workspace_id),
                    release_kind="rollback",
                    source_release_id=source.release_id,
                    status="published",
                    snapshot=source.snapshot,
                    snapshot_digest=source.snapshot_digest,
                    validation_errors=(),
                    rejection_reason=None,
                    created_by_account_id=account_id,
                    decided_by_account_id=account_id,
                    created_at=now,
                    validated_at=now,
                    decided_at=now,
                    published_at=now,
                    version=1,
                )
                unit_of_work.menus.add_release(release)
                unit_of_work.menus.set_current_release(
                    workspace_id,
                    release.release_id,
                    published_at=now,
                )
                _record_release_change(
                    unit_of_work,
                    context=context,
                    release=release,
                    action="authorization.menu_release.rollback",
                    event_type="authorization.menu_release.rolled_back",
                    occurred_at=now,
                    attributes={"source_release_id": str(source.release_id)},
                )
                unit_of_work.commit()
                return release
        except MenuConfigurationWriteConflictError as error:
            raise MenuReleaseConflictError from error

    def list(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
    ) -> tuple[MenuRelease, ...]:
        account_id = _browser_owner(context, workspace_id)
        with self._unit_of_work as unit_of_work:
            _require_owner(unit_of_work.menus, workspace_id, account_id)
            return unit_of_work.menus.list_releases(workspace_id)

    def get_current(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
    ) -> MenuRelease | None:
        account_id = _browser_account(context, workspace_id)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work.menus, workspace_id, account_id)
            release_id = unit_of_work.menus.get_current_release_id(workspace_id)
            if release_id is None:
                return None
            return _require_release(unit_of_work.menus, workspace_id, release_id)


def _build_snapshot(
    registry: ResourceRegistry,
    *,
    workspace_id: UUID,
    menu_version: int,
    overrides: tuple[WorkspaceMenuOverride, ...],
    role_menus: tuple[RoleMenuVisibility, ...],
) -> MenuReleaseSnapshot:
    override_by_id = {item.menu_id: item for item in overrides}
    workspace_menus = registry.workspace_menus
    workspace_menu_ids = {item.menu_id for item in workspace_menus}
    menus = tuple(
        _snapshot_item(menu, workspace_menus, override_by_id.get(menu.menu_id))
        for menu in sorted(workspace_menus, key=lambda item: item.menu_id.int)
    )
    bindings = tuple(
        sorted(
            (
                binding.menu_id,
                binding.api_resource_id,
                binding.action_type,
            )
            for binding in registry.workspace_menu_api_bindings
        )
    )
    return MenuReleaseSnapshot(
        schema_version=1,
        registry_version=registry.registry_version,
        workspace_id=workspace_id,
        menu_version=menu_version,
        menus=menus,
        role_menus=tuple(
            sorted(
                (item for item in role_menus if item.menu_id in workspace_menu_ids),
                key=lambda item: (item.role_id.int, item.menu_id.int),
            )
        ),
        menu_api_bindings=bindings,
    )


def _snapshot_item(
    menu: Menu,
    menus: tuple[Menu, ...],
    override: WorkspaceMenuOverride | None,
) -> MenuSnapshotItem:
    parent_id = (
        override.parent_menu_id if override is not None else _registered_parent_id(menu, menus)
    )
    return MenuSnapshotItem(
        menu_id=menu.menu_id,
        menu_key=menu.menu_key,
        parent_menu_id=parent_id,
        name=override.name if override is not None else menu.name,
        menu_type=menu.menu_type,
        page_resource_id=menu.page_resource_id,
        permission_code=menu.permission_code,
        icon_key=override.icon_key if override is not None else menu.icon_key,
        sort_order=override.sort_order if override is not None else menu.sort_order,
        source=menu.source,
        status=menu.status,
        visible=override.visible if override is not None else menu.status == "active",
    )


def _registered_parent_id(menu: Menu, menus: tuple[Menu, ...]) -> UUID | None:
    if menu.parent_menu_key is None:
        return None
    parent = next((item for item in menus if item.menu_key == menu.parent_menu_key), None)
    return parent.menu_id if parent is not None else None


def _snapshot_digest(snapshot: MenuReleaseSnapshot) -> str:
    payload = json.dumps(
        menu_snapshot_to_dict(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _snapshot_violations(
    registry: ResourceRegistry,
    release: MenuRelease,
) -> tuple[str, ...]:
    snapshot = release.snapshot
    errors: list[str] = []
    if snapshot.schema_version != 1:
        errors.append("snapshot.schema_version 不受支持")
    if snapshot.registry_version > registry.registry_version:
        errors.append("snapshot.registry_version 高于当前注册表")
    if snapshot.workspace_id != release.workspace_id:
        errors.append("snapshot.workspace_id 与发布记录不一致")
    if snapshot.menu_version < 1:
        errors.append("snapshot.menu_version 必须大于等于 1")
    if _snapshot_digest(snapshot) != release.snapshot_digest:
        errors.append("snapshot_digest 校验失败")

    registered_by_id = {item.menu_id: item for item in registry.workspace_menus}
    snapshot_by_id = {item.menu_id: item for item in snapshot.menus}
    if len(snapshot_by_id) != len(snapshot.menus) or not set(snapshot_by_id).issubset(
        registered_by_id
    ):
        errors.append("snapshot.menus 包含重复或未注册的工作空间菜单")
    for menu_id, item in snapshot_by_id.items():
        registered = registered_by_id.get(menu_id)
        if registered is None:
            continue
        if (
            item.menu_key != registered.menu_key
            or item.menu_type != registered.menu_type
            or item.page_resource_id != registered.page_resource_id
            or item.permission_code != registered.permission_code
            or item.source != registered.source
            or item.status != registered.status
        ):
            errors.append(f"snapshot.menus[{menu_id}] 修改了不可覆盖字段")
        parent = snapshot_by_id.get(item.parent_menu_id) if item.parent_menu_id else None
        if item.parent_menu_id is not None and parent is None:
            errors.append(f"snapshot.menus[{menu_id}] 父节点不存在")
        if not _valid_snapshot_parent(item.menu_type, parent.menu_type if parent else None):
            errors.append(f"snapshot.menus[{menu_id}] 父节点类型非法")
        if (
            not item.name.strip()
            or len(item.name) > 80
            or item.sort_order < 0
            or (item.icon_key is not None and ICON_KEY_PATTERN.fullmatch(item.icon_key) is None)
        ):
            errors.append(f"snapshot.menus[{menu_id}] 覆盖字段非法")
    _append_cycle_errors(snapshot_by_id, errors)

    expected_bindings = tuple(
        sorted(
            (item.menu_id, item.api_resource_id, item.action_type)
            for item in registry.workspace_menu_api_bindings
            if item.menu_id in snapshot_by_id
        )
    )
    if snapshot.menu_api_bindings != expected_bindings:
        errors.append("snapshot.menu_api_bindings 与当前注册表不一致")
    role_keys = {(item.role_id, item.menu_id) for item in snapshot.role_menus}
    if len(role_keys) != len(snapshot.role_menus) or any(
        item.workspace_id != release.workspace_id or item.menu_id not in snapshot_by_id
        for item in snapshot.role_menus
    ):
        errors.append("snapshot.role_menus 包含重复或越界资源")
    return tuple(errors)


def _valid_snapshot_parent(menu_type: str, parent_type: str | None) -> bool:
    if menu_type == "directory":
        return parent_type in {None, "directory"}
    if menu_type == "page":
        return parent_type == "directory"
    return parent_type == "page"


def _append_cycle_errors(
    menus: dict[UUID, MenuSnapshotItem],
    errors: list[str],
) -> None:
    for menu_id in menus:
        visited: set[UUID] = set()
        current: UUID | None = menu_id
        while current is not None:
            if current in visited:
                errors.append(f"snapshot.menus[{menu_id}] 存在父子循环")
                break
            visited.add(current)
            item = menus.get(current)
            current = item.parent_menu_id if item is not None else None


def _browser_owner(context: RequestContext, workspace_id: UUID) -> UUID:
    return _browser_account(context, workspace_id)


def _browser_account(context: RequestContext, workspace_id: UUID) -> UUID:
    if (
        context.user_id is None
        or context.authentication_method != "browser_session"
        or context.workspace_id != workspace_id
    ):
        raise MenuReleaseDeniedError
    return context.user_id


def _require_owner(
    repository: MenuReleaseRepository,
    workspace_id: UUID,
    account_id: UUID,
) -> None:
    access = repository.get_workspace_access(workspace_id, account_id)
    if access is None or access[1:] != ("active", "owner"):
        raise MenuReleaseDeniedError


def _require_active_member(
    repository: MenuReleaseRepository,
    workspace_id: UUID,
    account_id: UUID,
) -> None:
    access = repository.get_workspace_access(workspace_id, account_id)
    if access is None or access[1] != "active":
        raise MenuReleaseDeniedError


def _assert_binding_mirror(
    registry: ResourceRegistry,
    repository: MenuReleaseRepository,
) -> None:
    expected = tuple(
        sorted(
            (binding.menu_id, binding.api_resource_id, binding.action_type)
            for binding in registry.workspace_menu_api_bindings
        )
    )
    if repository.list_registered_bindings() != expected:
        raise MenuReleaseConflictError


def _require_release(
    repository: MenuReleaseRepository,
    workspace_id: UUID,
    release_id: UUID,
) -> MenuRelease:
    release = repository.get_release(workspace_id, release_id)
    if release is None:
        raise MenuReleaseNotFoundError
    return release


def _record_release_change(
    unit_of_work: MenuReleaseUnitOfWork,
    *,
    context: RequestContext,
    release: MenuRelease,
    action: str,
    event_type: str,
    occurred_at: datetime,
    attributes: dict[str, object] | None = None,
) -> None:
    details = {
        "release_number": release.release_number,
        "release_kind": release.release_kind,
        "status": release.status,
        **(attributes or {}),
    }
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=release.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type="menu_release",
            resource_id=release.release_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            attributes=details,
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=event_type,
            workspace_id=release.workspace_id,
            aggregate_id=release.release_id,
            aggregate_version=release.version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=details,
        )
    )
