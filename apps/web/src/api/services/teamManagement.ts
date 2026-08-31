/** @description 企业团队聚合、邀请撤销和成员原子治理 API Service。 */
import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

/** 企业团队管理统一读模型。 */
export type TeamManagement = components["schemas"]["TeamManagementResponse"];
/** 团队成员及其组织、角色和最近活动事实。 */
export type TeamMember = components["schemas"]["TeamMemberResponse"];
/** 企业邀请及其按当前时间计算的有效状态。 */
export type TeamInvitation = components["schemas"]["TeamInvitationResponse"];
/** 带乐观版本的成员组织和直接角色原子替换输入。 */
export type UpdateTeamMember = components["schemas"]["UpdateTeamMemberRequest"];

/** 查询同一事务口径的成员、邀请、组织、角色和治理审计。 */
export function getTeamManagement(
  workspaceId: string,
  signal?: AbortSignal,
): Promise<TeamManagement> {
  return apiRequest<TeamManagement>(
    `/api/v1/workspaces/${workspaceId}/team-management?audit_limit=20`,
    { signal },
  );
}

/** 撤销当前空间仍有效的待处理邀请。 */
export function cancelTeamInvitation(workspaceId: string, invitationId: string) {
  return apiRequest<components["schemas"]["WorkspaceInvitationResponse"]>(
    `/api/v1/workspaces/${workspaceId}/invitations/${invitationId}/cancel`,
    { method: "POST" },
  );
}

/** 原子替换成员部门、岗位和自定义直接角色。 */
export function updateTeamMember(workspaceId: string, accountId: string, body: UpdateTeamMember) {
  return apiRequest<components["schemas"]["WorkspaceMembershipResponse"]>(
    `/api/v1/workspaces/${workspaceId}/team-management/members/${accountId}`,
    { method: "PUT", body },
  );
}

/** 恢复被停用成员，并重新启用其保留的组织和角色配置。 */
export function activateTeamMember(workspaceId: string, accountId: string) {
  return apiRequest<components["schemas"]["WorkspaceMembershipResponse"]>(
    `/api/v1/workspaces/${workspaceId}/team-management/members/${accountId}/activate`,
    { method: "POST" },
  );
}

/** 移除普通成员，并清理组织与自定义直接角色关系。 */
export function removeTeamMember(workspaceId: string, accountId: string) {
  return apiRequest<components["schemas"]["WorkspaceMembershipResponse"]>(
    `/api/v1/workspaces/${workspaceId}/team-management/members/${accountId}/remove`,
    { method: "POST" },
  );
}
