"""把 P1G-01 固定数据图装入隔离 PostgreSQL Schema。

装载器只服务测试与本地验收，不参与生产启动，不写审计或 Outbox，也不接受真实资料。
调用方负责创建空 Schema、完成 Migration、提供事务和在结束后清理数据。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid5

from ai_platform_api.modules.authorization.domain.grants import (
    MEMBER_PERMISSION_CODES,
    OWNER_PERMISSION_CODES,
    RolePermissionGrant,
    system_role_permission_seed,
)
from ai_platform_api.modules.identity.domain.entitlements import default_entitlement
from ai_platform_api.modules.identity.domain.roles import Role, RoleBinding, system_role_seed
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.persistence.tables import (
    accounts,
    department_closure,
    departments,
    document_publications,
    document_sources,
    document_versions,
    documents,
    knowledge_bases,
    membership_departments,
    membership_positions,
    positions,
    role_bindings,
    role_permission_grants,
    roles,
    workspace_entitlements,
    workspace_feature_settings,
    workspace_memberships,
    workspaces,
)
from sqlalchemy import insert
from sqlalchemy.orm import Session

from scripts.generate_p1g01_dataset import DATASET_NAMESPACE, DATASET_VERSION

JsonObject = dict[str, Any]
ROLE_NAMES = {
    "enterprise_admin": "合成企业管理员",
    "department_head": "合成部门负责人",
    "employee": "合成普通员工",
    "knowledge_manager": "合成知识管理员",
    "approver": "合成审批人",
    "external_collaborator": "合成外部协作者",
    "inactive_member": "合成停用成员",
}


@dataclass(frozen=True)
class LoadedP1G01Dataset:
    """返回装载后的稳定标识和计数，供验收断言而不暴露 Session。"""

    enterprise_workspace_id: UUID
    personal_workspace_id: UUID
    owner_account_id: UUID
    account_count: int
    enterprise_membership_count: int
    department_count: int
    document_count: int
    document_version_count: int
    publication_count: int


@dataclass(frozen=True)
class _LoadContext:
    now: datetime
    owner_account_id: UUID
    enterprise_workspace_id: UUID
    personal_workspace_id: UUID
    account_ids: dict[str, UUID]
    enterprise_membership_ids: dict[str, UUID]
    personal_membership_id: UUID
    department_ids: dict[str, UUID]
    knowledge_base_ids: dict[str, UUID]


def _records(dataset: JsonObject, key: str) -> list[JsonObject]:
    return cast(list[JsonObject], dataset[key])


def _stable_uuid(kind: str, key: str) -> UUID:
    return uuid5(DATASET_NAMESPACE, f"{DATASET_VERSION}:{kind}:{key}")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("合成数据时间必须包含时区")
    return parsed.astimezone(UTC)


def validate_p1g01_dataset(dataset: JsonObject) -> None:
    """拒绝错误版本、非合成记录和内容摘要漂移后再允许写库。"""

    if dataset.get("dataset_version") != DATASET_VERSION or dataset.get("synthetic") is not True:
        raise ValueError("只允许装载 p1g01-v1 全合成数据集")
    expected_digest = dataset.get("content_sha256")
    core = dict(dataset)
    core.pop("content_sha256", None)
    canonical = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    actual_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if expected_digest != actual_digest:
        raise ValueError("P1G-01 数据集内容摘要不一致")
    for collection_name in (
        "workspaces",
        "departments",
        "users",
        "knowledge_bases",
        "documents",
        "workflows",
        "approval_policies",
        "approval_instances",
        "scenarios",
    ):
        if any(
            record.get("synthetic") is not True or record.get("dataset_version") != DATASET_VERSION
            for record in _records(dataset, collection_name)
        ):
            raise ValueError(f"集合包含非合成或错误版本记录: {collection_name}")


def _build_context(dataset: JsonObject) -> _LoadContext:
    workspaces_by_key = {
        item["workspace_key"]: UUID(item["workspace_id"])
        for item in _records(dataset, "workspaces")
    }
    account_ids = {
        item["user_key"]: UUID(item["account_id"]) for item in _records(dataset, "users")
    }
    membership_ids = {
        item["user_key"]: UUID(item["membership_id"]) for item in _records(dataset, "users")
    }
    return _LoadContext(
        now=_parse_time(cast(str, dataset["fixed_timestamp"])),
        owner_account_id=account_ids["user-001"],
        enterprise_workspace_id=workspaces_by_key["p1g01-enterprise"],
        personal_workspace_id=workspaces_by_key["p1g01-personal-owner"],
        account_ids=account_ids,
        enterprise_membership_ids=membership_ids,
        personal_membership_id=_stable_uuid("membership", "personal-owner"),
        department_ids={
            item["department_key"]: UUID(item["department_id"])
            for item in _records(dataset, "departments")
        },
        knowledge_base_ids={
            item["knowledge_base_key"]: UUID(item["knowledge_base_id"])
            for item in _records(dataset, "knowledge_bases")
        },
    )


def _load_identity(
    session: Session,
    dataset: JsonObject,
    context: _LoadContext,
    password_hash: str,
) -> None:
    users = _records(dataset, "users")
    session.execute(
        insert(accounts),
        [
            {
                "account_id": context.account_ids[user["user_key"]],
                "login_name": user["login_name"],
                "display_name": user["display_name"],
                "password_hash": password_hash,
                "status": "active",
                "auth_version": 1,
                "created_at": context.now,
                "created_by_actor_id": context.account_ids[user["user_key"]],
                "updated_at": context.now,
                "updated_by_actor_id": context.account_ids[user["user_key"]],
                "version": 1,
            }
            for user in users
        ],
    )
    workspace_rows: list[JsonObject] = []
    for workspace in _records(dataset, "workspaces"):
        workspace_id = (
            context.enterprise_workspace_id
            if workspace["workspace_key"] == "p1g01-enterprise"
            else context.personal_workspace_id
        )
        workspace_rows.append(
            {
                "workspace_id": workspace_id,
                "workspace_type": workspace["workspace_type"],
                "name": workspace["name"],
                "owner_account_id": context.owner_account_id
                if workspace["workspace_type"] == "personal"
                else None,
                "entitlement_version": 1,
                "role_version": 1,
                "menu_version": 1,
                "status": "active",
                "created_at": context.now,
                "created_by_actor_id": context.owner_account_id,
                "updated_at": context.now,
                "updated_by_actor_id": context.owner_account_id,
                "version": 1,
            }
        )
    session.execute(insert(workspaces), workspace_rows)

    enterprise_memberships = [
        {
            "membership_id": context.enterprise_membership_ids[user["user_key"]],
            "workspace_id": context.enterprise_workspace_id,
            "account_id": context.account_ids[user["user_key"]],
            "membership_type": "owner" if user["user_key"] == "user-001" else "member",
            "status": "disabled" if user["membership_status"] == "inactive" else "active",
            "created_at": context.now,
            "updated_at": context.now,
            "version": 1,
        }
        for user in users
    ]
    personal_membership = {
        "membership_id": context.personal_membership_id,
        "workspace_id": context.personal_workspace_id,
        "account_id": context.owner_account_id,
        "membership_type": "owner",
        "status": "active",
        "created_at": context.now,
        "updated_at": context.now,
        "version": 1,
    }
    session.execute(insert(workspace_memberships), [*enterprise_memberships, personal_membership])

    workspace_entitlement_targets: tuple[tuple[UUID, Literal["personal", "enterprise"]], ...] = (
        (context.enterprise_workspace_id, "enterprise"),
        (context.personal_workspace_id, "personal"),
    )
    for workspace_id, workspace_type in workspace_entitlement_targets:
        entitlement, feature_settings = default_entitlement(
            workspace_id=workspace_id,
            workspace_type=workspace_type,
            occurred_at=context.now,
        )
        session.execute(insert(workspace_entitlements).values(**entitlement.__dict__))
        session.execute(insert(workspace_feature_settings).values(**feature_settings.__dict__))


def _department_ancestors(
    department_key: str,
    parent_by_key: dict[str, str | None],
) -> list[tuple[str, int]]:
    ancestors: list[tuple[str, int]] = []
    current: str | None = department_key
    depth = 0
    while current is not None:
        ancestors.append((current, depth))
        current = parent_by_key[current]
        depth += 1
    return ancestors


def _load_organization(session: Session, dataset: JsonObject, context: _LoadContext) -> None:
    department_records = _records(dataset, "departments")
    parent_by_key = {
        item["department_key"]: cast(str | None, item["parent_department_key"])
        for item in department_records
    }
    session.execute(
        insert(departments),
        [
            {
                "department_id": context.department_ids[item["department_key"]],
                "workspace_id": context.enterprise_workspace_id,
                "parent_department_id": context.department_ids[item["parent_department_key"]]
                if item["parent_department_key"] is not None
                else None,
                "name": item["name"],
                "status": item["status"],
                "created_at": context.now,
                "updated_at": context.now,
                "version": 1,
            }
            for item in department_records
        ],
    )
    closure_rows = [
        {
            "workspace_id": context.enterprise_workspace_id,
            "ancestor_department_id": context.department_ids[ancestor_key],
            "descendant_department_id": context.department_ids[department_key],
            "depth": depth,
        }
        for department_key in parent_by_key
        for ancestor_key, depth in _department_ancestors(department_key, parent_by_key)
    ]
    session.execute(insert(department_closure), closure_rows)

    department_assignments: list[JsonObject] = []
    position_pairs: set[tuple[str, str]] = set()
    for user in _records(dataset, "users"):
        assigned_keys = [user["primary_department_key"], *user["secondary_department_keys"]]
        for department_key in assigned_keys:
            department_assignments.append(
                {
                    "workspace_id": context.enterprise_workspace_id,
                    "membership_id": context.enterprise_membership_ids[user["user_key"]],
                    "department_id": context.department_ids[department_key],
                    "is_primary": department_key == user["primary_department_key"],
                    "assigned_at": context.now,
                }
            )
            position_pairs.add((department_key, user["position_key"]))
    session.execute(insert(membership_departments), department_assignments)

    position_ids = {
        pair: _stable_uuid("position", f"{pair[0]}:{pair[1]}") for pair in position_pairs
    }
    session.execute(
        insert(positions),
        [
            {
                "position_id": position_id,
                "workspace_id": context.enterprise_workspace_id,
                "department_id": context.department_ids[department_key],
                "name": position_key,
                "status": "active",
                "created_at": context.now,
                "updated_at": context.now,
                "version": 1,
            }
            for (department_key, position_key), position_id in sorted(position_ids.items())
        ],
    )
    session.execute(
        insert(membership_positions),
        [
            {
                "workspace_id": context.enterprise_workspace_id,
                "membership_id": context.enterprise_membership_ids[user["user_key"]],
                "position_id": position_ids[(department_key, user["position_key"])],
                "department_id": context.department_ids[department_key],
                "assigned_at": context.now,
            }
            for user in _records(dataset, "users")
            for department_key in [
                user["primary_department_key"],
                *user["secondary_department_keys"],
            ]
        ],
    )


def _role_row(role: Role) -> JsonObject:
    return {
        "role_id": role.role_id,
        "workspace_id": role.workspace_id,
        "role_key": role.role_key,
        "name": role.name,
        "status": role.status,
        "system_managed": role.system_managed,
        "created_at": role.created_at,
        "updated_at": role.updated_at,
        "version": role.version,
    }


def _binding_row(binding: RoleBinding) -> JsonObject:
    return {
        "binding_id": binding.binding_id,
        "workspace_id": binding.workspace_id,
        "role_id": binding.role_id,
        "scope_type": binding.scope_type,
        "department_id": binding.department_id,
        "membership_id": binding.membership_id,
        "status": binding.status,
        "created_at": binding.created_at,
        "revoked_at": binding.revoked_at,
        "version": binding.version,
    }


def _grant_row(grant: RolePermissionGrant) -> JsonObject:
    return {
        "workspace_id": grant.workspace_id,
        "role_id": grant.role_id,
        "permission_code": grant.permission_code,
        "scope_type": grant.scope_type,
        "department_ids": list(grant.department_ids),
        "resource_ids": list(grant.resource_ids),
        "maximum_security_level": grant.maximum_security_level,
        "field_mask": sorted(grant.field_mask),
    }


def _custom_role_permissions(role_key: str) -> tuple[tuple[str, ...], str]:
    if role_key == "enterprise_admin":
        return OWNER_PERMISSION_CODES, "RESTRICTED"
    if role_key == "knowledge_manager":
        return tuple(
            code for code in OWNER_PERMISSION_CODES if code.startswith("knowledge.")
        ), "CONFIDENTIAL"
    if role_key == "approver":
        return tuple(
            code
            for code in OWNER_PERMISSION_CODES
            if code.startswith("approval.") or code.startswith("workflow.")
        ), "CONFIDENTIAL"
    if role_key == "inactive_member":
        return (), "PUBLIC"
    return MEMBER_PERMISSION_CODES, "INTERNAL"


def _load_roles(session: Session, dataset: JsonObject, context: _LoadContext) -> None:
    system_role_rows: list[JsonObject] = []
    system_binding_rows: list[JsonObject] = []
    system_grant_rows: list[JsonObject] = []
    workspace_seeds: tuple[tuple[UUID, Literal["personal", "enterprise"], UUID], ...] = (
        (
            context.enterprise_workspace_id,
            "enterprise",
            context.enterprise_membership_ids["user-001"],
        ),
        (context.personal_workspace_id, "personal", context.personal_membership_id),
    )
    for workspace_id, workspace_type, owner_membership_id in workspace_seeds:
        seeded_roles, seeded_bindings = system_role_seed(
            workspace_id=workspace_id,
            owner_membership_id=owner_membership_id,
            occurred_at=context.now,
        )
        system_role_rows.extend(_role_row(role) for role in seeded_roles)
        system_binding_rows.extend(_binding_row(binding) for binding in seeded_bindings)
        system_grant_rows.extend(
            _grant_row(grant)
            for grant in system_role_permission_seed(
                workspace_id=workspace_id,
                workspace_type=workspace_type,
                owner_role_id=seeded_roles[0].role_id,
                member_role_id=seeded_roles[1].role_id,
            )
        )
    session.execute(insert(roles), system_role_rows)
    session.execute(insert(role_bindings), system_binding_rows)
    session.execute(insert(role_permission_grants), system_grant_rows)

    custom_role_keys = sorted(
        {
            role_key
            for user in _records(dataset, "users")
            for role_key in user["role_keys"]
            if role_key not in {"enterprise_owner", "personal_owner"}
        }
    )
    custom_role_ids = {role_key: _stable_uuid("role", role_key) for role_key in custom_role_keys}
    session.execute(
        insert(roles),
        [
            {
                "role_id": custom_role_ids[role_key],
                "workspace_id": context.enterprise_workspace_id,
                "role_key": role_key,
                "name": ROLE_NAMES[role_key],
                "status": "active",
                "system_managed": False,
                "created_at": context.now,
                "updated_at": context.now,
                "version": 1,
            }
            for role_key in custom_role_keys
        ],
    )
    session.execute(
        insert(role_bindings),
        [
            {
                "binding_id": _stable_uuid("role-binding", f"{user['user_key']}:{role_key}"),
                "workspace_id": context.enterprise_workspace_id,
                "role_id": custom_role_ids[role_key],
                "scope_type": "member",
                "department_id": None,
                "membership_id": context.enterprise_membership_ids[user["user_key"]],
                "status": "active",
                "created_at": context.now,
                "revoked_at": None,
                "version": 1,
            }
            for user in _records(dataset, "users")
            for role_key in user["role_keys"]
            if role_key in custom_role_ids
        ],
    )
    custom_grants: list[JsonObject] = []
    for role_key, role_id in custom_role_ids.items():
        permission_codes, maximum_security_level = _custom_role_permissions(role_key)
        custom_grants.extend(
            {
                "workspace_id": context.enterprise_workspace_id,
                "role_id": role_id,
                "permission_code": permission_code,
                "scope_type": "workspace",
                "department_ids": [],
                "resource_ids": [],
                "maximum_security_level": maximum_security_level,
                "field_mask": [],
            }
            for permission_code in permission_codes
        )
    if custom_grants:
        session.execute(insert(role_permission_grants), custom_grants)


def _load_knowledge(
    session: Session,
    dataset: JsonObject,
    context: _LoadContext,
) -> tuple[int, int]:
    session.execute(
        insert(knowledge_bases),
        [
            {
                "knowledge_base_id": context.knowledge_base_ids[item["knowledge_base_key"]],
                "workspace_id": context.enterprise_workspace_id
                if item["workspace_key"] == "p1g01-enterprise"
                else context.personal_workspace_id,
                "name": item["name"],
                "description": "P1G-01 全合成固定知识库",
                "default_visibility": item["default_visibility"],
                "department_ids": [context.department_ids[key] for key in item["department_keys"]],
                "default_security_level": item["default_security_level"],
                "status": item["status"],
                "created_by_account_id": context.owner_account_id,
                "created_at": context.now,
                "updated_at": context.now,
                "deleted_at": None,
                "version": 1,
            }
            for item in _records(dataset, "knowledge_bases")
        ],
    )

    document_rows: list[JsonObject] = []
    version_rows: list[JsonObject] = []
    source_rows: list[JsonObject] = []
    publication_rows: list[JsonObject] = []
    for item in _records(dataset, "documents"):
        workspace_id = (
            context.enterprise_workspace_id
            if item["workspace_key"] == "p1g01-enterprise"
            else context.personal_workspace_id
        )
        document_id = UUID(item["document_id"])
        deleted_at = context.now if item["document_status"] == "deleted" else None
        document_rows.append(
            {
                "document_id": document_id,
                "workspace_id": workspace_id,
                "knowledge_base_id": context.knowledge_base_ids[item["knowledge_base_key"]],
                "title": item["title"],
                "visibility": item["visibility"],
                "department_ids": [context.department_ids[key] for key in item["department_keys"]],
                "security_level": item["security_level"],
                "permission_labels": item["permission_labels"],
                "status": item["document_status"],
                "created_by_account_id": context.owner_account_id,
                "created_at": context.now,
                "updated_at": context.now,
                "deleted_at": deleted_at,
                "version": 2 if deleted_at is not None else 1,
            }
        )
        published_version_id: UUID | None = None
        for version in item["versions"]:
            version_id = _stable_uuid(
                "document-version",
                f"{item['document_key']}:{version['version_number']}",
            )
            status = version["status"]
            version_rows.append(
                {
                    "document_version_id": version_id,
                    "workspace_id": workspace_id,
                    "document_id": document_id,
                    "version_number": version["version_number"],
                    "status": status,
                    "content_hash": None if status == "draft" else version["content_hash"],
                    "created_by_account_id": context.owner_account_id,
                    "created_at": context.now,
                    "published_at": context.now if status in {"published", "superseded"} else None,
                    "record_version": 1,
                }
            )
            source_rows.append(
                {
                    "source_id": _stable_uuid("document-source", str(version_id)),
                    "workspace_id": workspace_id,
                    "document_version_id": version_id,
                    "source_kind": "manual",
                    "source_name": item["file_name"],
                    "original_object_key": None,
                    "source_path": None,
                    "source_url": None,
                    "external_source_id": None,
                    "captured_at": context.now,
                    "created_at": context.now,
                    "media_type": None,
                    "size_bytes": None,
                    "content_hash": None,
                    "scan_status": None,
                    "scanner_version": None,
                    "scanned_at": None,
                }
            )
            if status == "published":
                published_version_id = version_id
        if published_version_id is not None and item["document_status"] == "active":
            publication_rows.append(
                {
                    "workspace_id": workspace_id,
                    "document_id": document_id,
                    "current_document_version_id": published_version_id,
                    "published_at": context.now,
                }
            )
    session.execute(insert(documents), document_rows)
    session.execute(insert(document_versions), version_rows)
    session.execute(insert(document_sources), source_rows)
    session.execute(insert(document_publications), publication_rows)
    return len(version_rows), len(publication_rows)


def load_p1g01_dataset(
    session: Session,
    dataset: JsonObject,
    *,
    local_password: str,
) -> LoadedP1G01Dataset:
    """在调用方事务中一次装入固定数据集，失败时由调用方整体回滚。

    `local_password` 仅在内存中生成 Argon2id Hash；装载器不读取环境变量，也不把密码
    写回 Fixture、日志或返回值。该入口只允许对空的隔离测试 Schema 调用一次。
    """

    # 1. 写库前先锁定版本、合成标记和摘要，避免把未知数据伪装成验收集。
    validate_p1g01_dataset(dataset)
    context = _build_context(dataset)
    password_hash = Argon2idPasswordAdapter().hash(local_password)

    # 2. 按外键依赖顺序装入身份、组织、角色和知识事实，共用调用方单一事务。
    _load_identity(session, dataset, context, password_hash)
    _load_organization(session, dataset, context)
    _load_roles(session, dataset, context)
    version_count, publication_count = _load_knowledge(session, dataset, context)

    # 3. 返回不含凭证和正文的稳定计数，供后续联合场景确认装载基线。
    return LoadedP1G01Dataset(
        enterprise_workspace_id=context.enterprise_workspace_id,
        personal_workspace_id=context.personal_workspace_id,
        owner_account_id=context.owner_account_id,
        account_count=len(_records(dataset, "users")),
        enterprise_membership_count=len(_records(dataset, "users")),
        department_count=len(_records(dataset, "departments")),
        document_count=len(_records(dataset, "documents")),
        document_version_count=version_count,
        publication_count=publication_count,
    )
