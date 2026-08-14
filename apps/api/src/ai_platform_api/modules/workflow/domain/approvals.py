"""定义版本化多级审批策略、审批人来源和确定性审批链计算。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol, TypeGuard, cast
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter

from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.integration.domain.events import OutboxWriter

ApprovalPolicyStatus = Literal["active", "disabled"]
ApprovalLevelMode = Literal["any", "all"]
ApprovalSourceType = Literal["accounts", "roles", "department_managers", "upper_managers"]
ApprovalTimeoutAction = Literal["escalate", "transfer", "reject", "wait"]
ApprovalRiskLevel = Literal["normal", "high", "critical"]
ApprovalConditionOperator = Literal[
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "contains",
    "exists",
]

MAX_APPROVAL_LEVELS = 5
MAX_APPROVER_REFERENCES = 50
MAX_FIELD_CONDITIONS = 20
_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_FIELD_PATH_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}(?:\.[a-z][a-z0-9_-]{0,63}){0,7}$")
_MISSING = object()


@dataclass(frozen=True)
class ApprovalFieldCondition:
    """使用固定操作符匹配服务端审批主题字段，禁止解释任意脚本。"""

    field_path: str
    operator: ApprovalConditionOperator
    expected: object | None = None


@dataclass(frozen=True)
class ApprovalApproverSource:
    """描述指定人员、角色或负责人职位等可解析审批人来源。"""

    source_type: ApprovalSourceType
    reference_ids: tuple[UUID, ...]
    levels_up: int | None = None


@dataclass(frozen=True)
class ApprovalLevelDefinition:
    """冻结一个串行审批层级的通过模式、超时策略和候补来源。"""

    sequence_no: int
    mode: ApprovalLevelMode
    sources: tuple[ApprovalApproverSource, ...]
    reminder_after_minutes: int = 24 * 60
    timeout_after_minutes: int = 72 * 60
    timeout_action: ApprovalTimeoutAction = "wait"
    fallback_sources: tuple[ApprovalApproverSource, ...] = ()


@dataclass(frozen=True)
class ApprovalPolicyDefinition:
    """冻结资源、操作、组织、密级、风险、字段条件和最多五级审批规则。"""

    resource_type: str
    operation: str
    priority: int
    department_ids: tuple[UUID, ...]
    security_levels: tuple[SecurityLevel, ...]
    risk_levels: tuple[ApprovalRiskLevel, ...]
    field_conditions: tuple[ApprovalFieldCondition, ...]
    levels: tuple[ApprovalLevelDefinition, ...]
    allow_self_approval: bool = False


@dataclass(frozen=True)
class ApprovalPolicy:
    """表示审批策略稳定身份、当前不可变版本指针和乐观锁版本。"""

    approval_policy_id: UUID
    workspace_id: UUID
    name: str
    status: ApprovalPolicyStatus
    current_version_id: UUID
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class ApprovalPolicyVersion:
    """保存只增不改的审批规则快照及其规范摘要。"""

    approval_policy_version_id: UUID
    approval_policy_id: UUID
    workspace_id: UUID
    version_number: int
    definition: ApprovalPolicyDefinition
    definition_digest: str
    created_by_account_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class ApprovalWorkspaceIdentity:
    """提供审批计算需要的空间类型和唯一所有者，不暴露其他身份字段。"""

    workspace_id: UUID
    workspace_type: Literal["personal", "enterprise"]
    owner_account_id: UUID


@dataclass(frozen=True)
class ApprovalMemberIdentity:
    """提供申请人的活动状态、全部部门和主部门组织位置。"""

    account_id: UUID
    active: bool
    department_ids: tuple[UUID, ...]
    primary_department_id: UUID | None


@dataclass(frozen=True)
class ApprovalSubject:
    """承载一次审批链计算的可信资源、风险、组织与结构化字段输入。"""

    workspace_id: UUID
    requester_account_id: UUID
    resource_type: str
    operation: str
    resource_id: UUID | None
    department_ids: tuple[UUID, ...]
    security_level: SecurityLevel
    risk_level: ApprovalRiskLevel
    fields: dict[str, object]


@dataclass(frozen=True)
class ResolvedApprovalLevel:
    """返回一个层级的稳定审批人集合与超时执行参数。"""

    sequence_no: int
    mode: ApprovalLevelMode
    approver_account_ids: tuple[UUID, ...]
    reminder_after_minutes: int
    timeout_after_minutes: int
    timeout_action: ApprovalTimeoutAction
    fallback_approver_account_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class ApprovalChain:
    """返回已冻结策略版本计算出的审批链和可供 P1F-04 绑定的摘要。"""

    workspace_id: UUID
    approval_policy_id: UUID | None
    approval_policy_version_id: UUID | None
    personal_owner_confirmation: bool
    levels: tuple[ResolvedApprovalLevel, ...]
    chain_digest: str


class ApprovalPolicyValidationError(Exception):
    """审批策略定义包含无效条件、层级、来源或超时配置。"""


class ApprovalPolicyConflictError(Exception):
    """同等优先级与特异度的策略同时命中，无法安全选择唯一规则。"""


class ApprovalPolicyNotMatchedError(Exception):
    """企业审批主题没有命中任何活动策略。"""


class ApprovalApproverUnavailableError(Exception):
    """审批层级没有可用审批人，或企业禁止自批后候选为空。"""

    def __init__(self, sequence_no: int) -> None:
        self.sequence_no = sequence_no
        super().__init__(str(sequence_no))


class ApprovalPolicyWriteConflictError(Exception):
    """审批策略名称、版本号或乐观锁竞争拒绝本次写入。"""


class ApprovalPolicyRepository(Protocol):
    """在工作空间边界内维护策略身份、不可变版本和审批目录读取。"""

    def list_policies(self, workspace_id: UUID) -> tuple[ApprovalPolicy, ...]: ...

    def get_policy(
        self,
        workspace_id: UUID,
        approval_policy_id: UUID,
        *,
        for_update: bool = False,
    ) -> ApprovalPolicy | None: ...

    def get_version(
        self,
        workspace_id: UUID,
        approval_policy_version_id: UUID,
    ) -> ApprovalPolicyVersion | None: ...

    def list_active_versions(self, workspace_id: UUID) -> tuple[ApprovalPolicyVersion, ...]: ...

    def next_version_number(self, workspace_id: UUID, approval_policy_id: UUID) -> int: ...

    def add_policy(self, policy: ApprovalPolicy, version: ApprovalPolicyVersion) -> None: ...

    def revise_policy(
        self,
        policy: ApprovalPolicy,
        version: ApprovalPolicyVersion,
        *,
        expected_version: int,
    ) -> bool: ...


class ApprovalPolicyUnitOfWork(Protocol):
    """保证审批策略指针、不可变版本、审计和 Outbox 原子提交。"""

    @property
    def approvals(self) -> ApprovalPolicyRepository: ...

    @property
    def directory(self) -> ApprovalOrganizationDirectory: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> ApprovalPolicyUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class ApprovalOrganizationDirectory(Protocol):
    """从组织事实解析空间、申请人和单个审批来源的活动账号集合。"""

    def workspace_identity(self, workspace_id: UUID) -> ApprovalWorkspaceIdentity | None: ...

    def member_identity(
        self,
        workspace_id: UUID,
        account_id: UUID,
    ) -> ApprovalMemberIdentity | None: ...

    def resolve_source(
        self,
        workspace_id: UUID,
        source: ApprovalApproverSource,
        *,
        requester: ApprovalMemberIdentity,
        resource_department_ids: tuple[UUID, ...],
    ) -> tuple[UUID, ...]: ...


def validate_approval_definition(definition: ApprovalPolicyDefinition) -> None:
    """完整校验声明式审批定义，任何歧义都在创建版本前失败关闭。"""

    if (
        not _KEY_PATTERN.fullmatch(definition.resource_type)
        or not _KEY_PATTERN.fullmatch(definition.operation)
        or not 0 <= definition.priority <= 10_000
        or len(set(definition.department_ids)) != len(definition.department_ids)
        or len(set(definition.security_levels)) != len(definition.security_levels)
        or len(set(definition.risk_levels)) != len(definition.risk_levels)
        or len(definition.field_conditions) > MAX_FIELD_CONDITIONS
        or not 1 <= len(definition.levels) <= MAX_APPROVAL_LEVELS
    ):
        raise ApprovalPolicyValidationError

    expected_sequences = tuple(range(1, len(definition.levels) + 1))
    if tuple(level.sequence_no for level in definition.levels) != expected_sequences:
        raise ApprovalPolicyValidationError

    # 1. 字段条件只接受有界路径、固定操作符和可规范序列化值，金额沿用数值比较能力。
    condition_paths: set[str] = set()
    for condition in definition.field_conditions:
        if (
            not _FIELD_PATH_PATTERN.fullmatch(condition.field_path)
            or condition.field_path in condition_paths
            or condition.operator
            not in {
                "eq",
                "ne",
                "gt",
                "gte",
                "lt",
                "lte",
                "in",
                "contains",
                "exists",
            }
            or (condition.operator != "exists" and condition.expected is None)
            or (condition.operator == "exists" and condition.expected is not None)
            or (
                condition.operator in {"gt", "gte", "lt", "lte"} and not _number(condition.expected)
            )
            or (
                condition.operator == "in"
                and (not isinstance(condition.expected, (list, tuple)) or not condition.expected)
            )
        ):
            raise ApprovalPolicyValidationError
        _canonical_json(condition.expected)
        condition_paths.add(condition.field_path)

    # 2. 层级间严格串行；负责人来源必须指定代表负责人身份的活动职位集合。
    for level in definition.levels:
        if (
            level.mode not in {"any", "all"}
            or not level.sources
            or not 1 <= level.reminder_after_minutes < level.timeout_after_minutes <= 43_200
            or level.timeout_action not in {"escalate", "transfer", "reject", "wait"}
            or (level.timeout_action in {"escalate", "transfer"} and not level.fallback_sources)
        ):
            raise ApprovalPolicyValidationError
        _validate_sources(level.sources)
        _validate_sources(level.fallback_sources)


def approval_definition_document(definition: ApprovalPolicyDefinition) -> dict[str, object]:
    """生成稳定可持久化文档，供版本摘要、数据库和 API 共用。"""

    return {
        "schema_version": 1,
        "allow_self_approval": definition.allow_self_approval,
        "department_ids": [str(item) for item in sorted(definition.department_ids, key=str)],
        "field_conditions": [
            {
                "expected": condition.expected,
                "field_path": condition.field_path,
                "operator": condition.operator,
            }
            for condition in definition.field_conditions
        ],
        "levels": [_level_document(level) for level in definition.levels],
        "operation": definition.operation,
        "priority": definition.priority,
        "resource_type": definition.resource_type,
        "risk_levels": sorted(definition.risk_levels),
        "security_levels": sorted(definition.security_levels),
    }


def approval_definition_from_document(document: object) -> ApprovalPolicyDefinition:
    """从版本化 JSON 恢复审批定义，损坏或未知结构按无效策略失败关闭。"""

    try:
        value = _object_document(document)
        if value.get("schema_version") != 1:
            raise ApprovalPolicyValidationError
        priority = _integer(value.get("priority"))
        allow_self_approval = value.get("allow_self_approval")
        if not isinstance(allow_self_approval, bool):
            raise ApprovalPolicyValidationError
        definition = ApprovalPolicyDefinition(
            resource_type=_text(value.get("resource_type")),
            operation=_text(value.get("operation")),
            priority=priority,
            department_ids=_uuid_items(value.get("department_ids")),
            security_levels=_security_level_items(value.get("security_levels")),
            risk_levels=_risk_level_items(value.get("risk_levels")),
            field_conditions=_field_condition_items(value.get("field_conditions")),
            levels=_level_items(value.get("levels")),
            allow_self_approval=allow_self_approval,
        )
        validate_approval_definition(definition)
        return definition
    except (KeyError, TypeError, ValueError) as error:
        raise ApprovalPolicyValidationError from error


def approval_definition_digest(definition: ApprovalPolicyDefinition) -> str:
    """计算审批定义规范 JSON 的 SHA-256，防止历史版本被静默替换。"""

    validate_approval_definition(definition)
    return hashlib.sha256(_canonical_json(approval_definition_document(definition))).hexdigest()


def select_approval_policy(
    versions: tuple[ApprovalPolicyVersion, ...],
    subject: ApprovalSubject,
) -> ApprovalPolicyVersion:
    """按优先级和条件特异度选择唯一策略，同分命中拒绝隐式按 ID 决胜。"""

    matches = tuple(version for version in versions if _matches(version.definition, subject))
    if not matches:
        raise ApprovalPolicyNotMatchedError
    ranked = sorted(
        matches,
        key=lambda item: (
            -item.definition.priority,
            -_specificity(item.definition),
            item.approval_policy_version_id.int,
        ),
    )
    best = ranked[0]
    if len(ranked) > 1 and (
        ranked[1].definition.priority,
        _specificity(ranked[1].definition),
    ) == (best.definition.priority, _specificity(best.definition)):
        raise ApprovalPolicyConflictError
    return best


def resolve_approval_chain(
    subject: ApprovalSubject,
    versions: tuple[ApprovalPolicyVersion, ...],
    directory: ApprovalOrganizationDirectory,
) -> ApprovalChain:
    """以当前组织事实计算稳定审批人集合；P1F-04 再将结果冻结为审批实例。"""

    workspace = directory.workspace_identity(subject.workspace_id)
    requester = directory.member_identity(subject.workspace_id, subject.requester_account_id)
    if workspace is None or requester is None or not requester.active:
        raise ApprovalApproverUnavailableError(0)
    if workspace.workspace_type == "personal":
        owner_confirmation = ResolvedApprovalLevel(
            1,
            "all",
            (workspace.owner_account_id,),
            1_440,
            4_320,
            "wait",
            (),
        )
        return _chain(subject.workspace_id, None, True, (owner_confirmation,))

    version = select_approval_policy(versions, subject)
    resolved: list[ResolvedApprovalLevel] = []
    # 1. 每一级独立解析当前组织事实，停用成员、角色或职位不会进入新审批链。
    for level_definition in version.definition.levels:
        approvers = _resolve_sources(
            directory,
            subject,
            requester,
            level_definition.sources,
        )
        fallback = _resolve_sources(
            directory,
            subject,
            requester,
            level_definition.fallback_sources,
        )
        # 2. 企业默认禁止申请人自批；候选被剔除后为空时明确定位到具体层级。
        if not version.definition.allow_self_approval:
            approvers.discard(subject.requester_account_id)
            fallback.discard(subject.requester_account_id)
        if not approvers or (
            level_definition.timeout_action in {"escalate", "transfer"} and not fallback
        ):
            raise ApprovalApproverUnavailableError(level_definition.sequence_no)
        resolved.append(
            ResolvedApprovalLevel(
                sequence_no=level_definition.sequence_no,
                mode=level_definition.mode,
                approver_account_ids=tuple(sorted(approvers, key=lambda item: item.int)),
                reminder_after_minutes=level_definition.reminder_after_minutes,
                timeout_after_minutes=level_definition.timeout_after_minutes,
                timeout_action=level_definition.timeout_action,
                fallback_approver_account_ids=tuple(sorted(fallback, key=lambda item: item.int)),
            )
        )
    return _chain(subject.workspace_id, version, False, tuple(resolved))


def _validate_sources(sources: tuple[ApprovalApproverSource, ...]) -> None:
    seen: set[tuple[str, tuple[UUID, ...], int | None]] = set()
    for source in sources:
        key = (source.source_type, source.reference_ids, source.levels_up)
        if (
            key in seen
            or not source.reference_ids
            or len(source.reference_ids) > MAX_APPROVER_REFERENCES
            or len(set(source.reference_ids)) != len(source.reference_ids)
            or source.source_type
            not in {"accounts", "roles", "department_managers", "upper_managers"}
            or (
                source.source_type == "upper_managers"
                and (source.levels_up is None or not 1 <= source.levels_up <= 5)
            )
            or (source.source_type != "upper_managers" and source.levels_up is not None)
        ):
            raise ApprovalPolicyValidationError
        seen.add(key)


def _matches(definition: ApprovalPolicyDefinition, subject: ApprovalSubject) -> bool:
    if (
        definition.resource_type != subject.resource_type
        or definition.operation != subject.operation
    ):
        return False
    effective_departments = set(subject.department_ids)
    if definition.department_ids and not effective_departments.intersection(
        definition.department_ids
    ):
        return False
    if definition.security_levels and subject.security_level not in definition.security_levels:
        return False
    if definition.risk_levels and subject.risk_level not in definition.risk_levels:
        return False
    return all(
        _matches_field(condition, subject.fields) for condition in definition.field_conditions
    )


def _matches_field(condition: ApprovalFieldCondition, fields: dict[str, object]) -> bool:
    # 1. 先区分字段不存在与显式 null，避免 exists、eq 和 ne 混用时产生歧义。
    actual = _field_value(fields, condition.field_path)
    if condition.operator == "exists":
        return actual is not _MISSING
    if actual is _MISSING:
        return False
    expected = condition.expected
    if condition.operator == "eq":
        return actual == expected
    if condition.operator == "ne":
        return actual != expected
    # 2. 比较与包含操作按 JSON 值类型收窄，类型不兼容时按策略未命中处理。
    if condition.operator in {"gt", "gte", "lt", "lte"}:
        if not _number(actual) or not _number(expected):
            return False
        if condition.operator == "gt":
            return actual > expected
        if condition.operator == "gte":
            return actual >= expected
        if condition.operator == "lt":
            return actual < expected
        return actual <= expected
    if condition.operator == "in":
        return isinstance(expected, (list, tuple)) and actual in expected
    if condition.operator == "contains":
        if isinstance(actual, str):
            return isinstance(expected, str) and expected in actual
        if isinstance(actual, (list, tuple)):
            return expected in actual
        if isinstance(actual, dict):
            return isinstance(expected, str) and expected in actual
        return False
    raise ApprovalPolicyValidationError


def _field_value(fields: dict[str, object], path: str) -> object:
    value: object = fields
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _specificity(definition: ApprovalPolicyDefinition) -> int:
    return (
        int(bool(definition.department_ids))
        + int(bool(definition.security_levels))
        + int(bool(definition.risk_levels))
        + len(definition.field_conditions)
    )


def _resolve_sources(
    directory: ApprovalOrganizationDirectory,
    subject: ApprovalSubject,
    requester: ApprovalMemberIdentity,
    sources: tuple[ApprovalApproverSource, ...],
) -> set[UUID]:
    accounts: set[UUID] = set()
    for source in sources:
        accounts.update(
            directory.resolve_source(
                subject.workspace_id,
                source,
                requester=requester,
                resource_department_ids=subject.department_ids,
            )
        )
    return accounts


def _chain(
    workspace_id: UUID,
    version: ApprovalPolicyVersion | None,
    personal: bool,
    levels: tuple[ResolvedApprovalLevel, ...],
) -> ApprovalChain:
    document = {
        "approval_policy_version_id": (
            str(version.approval_policy_version_id) if version is not None else None
        ),
        "levels": [
            {
                "approver_account_ids": [str(item) for item in level.approver_account_ids],
                "fallback_approver_account_ids": [
                    str(item) for item in level.fallback_approver_account_ids
                ],
                "mode": level.mode,
                "reminder_after_minutes": level.reminder_after_minutes,
                "sequence_no": level.sequence_no,
                "timeout_action": level.timeout_action,
                "timeout_after_minutes": level.timeout_after_minutes,
            }
            for level in levels
        ],
        "personal_owner_confirmation": personal,
        "workspace_id": str(workspace_id),
    }
    return ApprovalChain(
        workspace_id=workspace_id,
        approval_policy_id=version.approval_policy_id if version is not None else None,
        approval_policy_version_id=(
            version.approval_policy_version_id if version is not None else None
        ),
        personal_owner_confirmation=personal,
        levels=levels,
        chain_digest=hashlib.sha256(_canonical_json(document)).hexdigest(),
    )


def _level_document(level: ApprovalLevelDefinition) -> dict[str, object]:
    return {
        "fallback_sources": [_source_document(source) for source in level.fallback_sources],
        "mode": level.mode,
        "reminder_after_minutes": level.reminder_after_minutes,
        "sequence_no": level.sequence_no,
        "sources": [_source_document(source) for source in level.sources],
        "timeout_action": level.timeout_action,
        "timeout_after_minutes": level.timeout_after_minutes,
    }


def _source_document(source: ApprovalApproverSource) -> dict[str, object]:
    return {
        "levels_up": source.levels_up,
        "reference_ids": [str(item) for item in source.reference_ids],
        "source_type": source.source_type,
    }


def _level_items(value: object) -> tuple[ApprovalLevelDefinition, ...]:
    levels: list[ApprovalLevelDefinition] = []
    for raw_level in _array_document(value):
        level = _object_document(raw_level)
        mode = level.get("mode")
        timeout_action = level.get("timeout_action")
        if mode not in {"any", "all"} or timeout_action not in {
            "escalate",
            "transfer",
            "reject",
            "wait",
        }:
            raise ApprovalPolicyValidationError
        levels.append(
            ApprovalLevelDefinition(
                sequence_no=_integer(level.get("sequence_no")),
                mode=cast(ApprovalLevelMode, mode),
                sources=_source_items(level.get("sources")),
                reminder_after_minutes=_integer(level.get("reminder_after_minutes")),
                timeout_after_minutes=_integer(level.get("timeout_after_minutes")),
                timeout_action=cast(ApprovalTimeoutAction, timeout_action),
                fallback_sources=_source_items(level.get("fallback_sources")),
            )
        )
    return tuple(levels)


def _source_items(value: object) -> tuple[ApprovalApproverSource, ...]:
    sources: list[ApprovalApproverSource] = []
    for raw_source in _array_document(value):
        source = _object_document(raw_source)
        source_type = source.get("source_type")
        levels_up = source.get("levels_up")
        if source_type not in {
            "accounts",
            "roles",
            "department_managers",
            "upper_managers",
        } or (levels_up is not None and not _is_integer(levels_up)):
            raise ApprovalPolicyValidationError
        sources.append(
            ApprovalApproverSource(
                cast(ApprovalSourceType, source_type),
                _uuid_items(source.get("reference_ids")),
                levels_up,
            )
        )
    return tuple(sources)


def _field_condition_items(value: object) -> tuple[ApprovalFieldCondition, ...]:
    conditions: list[ApprovalFieldCondition] = []
    for raw_condition in _array_document(value):
        condition = _object_document(raw_condition)
        operator = condition.get("operator")
        if operator not in {
            "eq",
            "ne",
            "gt",
            "gte",
            "lt",
            "lte",
            "in",
            "contains",
            "exists",
        }:
            raise ApprovalPolicyValidationError
        conditions.append(
            ApprovalFieldCondition(
                _text(condition.get("field_path")),
                cast(ApprovalConditionOperator, operator),
                condition.get("expected"),
            )
        )
    return tuple(conditions)


def _security_level_items(value: object) -> tuple[SecurityLevel, ...]:
    values = _array_document(value)
    if any(item not in {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"} for item in values):
        raise ApprovalPolicyValidationError
    return tuple(cast(SecurityLevel, item) for item in values)


def _risk_level_items(value: object) -> tuple[ApprovalRiskLevel, ...]:
    values = _array_document(value)
    if any(item not in {"normal", "high", "critical"} for item in values):
        raise ApprovalPolicyValidationError
    return tuple(cast(ApprovalRiskLevel, item) for item in values)


def _uuid_items(value: object) -> tuple[UUID, ...]:
    return tuple(UUID(_text(item)) for item in _array_document(value))


def _object_document(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ApprovalPolicyValidationError
    return cast(dict[str, object], value)


def _array_document(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ApprovalPolicyValidationError
    return cast(list[object], value)


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ApprovalPolicyValidationError
    return value


def _is_integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _integer(value: object) -> int:
    if not _is_integer(value):
        raise ApprovalPolicyValidationError
    return value


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as error:
        raise ApprovalPolicyValidationError from error
