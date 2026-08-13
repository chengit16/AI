import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

export type Workspace = components["schemas"]["WorkspaceSummaryResponse"];
export type WorkspaceMember = components["schemas"]["WorkspaceMemberResponse"];

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
  return response.items;
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
