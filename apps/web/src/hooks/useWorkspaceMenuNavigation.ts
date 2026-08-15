/**
 * @description 当前工作空间动态菜单查询 Hook
 * 服务端快照是菜单事实来源，静态注册表只提供组件映射能力。
 */
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import {
  getCurrentWorkspaceMenuRelease,
  getEffectiveWorkspaceRoles,
} from "@/api/services/workspaces";
import { buildDynamicNavigation } from "@/config/dynamicMenu";
import { resourceRegistry } from "@/config/resourceRegistry.generated";
import { iconByKey, staticWorkspaceNavigation } from "@/config/resources";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";
import { useSessionStore } from "@/store/session";

/**
 * 返回当前空间菜单发布、有效角色、可见导航和加载错误状态。
 *
 * 个人空间没有部门角色继承，因此只在企业空间查询有效角色；菜单不存在发布快照时
 * 使用注册表导航兼容本地初始空间，首次发布后完全以服务端快照为准。
 */
export function useWorkspaceMenuNavigation() {
  const workspaceId = useSessionStore((state) => state.workspaceId);
  const accountId = useSessionStore((state) => state.accountId);
  const workspace = useCurrentWorkspace();
  // 1. 独立轮询菜单发布和企业有效角色，使任一权限事实变化都能在传播时限内触发重算。
  const release = useQuery({
    queryKey: ["workspace-menu-release", workspaceId],
    queryFn: ({ signal }) => getCurrentWorkspaceMenuRelease(workspaceId!, signal),
    enabled: Boolean(workspaceId),
    staleTime: 0,
    refetchInterval: 2_000,
    refetchIntervalInBackground: true,
  });
  const roles = useQuery({
    queryKey: ["workspace-effective-roles", workspaceId, accountId],
    queryFn: ({ signal }) => getEffectiveWorkspaceRoles(workspaceId!, accountId!, signal),
    enabled: Boolean(
      workspaceId && accountId && workspace.currentWorkspace?.workspace_type === "enterprise",
    ),
    staleTime: 0,
    refetchInterval: 2_000,
    refetchIntervalInBackground: true,
  });
  const authorizationUnavailable = Boolean(
    release.error || roles.error || workspace.workspaces.error,
  );
  // 2. 发布快照只携带数据，组件与图标始终从本地白名单解析；任一事实异常时清空旧入口。
  const navigation = useMemo(() => {
    // 策略查询失败时清空所有历史入口，避免静态回退或旧快照继续暴露已撤销页面。
    if (authorizationUnavailable) return [];
    return release.data?.snapshot
      ? buildDynamicNavigation(release.data, iconByKey, roles.data ?? null)
      : staticWorkspaceNavigation;
  }, [authorizationUnavailable, release.data, roles.data]);
  // 3. 权限码集合只裁剪按钮和页面入口，后端 PDP 仍对每次请求独立授权。
  const visiblePermissionCodes = useMemo(() => {
    if (authorizationUnavailable) return new Set<string>();
    if (!release.data?.snapshot) {
      return new Set(
        resourceRegistry.menus
          .filter((menu) => menu.status === "active" && menu.permission_code)
          .map((menu) => menu.permission_code!),
      );
    }
    const roleIds = new Set(roles.data?.roles.map((role) => role.role_id) ?? []);
    const hiddenMenuIds = new Set(
      release.data.snapshot.role_menus
        .filter((item) => roleIds.has(item.role_id) && !item.visible)
        .map((item) => item.menu_id),
    );
    return new Set(
      release.data.snapshot.menus
        .filter(
          (menu) =>
            menu.status === "active" &&
            menu.visible &&
            !hiddenMenuIds.has(menu.menu_id) &&
            menu.permission_code,
        )
        .map((menu) => menu.permission_code!),
    );
  }, [authorizationUnavailable, release.data, roles.data]);
  return {
    release,
    roles,
    navigation,
    visiblePermissionCodes,
    isLoading: release.isLoading || roles.isLoading || workspace.workspaces.isLoading,
    error: release.error ?? roles.error ?? workspace.workspaces.error,
    hasPublishedRelease: Boolean(release.data?.snapshot),
  };
}
