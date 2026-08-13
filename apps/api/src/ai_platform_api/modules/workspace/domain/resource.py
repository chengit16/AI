from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class WorkspaceResource:
    resource_id: UUID
    workspace_id: UUID
    title: str
    sensitive_value: str | None
    version: int = 1

    def mask(self, fields: frozenset[str]) -> "WorkspaceResource":
        if "sensitive_value" in fields:
            return replace(self, sensitive_value=None)
        return self


class WorkspaceResourceRepository(Protocol):
    def add(self, resource: WorkspaceResource) -> None: ...

    def get_scoped(self, workspace_id: UUID, resource_id: UUID) -> WorkspaceResource | None: ...
