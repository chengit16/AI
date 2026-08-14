/** @description 企业空间部门、岗位和成员组织归属 API Service。 */
import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

/** 企业空间部门节点及其有效状态。 */
export type Department = components["schemas"]["DepartmentResponse"];
/** 归属单个部门的岗位事实。 */
export type Position = components["schemas"]["PositionResponse"];
/** 成员多部门、主部门和岗位归属快照。 */
export type MemberOrganization = components["schemas"]["MemberOrganizationResponse"];

/** 查询当前企业空间的完整可见部门树。 */
export async function getDepartments(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["DepartmentListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/organization/departments`,
    { signal },
  );
  return response.items;
}

/** 创建根部门或指定父部门下的子部门。 */
export function createDepartment(workspaceId: string, name: string, parentId: string | null) {
  return apiRequest<Department>(`/api/v1/workspaces/${workspaceId}/organization/departments`, {
    method: "POST",
    body: { name, parent_department_id: parentId },
  });
}

/** 启停部门；后端负责级联有效性和成员约束校验。 */
export function setDepartmentStatus(workspaceId: string, departmentId: string, active: boolean) {
  return apiRequest<Department>(
    `/api/v1/workspaces/${workspaceId}/organization/departments/${departmentId}/status`,
    { method: "POST", body: { active } },
  );
}

/** 查询当前企业空间的岗位清单。 */
export async function getPositions(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["PositionListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/organization/positions`,
    { signal },
  );
  return response.items;
}

/** 在有效部门下创建岗位。 */
export function createPosition(workspaceId: string, departmentId: string, name: string) {
  return apiRequest<Position>(`/api/v1/workspaces/${workspaceId}/organization/positions`, {
    method: "POST",
    body: { department_id: departmentId, name },
  });
}

/** 启停岗位；成员继承结果由后端重新计算。 */
export function setPositionStatus(workspaceId: string, positionId: string, active: boolean) {
  return apiRequest<Position>(
    `/api/v1/workspaces/${workspaceId}/organization/positions/${positionId}/status`,
    { method: "POST", body: { active } },
  );
}

/** 查询指定成员经过空间隔离的组织归属。 */
export function getMemberOrganization(workspaceId: string, accountId: string) {
  return apiRequest<MemberOrganization>(
    `/api/v1/workspaces/${workspaceId}/organization/members/${accountId}`,
  );
}

/** 原子替换成员的部门、主部门和岗位归属。 */
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
