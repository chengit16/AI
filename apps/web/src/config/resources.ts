/**
 * @description 生成资源注册表的前端消费映射
 * 集中维护页面组件、图标、静态回退导航与 permission_code 绑定。
 */
import {
  Activity,
  Bot,
  Cpu,
  History,
  LibraryBig,
  LayoutDashboard,
  MessageSquareText,
  Network,
  Route,
  UsersRound,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import { resourceRegistry } from "@/config/resourceRegistry.generated";

/** 按不可变页面资源 ID 查找静态组件和路由声明。 */
export const pageById = new Map(
  resourceRegistry.page_resources.map((resource) => [resource.page_resource_id, resource]),
);
/** 服务端菜单可引用的图标白名单，禁止快照加载任意组件。 */
export const iconByKey = {
  activity: Activity,
  bot: Bot,
  cpu: Cpu,
  history: History,
  "library-big": LibraryBig,
  "layout-dashboard": LayoutDashboard,
  network: Network,
  route: Route,
  "message-square-text": MessageSquareText,
  "users-round": UsersRound,
  wrench: Wrench,
} satisfies Record<string, LucideIcon>;

/** 按组件键暴露类型安全的页面路由表。 */
export const pageRoutes = Object.fromEntries(
  resourceRegistry.page_resources.map((resource) => [resource.component_key, resource.route]),
) as Record<(typeof resourceRegistry.page_resources)[number]["component_key"], string>;

/** 空间级菜单尚未发布时使用的已授权页面导航。 */
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

/** 仅平台管理员入口使用的平台级静态导航。 */
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
