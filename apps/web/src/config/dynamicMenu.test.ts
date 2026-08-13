import { Activity, LayoutDashboard } from "lucide-react";
import { describe, expect, it } from "vitest";

import { buildDynamicNavigation } from "@/config/dynamicMenu";

const release = {
  item: null,
  snapshot: {
    schema_version: 1,
    registry_version: 3,
    workspace_id: "workspace-id",
    menu_version: 2,
    menus: [
      {
        menu_id: "directory",
        menu_key: "navigation.workspace",
        parent_menu_id: null,
        name: "空间管理",
        menu_type: "directory" as const,
        page_resource_id: null,
        permission_code: null,
        icon_key: null,
        sort_order: 1,
        source: "system" as const,
        status: "active" as const,
        visible: true,
      },
      {
        menu_id: "overview",
        menu_key: "navigation.workspace.overview",
        parent_menu_id: "directory",
        name: "空间总览",
        menu_type: "page" as const,
        page_resource_id: "80000000-0000-4000-8000-000000000002",
        permission_code: "workspace.overview.access",
        icon_key: "layout-dashboard",
        sort_order: 20,
        source: "system" as const,
        status: "active" as const,
        visible: true,
      },
      {
        menu_id: "status",
        menu_key: "navigation.workspace.status",
        parent_menu_id: "directory",
        name: "运行状态",
        menu_type: "page" as const,
        page_resource_id: "80000000-0000-4000-8000-000000000005",
        permission_code: "system.runtime.access",
        icon_key: "activity",
        sort_order: 30,
        source: "system" as const,
        status: "active" as const,
        visible: true,
      },
    ],
    role_menus: [{ role_id: "member-role", menu_id: "status", visible: false }],
    menu_api_bindings: [],
  },
} as const;

describe("动态菜单模型", () => {
  it("只保留当前角色可见且已注册页面", () => {
    const items = buildDynamicNavigation(
      release,
      { "layout-dashboard": LayoutDashboard, activity: Activity },
      {
        account_id: "account",
        membership_id: "membership",
        role_version: 1,
        roles: [{ role_id: "member-role", role_key: "member", name: "成员", sources: [] }],
      },
    );

    expect(items.map((item) => [item.label, item.to])).toEqual([
      ["空间总览", "/workspace/overview"],
    ]);
  });

  it("没有快照时返回空导航", () => {
    expect(buildDynamicNavigation({ item: null, snapshot: null }, {}, null)).toEqual([]);
  });
});
