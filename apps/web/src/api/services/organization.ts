import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

export type Department = components["schemas"]["DepartmentResponse"];
export type Position = components["schemas"]["PositionResponse"];
export type MemberOrganization = components["schemas"]["MemberOrganizationResponse"];

export async function getDepartments(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["DepartmentListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/organization/departments`,
    { signal },
  );
  return response.items;
}

export function createDepartment(workspaceId: string, name: string, parentId: string | null) {
  return apiRequest<Department>(`/api/v1/workspaces/${workspaceId}/organization/departments`, {
    method: "POST",
    body: { name, parent_department_id: parentId },
  });
}

export function setDepartmentStatus(workspaceId: string, departmentId: string, active: boolean) {
  return apiRequest<Department>(
    `/api/v1/workspaces/${workspaceId}/organization/departments/${departmentId}/status`,
    { method: "POST", body: { active } },
  );
}

export async function getPositions(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["PositionListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/organization/positions`,
    { signal },
  );
  return response.items;
}

export function createPosition(workspaceId: string, departmentId: string, name: string) {
  return apiRequest<Position>(`/api/v1/workspaces/${workspaceId}/organization/positions`, {
    method: "POST",
    body: { department_id: departmentId, name },
  });
}

export function setPositionStatus(workspaceId: string, positionId: string, active: boolean) {
  return apiRequest<Position>(
    `/api/v1/workspaces/${workspaceId}/organization/positions/${positionId}/status`,
    { method: "POST", body: { active } },
  );
}

export function getMemberOrganization(workspaceId: string, accountId: string) {
  return apiRequest<MemberOrganization>(
    `/api/v1/workspaces/${workspaceId}/organization/members/${accountId}`,
  );
}

export function assignMemberOrganization(
  workspaceId: string,
  accountId: string,
  body: components["schemas"]["AssignMemberOrganizationRequest"],
) {
  return apiRequest<MemberOrganization>(
    `/api/v1/workspaces/${workspaceId}/organization/members/${accountId}`,
    { method: "PUT", body },
  );
}
