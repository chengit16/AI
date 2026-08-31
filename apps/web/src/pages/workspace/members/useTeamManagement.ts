/** @description 团队聚合查询与邀请、成员治理 Mutation 编排。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";

import { errorMessage, PlatformApiError } from "@/api/client";
import { disableWorkspaceMember, inviteWorkspaceMember } from "@/api/services/workspaces";
import {
  activateTeamMember,
  cancelTeamInvitation,
  getTeamManagement,
  removeTeamMember,
  updateTeamMember,
  type TeamMember,
  type UpdateTeamMember,
} from "@/api/services/teamManagement";

interface UseTeamManagementOptions {
  /** 来自可信空间切换状态的当前工作空间标识。 */
  workspaceId: string | null | undefined;
  /** 当前页面是否已满足企业空间和读取权限条件。 */
  enabled: boolean;
}

/**
 * 仅在授权策略版本尚未传播完成时有限重试团队聚合。
 *
 * 成员角色事务提交后，PDP 会在缓存版本追平前安全失败关闭；这里不扩大权限，
 * 只重试服务端明确标为可重试的 `POLICY_UNAVAILABLE`，其他错误立即交给页面处理。
 */
export function shouldRetryTeamQuery(failureCount: number, error: unknown): boolean {
  return (
    failureCount < 4 &&
    error instanceof PlatformApiError &&
    error.code === "POLICY_UNAVAILABLE" &&
    error.retryable
  );
}

/** 为策略传播窗口提供有上限的指数退避，避免持续轮询不可用依赖。 */
export function teamQueryRetryDelay(failureCount: number): number {
  return Math.min(500 * 2 ** failureCount, 4_000);
}

/** 集中维护团队 Query 失效和稳定反馈，页面组件只编排可见交互。 */
export function useTeamManagement({ workspaceId, enabled }: UseTeamManagementOptions) {
  // 1. 建立当前空间唯一查询键，并集中维护刷新和用户反馈。
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const queryKey = ["team-management", workspaceId] as const;
  const team = useQuery({
    queryKey,
    queryFn: ({ signal }) => getTeamManagement(workspaceId!, signal),
    enabled: Boolean(workspaceId && enabled),
    retry: shouldRetryTeamQuery,
    retryDelay: teamQueryRetryDelay,
  });

  const refresh = async () => queryClient.invalidateQueries({ queryKey });
  const mutationOptions = (successMessage: string) => ({
    onSuccess: async () => {
      await refresh();
      void message.success(successMessage);
    },
    onError: (error: unknown) => void message.error(errorMessage(error)),
  });

  // 2. 所有团队治理写操作复用同一失效策略，避免页面出现不同步的局部事实。
  const invite = useMutation({
    mutationFn: (loginName: string) => inviteWorkspaceMember(workspaceId!, loginName),
    ...mutationOptions("邀请已创建，可复制链接发送给成员"),
  });
  const cancelInvitation = useMutation({
    mutationFn: (invitationId: string) => cancelTeamInvitation(workspaceId!, invitationId),
    ...mutationOptions("邀请已撤销"),
  });
  const updateMember = useMutation({
    mutationFn: ({ member, body }: { member: TeamMember; body: UpdateTeamMember }) =>
      updateTeamMember(workspaceId!, member.account_id, body),
    ...mutationOptions("成员组织与角色已更新"),
  });
  const disableMember = useMutation({
    mutationFn: (accountId: string) => disableWorkspaceMember(workspaceId!, accountId),
    ...mutationOptions("成员已停用，组织和角色配置已保留"),
  });
  const activateMember = useMutation({
    mutationFn: (accountId: string) => activateTeamMember(workspaceId!, accountId),
    ...mutationOptions("成员已恢复"),
  });
  const removeMember = useMutation({
    mutationFn: (accountId: string) => removeTeamMember(workspaceId!, accountId),
    ...mutationOptions("成员已移除，重新加入需要新的邀请"),
  });

  return {
    team,
    invite,
    cancelInvitation,
    updateMember,
    disableMember,
    activateMember,
    removeMember,
  };
}
