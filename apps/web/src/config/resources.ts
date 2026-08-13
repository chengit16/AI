import { Activity, LayoutDashboard, Network, UsersRound, type LucideIcon } from "lucide-react";

import { resourceRegistry } from "@/config/resourceRegistry.generated";

const pageById = new Map(
  resourceRegistry.page_resources.map((resource) => [resource.page_resource_id, resource]),
);
const iconByKey = {
  activity: Activity,
  "layout-dashboard": LayoutDashboard,
  network: Network,
  "users-round": UsersRound,
} satisfies Record<string, LucideIcon>;

export const pageRoutes = Object.fromEntries(
  resourceRegistry.page_resources.map((resource) => [resource.component_key, resource.route]),
) as Record<(typeof resourceRegistry.page_resources)[number]["component_key"], string>;

export const workspaceNavigation = resourceRegistry.menus
  .filter((menu) => menu.status === "active" && menu.menu_type === "page" && menu.page_resource_id)
  .map((menu) => {
    const page = pageById.get(menu.page_resource_id!);
    const icon = menu.icon_key ? iconByKey[menu.icon_key as keyof typeof iconByKey] : undefined;
    if (!page || !icon) {
      throw new Error(`系统菜单资源未注册完整: ${menu.menu_key}`);
    }
    return {
      key: menu.menu_key,
      to: page.route,
      label: menu.name,
      permissionCode: menu.permission_code,
      icon,
      sortOrder: menu.sort_order,
    };
  })
  .sort((left, right) => left.sortOrder - right.sortOrder);
