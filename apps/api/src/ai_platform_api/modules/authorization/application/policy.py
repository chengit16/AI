from __future__ import annotations

from uuid import UUID, uuid4

from ai_platform_api.modules.authorization.domain.fields import (
    SECURITY_LEVEL_RANK,
    FieldPolicyRegistry,
    SecurityLevel,
)
from ai_platform_api.modules.authorization.domain.grants import (
    PolicyGrantReader,
    RolePermissionGrant,
)
from ai_platform_api.modules.authorization.domain.policy import (
    PolicyDecision,
    PolicyDecisionPoint,
    PolicyRequest,
    ResourceReference,
    ResourceScope,
)
from ai_platform_api.modules.authorization.domain.resources import ApiResource, ResourceRegistry

__all__ = [
    "ApiResource",
    "PolicyDecisionPoint",
    "PolicyRequest",
    "RbacPolicyDecisionPoint",
    "ResourceReference",
    "ResourceRegistry",
]


class RbacPolicyDecisionPoint:
    """以注册表、可信主体、角色授权和数据范围形成唯一策略决策。"""

    def __init__(
        self,
        registry: ResourceRegistry,
        grants: PolicyGrantReader,
        field_registry: FieldPolicyRegistry | None = None,
    ) -> None:
        self._registry = registry
        self._grants = grants
        self._field_registry = field_registry or FieldPolicyRegistry(1, 1, ())

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        try:
            return self._decide(request)
        except Exception:
            # 策略存储、组织树或角色事实异常时不能降级为放行。
            return self._denied(request, "policy_unavailable")

    def _decide(self, request: PolicyRequest) -> PolicyDecision:
        if request.resource.workspace_id != request.context.workspace_id:
            return self._denied(request, "workspace_mismatch")
        permission = next(
            (
                item
                for item in self._registry.permissions
                if item.code == request.permission_code and item.status == "active"
            ),
            None,
        )
        if permission is None or permission.resource_type != request.resource.resource_type:
            return self._denied(request, "permission_not_registered")
        if (
            request.context.credential_scopes is not None
            and request.permission_code not in request.context.credential_scopes
        ):
            return self._denied(request, "credential_scope_denied")

        subject = self._grants.resolve_subject(request.context)
        if subject is None or not subject.role_ids:
            return self._denied(request, "subject_not_active")
        matching = tuple(
            grant
            for grant in self._grants.list_role_grants(
                request.context.workspace_id,
                subject.role_ids,
            )
            if grant.permission_code == request.permission_code
        )
        if not matching:
            return self._denied(request, "permission_not_granted")

        department_roots = frozenset(
            department_id
            for grant in matching
            if grant.scope_type == "department_tree"
            for department_id in grant.department_ids
        )
        scope = ResourceScope(
            workspace=any(grant.scope_type == "workspace" for grant in matching),
            department_ids=self._grants.expand_department_tree(
                request.context.workspace_id,
                department_roots,
            )
            if department_roots
            else frozenset(),
            account_ids=frozenset({subject.account_id})
            if any(grant.scope_type == "self" for grant in matching)
            else frozenset(),
            resource_ids=frozenset(
                resource_id
                for grant in matching
                if grant.scope_type == "resource"
                for resource_id in grant.resource_ids
            ),
        )
        if not _resource_matches_scope(request, scope):
            return self._denied(
                request,
                "resource_out_of_scope",
                policy_version=subject.role_version,
                scope=scope,
            )
        maximum_security_level, explicit_mask = self._field_access(
            request,
            subject.account_id,
            matching,
        )
        field_mask = self._field_registry.field_mask(
            request.resource.resource_type,
            maximum_security_level,
            request.resource.attributes,
        ) | frozenset(explicit_mask)
        high_risk = request.resource.attributes.get("risk_level") in {"high", "critical"}
        sensitive = bool(field_mask) or self._field_registry.contains_sensitive_fields(
            request.resource.resource_type
        )
        return PolicyDecision(
            decision_id=uuid4(),
            decision="allow",
            permission_code=request.permission_code,
            workspace_id=request.context.workspace_id,
            resource_scope=scope,
            field_mask=field_mask,
            policy_version=subject.role_version,
            cache_ttl_seconds=0 if high_risk or sensitive else 30,
            reason="role_permission_granted",
        )

    def _field_access(
        self,
        request: PolicyRequest,
        subject_account_id: UUID,
        grants: tuple[RolePermissionGrant, ...],
    ) -> tuple[SecurityLevel, frozenset[str]]:
        if _is_collection_request(request):
            # 集合查询共用一个 field_mask，必须采用所有数据范围都能承受的最小字段权限。
            level = min(
                (grant.maximum_security_level for grant in grants),
                key=SECURITY_LEVEL_RANK.__getitem__,
            )
            return level, frozenset(
                field_name for grant in grants for field_name in grant.field_mask
            )

        applicable = tuple(
            grant
            for grant in grants
            if self._grant_matches_resource(request, subject_account_id, grant)
        )
        if not applicable:
            # 资源范围合并和字段范围计算不一致时失败关闭，不能返回宽松字段结果。
            raise ValueError("字段授权没有覆盖目标资源")
        level = max(
            (grant.maximum_security_level for grant in applicable),
            key=SECURITY_LEVEL_RANK.__getitem__,
        )
        explicit_mask = set(applicable[0].field_mask)
        for grant in applicable[1:]:
            explicit_mask.intersection_update(grant.field_mask)
        return level, frozenset(explicit_mask)

    def _grant_matches_resource(
        self,
        request: PolicyRequest,
        subject_account_id: UUID,
        grant: RolePermissionGrant,
    ) -> bool:
        if grant.scope_type == "workspace":
            return True
        attributes = request.resource.attributes
        account_id = _uuid_attribute(attributes.get("account_id"))
        department_id = _uuid_attribute(attributes.get("department_id"))
        if grant.scope_type == "self":
            return account_id == subject_account_id
        if grant.scope_type == "resource":
            return request.resource.resource_id in grant.resource_ids
        return department_id in self._grants.expand_department_tree(
            request.context.workspace_id,
            grant.department_ids,
        )

    @staticmethod
    def _denied(
        request: PolicyRequest,
        reason: str,
        *,
        policy_version: int = 1,
        scope: ResourceScope | None = None,
    ) -> PolicyDecision:
        return PolicyDecision(
            decision_id=uuid4(),
            decision="deny",
            permission_code=request.permission_code,
            workspace_id=request.context.workspace_id,
            resource_scope=scope or ResourceScope(),
            field_mask=frozenset(),
            policy_version=policy_version,
            cache_ttl_seconds=0,
            reason=reason,
        )


def _resource_matches_scope(request: PolicyRequest, scope: ResourceScope) -> bool:
    if scope.workspace:
        return True
    attributes = request.resource.attributes
    account_id = _uuid_attribute(attributes.get("account_id"))
    department_id = _uuid_attribute(attributes.get("department_id"))
    if account_id is not None:
        return account_id in scope.account_ids or request.resource.resource_id in scope.resource_ids
    if department_id is not None:
        return (
            department_id in scope.department_ids
            or request.resource.resource_id in scope.resource_ids
        )
    if request.resource.resource_id != request.context.workspace_id:
        return request.resource.resource_id in scope.resource_ids
    # 集合读取允许进入用例，Repository 必须继续消费决策中的可执行范围。
    return bool(scope.department_ids or scope.account_ids or scope.resource_ids)


def _is_collection_request(request: PolicyRequest) -> bool:
    attributes = request.resource.attributes
    return (
        request.resource.resource_id == request.context.workspace_id
        and _uuid_attribute(attributes.get("account_id")) is None
        and _uuid_attribute(attributes.get("department_id")) is None
    )


def _uuid_attribute(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None
