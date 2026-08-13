import type { LucideIcon } from "lucide-react";

import type { CurrentMenuRelease, EffectiveRoleSet } from "@/api/services/workspaces";
import { resourceRegistry } from "@/config/resourceRegistry.generated";

export interface DynamicNavigationItem {
  key: string;
  to: string;
  label: string;
  permissionCode: string | null;
  icon: LucideIcon;
  sortOrder: number;
}

const pageResourceById = new Map(
  resourceRegistry.page_resources.map((resource) => [resource.page_resource_id, resource]),
);

/** 动态快照只决定已注册页面入口；组件和路由仍来自静态注册表，防止服务端数据执行任意代码。 */
export function buildDynamicNavigation(
  release: CurrentMenuRelease,
  icons: Record<string, LucideIcon>,
  roles: EffectiveRoleSet | null,
): DynamicNavigationItem[] {
  const snapshot = release.snapshot;
  if (!snapshot) return [];
  const roleIds = new Set(roles?.roles.map((role) => role.role_id) ?? []);
  const hiddenByRole = new Set(
    snapshot.role_menus
      .filter((item) => roleIds.has(item.role_id) && !item.visible)
      .map((item) => item.menu_id),
  );
  const menusById = new Map(snapshot.menus.map((menu) => [menu.menu_id, menu]));
  const items: Array<DynamicNavigationItem | null> = snapshot.menus
    .filter(
      (menu) =>
        menu.status === "active" &&
        menu.visible &&
        menu.menu_type === "page" &&
        menu.page_resource_id !== null &&
        !hiddenByRole.has(menu.menu_id),
    )
    .map((menu) => {
      const page = pageResourceById.get(
        menu.page_resource_id as Parameters<typeof pageResourceById.get>[0],
      );
      const icon = menu.icon_key ? icons[menu.icon_key] : undefined;
      const parent = menu.parent_menu_id ? menusById.get(menu.parent_menu_id) : undefined;
      if (!page || !icon || !parent || parent.menu_type !== "directory") return null;
      return {
        key: menu.menu_key,
        to: page.route,
        label: menu.name,
        permissionCode: menu.permission_code,
        icon,
        sortOrder: menu.sort_order,
      } satisfies DynamicNavigationItem;
    });
  return items
    .filter((item): item is DynamicNavigationItem => item !== null)
    .sort((left, right) => left.sortOrder - right.sortOrder);
}
