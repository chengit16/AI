"""实现服务定义、访问策略和启停状态的受控更新。"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.service_governance.application.errors import (
    ServiceRouteConflictError,
    ServiceValidationError,
)
from ai_platform_api.modules.service_governance.application.support import (
    UPDATE_SERVICE_OPERATION,
    access_policy_digest,
    browser_account,
    deployment_from_request,
    document_digest,
    normalize_name,
    normalize_policy_subjects,
    raise_write_conflict,
    record_service_change,
    require_deployment,
    require_idempotency_key,
    require_request_hash,
    require_routable_release,
    require_service_scope,
    service_request,
)
from ai_platform_api.modules.service_governance.domain.models import (
    ServiceAccessPolicyVersion,
    ServiceAccessVisibility,
    ServiceDeployment,
    ServiceGovernanceUnitOfWork,
    ServiceStatus,
    ServiceWriteConflictError,
)


def update_service(
    unit_of_work_factory: ServiceGovernanceUnitOfWork,
    context: RequestContext,
    *,
    service_id: UUID,
    expected_version: int,
    name: str | None,
    target_status: ServiceStatus | None,
    visibility: str | None,
    allowed_department_ids: tuple[UUID, ...],
    allowed_account_ids: tuple[UUID, ...],
    idempotency_key: str,
) -> ServiceDeployment:
    """乐观锁更新服务，并让访问策略变更产生不可变新版本。"""

    # 长函数保留原因: 该流程刻意完整展示单事务内的重放、锁定、门禁、写入和事件顺序，
    # 拆成多个可写 helper 会隐藏 Unit of Work 边界并增加部分提交被误用的风险。
    # 1. 规范可选字段并冻结完整请求摘要，避免重试时 None 与空集合语义漂移。
    account_id = browser_account(context)
    require_service_scope(context, service_id)
    if expected_version < 1 or target_status == "draft":
        raise ServiceValidationError
    normalized_name = normalize_name(name) if name is not None else None
    normalized_policy = _optional_policy(
        visibility,
        allowed_department_ids,
        allowed_account_ids,
    )
    require_idempotency_key(idempotency_key)
    request_hash = document_digest(
        {
            "operation": UPDATE_SERVICE_OPERATION,
            "service_id": str(service_id),
            "expected_version": expected_version,
            "name": normalized_name,
            "target_status": target_status,
            "policy": _policy_request_document(normalized_policy),
        }
    )
    now = datetime.now(UTC)
    try:
        with unit_of_work_factory as unit_of_work:
            # 2. 相同请求返回原始响应快照，不受后续服务状态和路由变化影响。
            request = unit_of_work.services.get_request(
                context.workspace_id,
                context.actor_id,
                UPDATE_SERVICE_OPERATION,
                idempotency_key,
            )
            if request is not None:
                require_request_hash(request, request_hash)
                return deployment_from_request(request)

            current = require_deployment(
                unit_of_work.services,
                context.workspace_id,
                service_id,
                for_update=True,
            )
            if current.service.version != expected_version:
                raise ServiceRouteConflictError
            next_status = target_status or current.service.status
            _require_transition(current.service.status, next_status)
            policy = _next_policy(
                unit_of_work,
                context,
                current,
                normalized_policy,
                account_id=account_id,
                now=now,
            )
            next_name = normalized_name or current.service.name
            if (
                next_name == current.service.name
                and next_status == current.service.status
                and policy == current.access_policy
            ):
                raise ServiceValidationError

            # 3. 激活时重新核对当前 Route，避免恢复一个已失效或跨 Agent 的 Release。
            if next_status == "active":
                release = require_routable_release(
                    unit_of_work.services.get_routable_release(
                        context.workspace_id,
                        current.route.primary_release_id,
                        for_share=True,
                    ),
                    expected_kind=(
                        "system" if current.service.service_type == "system_assistant" else "custom"
                    ),
                )
                if release.agent_id != current.service.agent_id:
                    raise ServiceRouteConflictError

            if policy != current.access_policy:
                unit_of_work.services.add_access_policy(policy)
            updated_service = replace(
                current.service,
                name=next_name,
                status=next_status,
                access_policy_version_id=policy.access_policy_version_id,
                updated_by_account_id=account_id,
                updated_at=now,
                version=current.service.version + 1,
            )
            if not unit_of_work.services.save_service(
                updated_service,
                expected_version=current.service.version,
            ):
                raise ServiceRouteConflictError
            deployment = ServiceDeployment(
                updated_service,
                policy,
                current.route,
                current.publication,
            )
            unit_of_work.services.add_request(
                service_request(
                    context,
                    UPDATE_SERVICE_OPERATION,
                    idempotency_key,
                    request_hash,
                    deployment,
                    now,
                )
            )
            record_service_change(
                unit_of_work,
                context,
                action="service.definition.updated",
                deployment=deployment,
                occurred_at=now,
                attributes={"previous_status": current.service.status},
            )
            unit_of_work.commit()
            return deployment
    except ServiceWriteConflictError as error:
        replayed = _recover_update(
            unit_of_work_factory,
            context,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replayed is not None:
            return replayed
        raise_write_conflict(error)


def _optional_policy(
    visibility: str | None,
    department_ids: tuple[UUID, ...],
    account_ids: tuple[UUID, ...],
) -> tuple[str, tuple[UUID, ...], tuple[UUID, ...]] | None:
    """区分“不修改策略”和“提交新策略”，避免空集合被误解释。"""

    if visibility is None:
        if department_ids or account_ids:
            raise ServiceValidationError
        return None
    return normalize_policy_subjects(visibility, department_ids, account_ids)


def _policy_request_document(
    policy: tuple[str, tuple[UUID, ...], tuple[UUID, ...]] | None,
) -> dict[str, object] | None:
    if policy is None:
        return None
    return {
        "visibility": policy[0],
        "allowed_department_ids": [str(value) for value in policy[1]],
        "allowed_account_ids": [str(value) for value in policy[2]],
    }


def _require_transition(current: ServiceStatus, target: ServiceStatus) -> None:
    """执行冻结服务状态机；归档终态和从活动直接再激活均不能绕过。"""

    allowed: dict[ServiceStatus, frozenset[ServiceStatus]] = {
        "draft": frozenset({"active", "archived"}),
        "active": frozenset({"active", "suspended", "archived"}),
        "suspended": frozenset({"suspended", "active", "archived"}),
        "archived": frozenset(),
    }
    if target not in allowed[current]:
        raise ServiceValidationError


def _next_policy(
    unit_of_work: ServiceGovernanceUnitOfWork,
    context: RequestContext,
    current: ServiceDeployment,
    normalized: tuple[str, tuple[UUID, ...], tuple[UUID, ...]] | None,
    *,
    account_id: UUID,
    now: datetime,
) -> ServiceAccessPolicyVersion:
    """验证新策略主体并生成单调版本；未修改时复用当前不可变策略。"""

    if normalized is None:
        return current.access_policy
    if not unit_of_work.services.subjects_exist(
        context.workspace_id,
        normalized[1],
        normalized[2],
    ):
        raise ServiceValidationError
    version = unit_of_work.services.next_access_policy_version(
        context.workspace_id,
        current.service.service_id,
    )
    return ServiceAccessPolicyVersion(
        access_policy_version_id=uuid4(),
        service_id=current.service.service_id,
        workspace_id=context.workspace_id,
        version=version,
        visibility=cast("ServiceAccessVisibility", normalized[0]),
        allowed_department_ids=normalized[1],
        allowed_account_ids=normalized[2],
        policy_hash=access_policy_digest(
            service_id=current.service.service_id,
            version=version,
            visibility=normalized[0],
            department_ids=normalized[1],
            account_ids=normalized[2],
        ),
        created_by_account_id=account_id,
        created_at=now,
    )


def _recover_update(
    unit_of_work_factory: ServiceGovernanceUnitOfWork,
    context: RequestContext,
    *,
    idempotency_key: str,
    request_hash: str,
) -> ServiceDeployment | None:
    """在乐观锁或幂等唯一键竞争后恢复已提交的同一更新。"""

    with unit_of_work_factory as unit_of_work:
        request = unit_of_work.services.get_request(
            context.workspace_id,
            context.actor_id,
            UPDATE_SERVICE_OPERATION,
            idempotency_key,
        )
        if request is None:
            return None
        require_request_hash(request, request_hash)
        return deployment_from_request(request)
