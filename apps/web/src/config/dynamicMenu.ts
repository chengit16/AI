/**
 * @description 服务端菜单发布快照到前端导航树的转换器
 * 只构造可见导航，页面和接口安全边界仍由后端权限策略执行。
 */
import type { LucideIcon } from "lucide-react";

import type { CurrentMenuRelease, EffectiveRoleSet } from "@/api/services/workspaces";
import { resourceRegistry } from "@/config/resourceRegistry.generated";

/** 从不可变菜单快照解析出的安全导航项。 */
export interface DynamicNavigationItem {
  /** 稳定菜单键，用于导航选中与 React 列表标识。 */
  key: string;
  /** 已在静态资源注册表中声明的路由。 */
  to: string;
  /** 当前菜单发布版本冻结的展示名称。 */
  label: string;
  /** 页面体验权限码；接口仍由服务端单独授权。 */
  permissionCode: string | null;
  /** 由静态 icon_key 白名单解析的图标组件。 */
  icon: LucideIcon;
  /** 同级导航稳定排序值。 */
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
