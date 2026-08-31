/**
 * @description 工作空间、成员、菜单发布和有效角色 API Service
 * 请求只传递当前操作目标，成员资格与空间隔离由服务端可信上下文决定。
 */
import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

/** 当前账号可访问的个人或企业空间摘要。 */
export type Workspace = components["schemas"]["WorkspaceSummaryResponse"];
/** 具备稳定账号标识的成员治理视图。 */
export type WorkspaceMember = components["schemas"]["WorkspaceMemberResponse"];
type WorkspaceMemberView = components["schemas"]["WorkspaceMemberListResponse"]["items"][number];
/** 当前空间已发布菜单快照及其资源绑定。 */
export type CurrentMenuRelease = components["schemas"]["CurrentMenuReleaseResponse"];
/** 指定成员经过直接、部门和系统角色继承后的有效角色集合。 */
export type EffectiveRoleSet = components["schemas"]["EffectiveRoleSetResponse"];
/** 企业控制台使用统一事务口径返回的低敏聚合快照。 */
export type EnterpriseConsole = components["schemas"]["EnterpriseConsoleResponse"];

function isWorkspaceMember(item: WorkspaceMemberView): item is WorkspaceMember {
  return (
    typeof item.account_id === "string" &&
    typeof item.display_name === "string" &&
    (item.membership_type === "owner" || item.membership_type === "member") &&
    (item.status === "active" || item.status === "disabled" || item.status === "left")
  );
}

/** 查询当前会话可访问的空间清单及服务端选中状态。 */
export async function getWorkspaces(signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["WorkspaceListResponse"]>(
    "/api/v1/workspaces",
    { signal },
  );
  return response.items;
}

/** 创建企业空间并把当前账号设为系统所有者。 */
export function createEnterpriseWorkspace(name: string) {
  return apiRequest<Workspace>("/api/v1/workspaces/enterprise", { method: "POST", body: { name } });
}

/** 切换服务端会话的当前空间；调用方随后必须失效空间相关缓存。 */
export function switchWorkspace(workspaceId: string) {
  return apiRequest<Workspace>(`/api/v1/workspaces/${workspaceId}/switch`, { method: "POST" });
}

/** 查询可治理成员，并排除字段投影后缺少稳定标识的只读记录。 */
export async function getWorkspaceMembers(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["WorkspaceMemberListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/members`,
    { signal },
  );
  // 治理页面依赖稳定成员标识；字段级 ABAC 投影后的记录不能用于停用或组织归属操作。
  return response.items.filter(isWorkspaceMember);
}

/** 邀请已注册账号加入企业空间。 */
export function inviteWorkspaceMember(workspaceId: string, loginName: string) {
  return apiRequest<components["schemas"]["WorkspaceInvitationResponse"]>(
    `/api/v1/workspaces/${workspaceId}/invitations`,
    { method: "POST", body: { login_name: loginName } },
  );
}

/** 接受当前账号收到的有效邀请并返回目标空间摘要。 */
export function acceptWorkspaceInvitation(invitationId: string) {
  return apiRequest<Workspace>(`/api/v1/workspaces/invitations/${invitationId}/accept`, {
    method: "POST",
  });
}

/** 停用企业成员；服务端禁止越权或破坏最后所有者约束。 */
export function disableWorkspaceMember(workspaceId: string, accountId: string) {
  return apiRequest<components["schemas"]["WorkspaceMembershipResponse"]>(
    `/api/v1/workspaces/${workspaceId}/members/${accountId}/disable`,
    { method: "POST" },
  );
}

/** 获取当前空间不可变菜单发布快照，供应用壳层构造导航。 */
export async function getCurrentWorkspaceMenuRelease(
  workspaceId: string,
  signal?: AbortSignal,
): Promise<CurrentMenuRelease> {
  return apiRequest<CurrentMenuRelease>(`/api/v1/workspaces/${workspaceId}/menu-releases/current`, {
    signal,
  });
}

/** 查询指定成员的确定性有效角色集合，供治理页面解释权限来源。 */
export async function getEffectiveWorkspaceRoles(
  workspaceId: string,
  accountId: string,
  signal?: AbortSignal,
): Promise<EffectiveRoleSet> {
  return apiRequest<EffectiveRoleSet>(
    `/api/v1/workspaces/${workspaceId}/roles/effective/${accountId}`,
    { signal },
  );
}

/** 查询企业控制台统计、趋势和最近内容；全空间聚合范围由服务端 PDP 决定。 */
export function getEnterpriseConsole(
  workspaceId: string,
  signal?: AbortSignal,
): Promise<EnterpriseConsole> {
  return apiRequest<EnterpriseConsole>(
    `/api/v1/workspaces/${workspaceId}/enterprise-console?trend_months=6&recent_limit=10`,
    { signal },
  );
}
