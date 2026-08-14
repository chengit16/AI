import {
  Activity,
  Cpu,
  LibraryBig,
  LayoutDashboard,
  Network,
  UsersRound,
  type LucideIcon,
} from "lucide-react";

import { resourceRegistry } from "@/config/resourceRegistry.generated";

export const pageById = new Map(
  resourceRegistry.page_resources.map((resource) => [resource.page_resource_id, resource]),
);
export const iconByKey = {
  activity: Activity,
  cpu: Cpu,
  "library-big": LibraryBig,
  "layout-dashboard": LayoutDashboard,
  network: Network,
  "users-round": UsersRound,
} satisfies Record<string, LucideIcon>;

export const pageRoutes = Object.fromEntries(
  resourceRegistry.page_resources.map((resource) => [resource.component_key, resource.route]),
) as Record<(typeof resourceRegistry.page_resources)[number]["component_key"], string>;

export const staticWorkspaceNavigation = resourceRegistry.menus
  .filter((menu) => {
    const page = menu.page_resource_id ? pageById.get(menu.page_resource_id) : undefined;
    return (
      menu.status === "active" && menu.menu_type === "page" && page?.access_level === "authorized"
    );
  })
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

export const staticPlatformNavigation = resourceRegistry.menus
  .filter((menu) => {
    const page = menu.page_resource_id ? pageById.get(menu.page_resource_id) : undefined;
    return (
      menu.status === "active" &&
      menu.menu_type === "page" &&
      page?.access_level === "platform_admin"
    );
  })
  .map((menu) => {
    const page = pageById.get(menu.page_resource_id!);
    const icon = menu.icon_key ? iconByKey[menu.icon_key as keyof typeof iconByKey] : undefined;
    if (!page || !icon) throw new Error(`平台菜单资源未注册完整: ${menu.menu_key}`);
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

/** 未发布空间的兼容入口；首次发布后由 MenuRelease 快照接管。 */
export const workspaceNavigation = staticWorkspaceNavigation;
