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

export function useWorkspaceMenuNavigation() {
  const workspaceId = useSessionStore((state) => state.workspaceId);
  const accountId = useSessionStore((state) => state.accountId);
  const workspace = useCurrentWorkspace();
  const release = useQuery({
    queryKey: ["workspace-menu-release", workspaceId],
    queryFn: ({ signal }) => getCurrentWorkspaceMenuRelease(workspaceId!, signal),
    enabled: Boolean(workspaceId),
    staleTime: 0,
  });
  const roles = useQuery({
    queryKey: ["workspace-effective-roles", workspaceId, accountId],
    queryFn: ({ signal }) => getEffectiveWorkspaceRoles(workspaceId!, accountId!, signal),
    enabled: Boolean(
      workspaceId && accountId && workspace.currentWorkspace?.workspace_type === "enterprise",
    ),
    staleTime: 0,
  });
  const navigation = useMemo(
    () =>
      release.data?.snapshot
        ? buildDynamicNavigation(release.data, iconByKey, roles.data ?? null)
        : staticWorkspaceNavigation,
    [release.data, roles.data],
  );
  const visiblePermissionCodes = useMemo(() => {
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
  }, [release.data, roles.data]);
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
