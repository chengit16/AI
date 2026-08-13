import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

export type Workspace = components["schemas"]["WorkspaceSummaryResponse"];
export type WorkspaceMember = components["schemas"]["WorkspaceMemberResponse"];
type WorkspaceMemberView = components["schemas"]["WorkspaceMemberListResponse"]["items"][number];

function isWorkspaceMember(item: WorkspaceMemberView): item is WorkspaceMember {
  return (
    typeof item.account_id === "string" &&
    typeof item.display_name === "string" &&
    (item.membership_type === "owner" || item.membership_type === "member") &&
    (item.status === "active" || item.status === "disabled" || item.status === "left")
  );
}

export async function getWorkspaces(signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["WorkspaceListResponse"]>(
    "/api/v1/workspaces",
    { signal },
  );
  return response.items;
}

export function createEnterpriseWorkspace(name: string) {
  return apiRequest<Workspace>("/api/v1/workspaces/enterprise", { method: "POST", body: { name } });
}

export function switchWorkspace(workspaceId: string) {
  return apiRequest<Workspace>(`/api/v1/workspaces/${workspaceId}/switch`, { method: "POST" });
}

export async function getWorkspaceMembers(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["WorkspaceMemberListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/members`,
    { signal },
  );
  // 治理页面依赖稳定成员标识；字段级 ABAC 投影后的记录不能用于停用或组织归属操作。
  return response.items.filter(isWorkspaceMember);
}

export function inviteWorkspaceMember(workspaceId: string, loginName: string) {
  return apiRequest<components["schemas"]["WorkspaceInvitationResponse"]>(
    `/api/v1/workspaces/${workspaceId}/invitations`,
    { method: "POST", body: { login_name: loginName } },
  );
}

export function acceptWorkspaceInvitation(invitationId: string) {
  return apiRequest<Workspace>(`/api/v1/workspaces/invitations/${invitationId}/accept`, {
    method: "POST",
  });
}

export function disableWorkspaceMember(workspaceId: string, accountId: string) {
  return apiRequest<components["schemas"]["WorkspaceMembershipResponse"]>(
    `/api/v1/workspaces/${workspaceId}/members/${accountId}/disable`,
    { method: "POST" },
  );
}
