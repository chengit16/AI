"""定义 Runtime 可执行发布快照、发布事实 Source 和派生缓存端口。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, cast
from uuid import UUID

RuntimeReleaseKind = Literal["system", "custom"]


class RuntimeSourceUnavailableError(Exception):
    """表示 Runtime 发布事实 Source 暂时无法完成查询。"""


class RuntimeCacheUnavailableError(Exception):
    """表示派生快照缓存暂时不可读写。"""


@dataclass(frozen=True)
class RuntimeReleaseSnapshot:
    """冻结一次服务 Route 选择及其唯一可执行 AgentRelease。"""

    workspace_id: UUID
    service_id: UUID
    service_key: str
    service_status: str
    access_policy_version_id: UUID
    route_id: UUID
    service_route_version: int
    route_mode: str
    primary_release_id: UUID
    canary_release_id: UUID | None
    canary_percent: int
    previous_route_id: UUID | None
    route_hash: str
    agent_id: UUID
    agent_status: str
    agent_release_id: UUID
    release_kind: RuntimeReleaseKind
    release_version: int
    release_status: str
    runtime_config_version_id: UUID
    config_hash: str
    release_snapshot: dict[str, object] | None
    release_snapshot_hash: str | None
    released_at: datetime


class RuntimeSnapshotSource(Protocol):
    """只从服务 Route 与 Release 发布事实装载 Runtime 输入。"""

    def get_current(
        self,
        workspace_id: UUID,
        service_id: UUID,
        assignment_bucket: int,
    ) -> RuntimeReleaseSnapshot | None: ...

    def get_bound(
        self,
        workspace_id: UUID,
        service_id: UUID,
        route_id: UUID,
        service_route_version: int,
        agent_release_id: UUID,
    ) -> RuntimeReleaseSnapshot | None: ...


class RuntimeSnapshotCache(Protocol):
    """保存可重建的当前 Route 与不可变 Run 精确绑定快照。"""

    def get_current(
        self,
        workspace_id: UUID,
        service_id: UUID,
        assignment_bucket: int,
    ) -> RuntimeReleaseSnapshot | None: ...

    def get_bound(
        self,
        workspace_id: UUID,
        service_id: UUID,
        route_id: UUID,
        service_route_version: int,
        agent_release_id: UUID,
    ) -> RuntimeReleaseSnapshot | None: ...

    def put_current(self, snapshot: RuntimeReleaseSnapshot, assignment_bucket: int) -> None: ...

    def put_bound(self, snapshot: RuntimeReleaseSnapshot) -> None: ...

    def delete_current(self, workspace_id: UUID, service_id: UUID) -> None: ...

    def delete_bound(
        self,
        workspace_id: UUID,
        service_id: UUID,
        route_id: UUID,
        service_route_version: int,
        agent_release_id: UUID,
    ) -> None: ...

    def close(self) -> None: ...


def canonical_json(document: object) -> bytes:
    """编码稳定 JSON，供 Route、Release 和缓存信封跨进程复算。"""

    return json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def document_digest(document: object) -> str:
    """计算 Runtime 发布文档的 SHA-256 内容身份。"""

    return hashlib.sha256(canonical_json(document)).hexdigest()


def route_assignment_bucket(service_id: UUID, assignment_key: str) -> int:
    """把稳定非敏感分配键映射到固定百分位，不保存或返回原始主体值。"""

    normalized = assignment_key.strip()
    if not 1 <= len(normalized) <= 256 or any(
        ord(character) < 32 or ord(character) == 127 for character in normalized
    ):
        raise ValueError("Runtime 灰度分配键不满足长度或字符约束")
    digest = hashlib.sha256(service_id.bytes + b"\x00" + normalized.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False) % 100


def selected_route_release(snapshot: RuntimeReleaseSnapshot, assignment_bucket: int) -> UUID:
    """按 Route 模式和固定百分位选择唯一 Release。"""

    if not 0 <= assignment_bucket <= 99:
        raise ValueError("Runtime 灰度分桶超出百分位范围")
    if (
        snapshot.route_mode == "canary"
        and snapshot.canary_release_id is not None
        and assignment_bucket < snapshot.canary_percent
    ):
        return snapshot.canary_release_id
    return snapshot.primary_release_id


def runtime_snapshot_document(snapshot: RuntimeReleaseSnapshot) -> dict[str, object]:
    """把发布快照转换为类型稳定且不包含草稿或凭证的缓存文档。"""

    return {
        "workspace_id": str(snapshot.workspace_id),
        "service_id": str(snapshot.service_id),
        "service_key": snapshot.service_key,
        "service_status": snapshot.service_status,
        "access_policy_version_id": str(snapshot.access_policy_version_id),
        "route_id": str(snapshot.route_id),
        "service_route_version": snapshot.service_route_version,
        "route_mode": snapshot.route_mode,
        "primary_release_id": str(snapshot.primary_release_id),
        "canary_release_id": (
            str(snapshot.canary_release_id) if snapshot.canary_release_id is not None else None
        ),
        "canary_percent": snapshot.canary_percent,
        "previous_route_id": (
            str(snapshot.previous_route_id) if snapshot.previous_route_id is not None else None
        ),
        "route_hash": snapshot.route_hash,
        "agent_id": str(snapshot.agent_id),
        "agent_status": snapshot.agent_status,
        "agent_release_id": str(snapshot.agent_release_id),
        "release_kind": snapshot.release_kind,
        "release_version": snapshot.release_version,
        "release_status": snapshot.release_status,
        "runtime_config_version_id": str(snapshot.runtime_config_version_id),
        "config_hash": snapshot.config_hash,
        "release_snapshot": snapshot.release_snapshot,
        "release_snapshot_hash": snapshot.release_snapshot_hash,
        "released_at": snapshot.released_at.isoformat(),
    }


def runtime_snapshot_from_document(document: object) -> RuntimeReleaseSnapshot:
    """从缓存文档恢复快照；未知或缺失字段由调用方按损坏缓存处理。"""

    # 1. 先验证封闭字段集和需要保留对象语义的 Release 内容，拒绝宽松解析未知版本。
    if not isinstance(document, dict) or set(document) != _SNAPSHOT_FIELDS:
        raise ValueError("Runtime 快照字段不完整")
    release_kind = document["release_kind"]
    release_snapshot = document["release_snapshot"]
    if release_kind not in {"system", "custom"}:
        raise ValueError("Runtime 快照包含未知 Release 类型")
    if release_snapshot is not None and not isinstance(release_snapshot, dict):
        raise ValueError("Runtime Release 快照必须是对象")
    # 2. 逐字段恢复强类型值对象；任何 UUID、时间或数值异常都由缓存 Adapter 视为损坏。
    return RuntimeReleaseSnapshot(
        workspace_id=UUID(str(document["workspace_id"])),
        service_id=UUID(str(document["service_id"])),
        service_key=str(document["service_key"]),
        service_status=str(document["service_status"]),
        access_policy_version_id=UUID(str(document["access_policy_version_id"])),
        route_id=UUID(str(document["route_id"])),
        service_route_version=int(str(document["service_route_version"])),
        route_mode=str(document["route_mode"]),
        primary_release_id=UUID(str(document["primary_release_id"])),
        canary_release_id=(
            UUID(str(document["canary_release_id"]))
            if document["canary_release_id"] is not None
            else None
        ),
        canary_percent=int(str(document["canary_percent"])),
        previous_route_id=(
            UUID(str(document["previous_route_id"]))
            if document["previous_route_id"] is not None
            else None
        ),
        route_hash=str(document["route_hash"]),
        agent_id=UUID(str(document["agent_id"])),
        agent_status=str(document["agent_status"]),
        agent_release_id=UUID(str(document["agent_release_id"])),
        release_kind=cast("RuntimeReleaseKind", release_kind),
        release_version=int(str(document["release_version"])),
        release_status=str(document["release_status"]),
        runtime_config_version_id=UUID(str(document["runtime_config_version_id"])),
        config_hash=str(document["config_hash"]),
        release_snapshot=cast("dict[str, object] | None", release_snapshot),
        release_snapshot_hash=(
            str(document["release_snapshot_hash"])
            if document["release_snapshot_hash"] is not None
            else None
        ),
        released_at=datetime.fromisoformat(str(document["released_at"])),
    )


def runtime_snapshot_digest(snapshot: RuntimeReleaseSnapshot) -> str:
    """计算完整 Runtime 快照信封摘要，防止缓存字段被局部替换。"""

    return document_digest(runtime_snapshot_document(snapshot))


def route_digest(snapshot: RuntimeReleaseSnapshot) -> str:
    """按服务治理相同算法复算 Route 身份，不信任缓存或查询结果中的摘要。"""

    return document_digest(
        {
            "service_id": str(snapshot.service_id),
            "workspace_id": str(snapshot.workspace_id),
            "route_version": snapshot.service_route_version,
            "route_mode": snapshot.route_mode,
            "primary_release_id": str(snapshot.primary_release_id),
            "canary_release_id": (
                str(snapshot.canary_release_id) if snapshot.canary_release_id is not None else None
            ),
            "canary_percent": snapshot.canary_percent,
            "previous_route_id": (
                str(snapshot.previous_route_id) if snapshot.previous_route_id is not None else None
            ),
        }
    )


_SNAPSHOT_FIELDS = frozenset(
    {
        "workspace_id",
        "service_id",
        "service_key",
        "service_status",
        "access_policy_version_id",
        "route_id",
        "service_route_version",
        "route_mode",
        "primary_release_id",
        "canary_release_id",
        "canary_percent",
        "previous_route_id",
        "route_hash",
        "agent_id",
        "agent_status",
        "agent_release_id",
        "release_kind",
        "release_version",
        "release_status",
        "runtime_config_version_id",
        "config_hash",
        "release_snapshot",
        "release_snapshot_hash",
        "released_at",
    }
)
