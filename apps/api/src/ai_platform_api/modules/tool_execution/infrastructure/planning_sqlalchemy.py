"""从 Service 与不可变 AgentRelease 投影工具执行允许列表。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from typing import cast
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from ai_platform_api.modules.tool_execution.domain.planning import (
    ReleaseToolReference,
    ToolPolicyDecisionRecord,
    ToolReleasePlan,
)
from ai_platform_api.persistence.tables import (
    agent_releases,
    agents,
    services,
    tool_policy_decisions,
)

SessionFactory = Callable[[], Session]
PERMISSION_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){2,}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SqlAlchemyToolReleasePlanSource:
    """只接受活动 Service、活动 Agent 和可验证的精确 Release 绑定。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def get_bound(
        self,
        workspace_id: UUID,
        service_id: UUID,
        agent_release_id: UUID,
    ) -> ToolReleasePlan | None:
        """读取一个事务快照，避免拼接两个时点的 Service 和 Release 当前态。"""

        # 1. 一次查询固定 Service、Agent 和精确 Release 的身份与活动状态。
        statement = (
            select(
                services.c.service_id,
                services.c.workspace_id,
                agent_releases.c.release_id,
                agent_releases.c.release_kind,
                agent_releases.c.status.label("release_status"),
                agent_releases.c.snapshot,
                agent_releases.c.snapshot_hash,
            )
            .join(
                agents,
                (agents.c.agent_id == services.c.agent_id)
                & (agents.c.workspace_id == services.c.workspace_id),
            )
            .join(
                agent_releases,
                (agent_releases.c.agent_id == services.c.agent_id)
                & (agent_releases.c.workspace_id == services.c.workspace_id),
            )
            .where(
                services.c.workspace_id == workspace_id,
                services.c.service_id == service_id,
                services.c.status == "active",
                agents.c.status == "active",
                agent_releases.c.release_id == agent_release_id,
                agent_releases.c.status == "released",
            )
        )
        with self._session_factory() as session:
            row = session.execute(statement).mappings().one_or_none()
        if row is None:
            return None

        # 2. 系统 Release 固定为空允许列表；自定义 Release 则必须通过完整快照摘要复算。
        if row["release_kind"] == "system":
            if row["snapshot"] is not None or row["snapshot_hash"] is not None:
                return None
            return ToolReleasePlan(
                workspace_id=workspace_id,
                service_id=service_id,
                agent_release_id=agent_release_id,
                release_snapshot_hash=None,
                tools=(),
            )
        if row["release_kind"] != "custom":
            return None

        snapshot = row["snapshot"]
        snapshot_hash = row["snapshot_hash"]
        if (
            not isinstance(snapshot, Mapping)
            or not isinstance(snapshot_hash, str)
            or SHA256_PATTERN.fullmatch(snapshot_hash) is None
            or _document_hash(snapshot) != snapshot_hash
        ):
            return None
        tools = _read_only_tools(snapshot)
        if tools is None:
            return None
        return ToolReleasePlan(
            workspace_id=cast(UUID, row["workspace_id"]),
            service_id=cast(UUID, row["service_id"]),
            agent_release_id=cast(UUID, row["release_id"]),
            release_snapshot_hash=snapshot_hash,
            tools=tools,
        )


def insert_tool_policy_decision(session: Session, policy: ToolPolicyDecisionRecord) -> None:
    """在调用方事务内追加一次不可变 PDP 证据，不负责提交 Session。"""

    session.execute(
        insert(tool_policy_decisions).values(
            decision_id=policy.decision_id,
            workspace_id=policy.workspace_id,
            run_id=policy.run_id,
            step_id=policy.step_id,
            tool_id=policy.tool_id,
            tool_version=policy.tool_version,
            canonical_arguments_hash=policy.canonical_arguments_hash,
            permission_code=policy.permission_code,
            policy_version=policy.policy_version,
            resource_scope_hash=policy.resource_scope_hash,
            field_mask_hash=policy.field_mask_hash,
            evaluated_at=policy.evaluated_at,
        )
    )


def _read_only_tools(
    snapshot: Mapping[object, object],
) -> tuple[ReleaseToolReference, ...] | None:
    """严格解析 Release 配置中的只读引用，损坏快照不产生部分允许列表。"""

    # 1. 只从固定配置字段读取有限数组，不接受缺失或其他集合类型的隐式转换。
    configuration = snapshot.get("configuration")
    if not isinstance(configuration, Mapping):
        return None
    raw_tools = configuration.get("read_only_tools")
    if not isinstance(raw_tools, list) or len(raw_tools) > 20:
        return None
    # 2. 每项必须与 Agent 配置契约字段完全一致，坏项不会被过滤后部分放行。
    parsed: list[ReleaseToolReference] = []
    for raw_tool in raw_tools:
        if not isinstance(raw_tool, Mapping) or set(raw_tool) != {
            "tool_id",
            "tool_version",
            "access_mode",
            "permission_code",
        }:
            return None
        tool_id = raw_tool.get("tool_id")
        tool_version = raw_tool.get("tool_version")
        access_mode = raw_tool.get("access_mode")
        permission_code = raw_tool.get("permission_code")
        if (
            not isinstance(tool_id, str)
            or isinstance(tool_version, bool)
            or not isinstance(tool_version, int)
            or tool_version < 1
            or access_mode != "read"
            or not isinstance(permission_code, str)
            or PERMISSION_PATTERN.fullmatch(permission_code) is None
        ):
            return None
        try:
            parsed_tool_id = UUID(tool_id)
        except ValueError:
            return None
        parsed.append(
            ReleaseToolReference(
                tool_id=parsed_tool_id,
                tool_version=tool_version,
                access_mode="read",
                permission_code=permission_code,
            )
        )
    # 3. 重复身份会让允许列表语义不确定，因此在返回前整体拒绝。
    identities = {(item.tool_id, item.tool_version) for item in parsed}
    if len(identities) != len(parsed):
        return None
    return tuple(parsed)


def _document_hash(document: Mapping[object, object]) -> str | None:
    """复算 AgentRelease 使用的规范 JSON 摘要，不信任数据库中的摘要列。"""

    try:
        payload = json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(payload).hexdigest()


__all__ = ["SqlAlchemyToolReleasePlanSource", "insert_tool_policy_decision"]
