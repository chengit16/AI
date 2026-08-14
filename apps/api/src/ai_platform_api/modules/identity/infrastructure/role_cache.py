"""使用 Valkey 缓存有效角色版本，并在授权事实变化后精确失效。"""

from __future__ import annotations

import json
from typing import cast
from uuid import UUID

from redis import Redis

from ai_platform_api.modules.identity.domain.roles import (
    EffectiveRole,
    EffectiveRoleSet,
    EffectiveRoleSource,
    RoleScopeType,
)


class ValkeyRoleResolutionCache:
    """缓存键包含 PostgreSQL 角色版本，旧值无需同步删除也不会再次命中。"""

    def __init__(self, url: str, ttl_seconds: int = 300) -> None:
        self._client = Redis.from_url(url, decode_responses=True)
        self._ttl_seconds = ttl_seconds

    def get(
        self, workspace_id: UUID, membership_id: UUID, role_version: int
    ) -> EffectiveRoleSet | None:
        payload = self._client.get(self._key(workspace_id, membership_id, role_version))
        if not isinstance(payload, str):
            return None
        try:
            document: object = json.loads(payload)
            if not isinstance(document, dict) or document.get("role_version") != role_version:
                return None
            roles = document.get("roles")
            account_id = document.get("account_id")
            if not isinstance(roles, list) or not isinstance(account_id, str):
                return None
            effective_roles: list[EffectiveRole] = []
            for item in roles:
                if not isinstance(item, dict) or not isinstance(item.get("sources"), list):
                    return None
                effective_roles.append(
                    EffectiveRole(
                        UUID(str(item["role_id"])),
                        str(item["role_key"]),
                        str(item["name"]),
                        tuple(
                            EffectiveRoleSource(
                                self._scope_type(source["scope_type"]),
                                UUID(str(source["scope_id"])),
                            )
                            for source in item["sources"]
                            if isinstance(source, dict)
                        ),
                    )
                )
            return EffectiveRoleSet(
                workspace_id,
                UUID(account_id),
                membership_id,
                role_version,
                tuple(effective_roles),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def put(self, role_set: EffectiveRoleSet) -> None:
        payload = {
            "account_id": str(role_set.account_id),
            "role_version": role_set.role_version,
            "roles": [
                {
                    "role_id": str(role.role_id),
                    "role_key": role.role_key,
                    "name": role.name,
                    "sources": [
                        {
                            "scope_type": source.scope_type,
                            "scope_id": str(source.scope_id),
                        }
                        for source in role.sources
                    ],
                }
                for role in role_set.roles
            ],
        }
        self._client.set(
            self._key(
                role_set.workspace_id,
                role_set.membership_id,
                role_set.role_version,
            ),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            ex=self._ttl_seconds,
        )

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _key(workspace_id: UUID, membership_id: UUID, role_version: int) -> str:
        return f"effective-roles:v1:{workspace_id}:{membership_id}:{role_version}"

    @staticmethod
    def _scope_type(value: object) -> RoleScopeType:
        if value not in {"workspace", "department", "member"}:
            raise ValueError("角色缓存包含未知范围")
        return cast("RoleScopeType", value)
