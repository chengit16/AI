/** @description 应用根路由与会话状态的集成测试。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/app/App";
import { pageRoutes, workspaceNavigation } from "@/config/resources";
import { useSessionStore } from "@/store/session";

function renderApp(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

const publishedMenuRelease = {
  item: { status: "published" },
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
        menu_type: "directory",
        page_resource_id: null,
        permission_code: null,
        icon_key: null,
        sort_order: 1,
        source: "system",
        status: "active",
        visible: true,
      },
      {
        menu_id: "overview",
        menu_key: "navigation.workspace.overview",
        parent_menu_id: "directory",
        name: "已发布总览",
        menu_type: "page",
        page_resource_id: "80000000-0000-4000-8000-000000000002",
        permission_code: "workspace.overview.access",
        icon_key: "layout-dashboard",
        sort_order: 10,
        source: "system",
        status: "active",
        visible: true,
      },
      {
        menu_id: "status",
        menu_key: "navigation.workspace.status",
        parent_menu_id: "directory",
        name: "运行状态",
        menu_type: "page",
        page_resource_id: "80000000-0000-4000-8000-000000000005",
        permission_code: "system.runtime.access",
        icon_key: "activity",
        sort_order: 20,
        source: "system",
        status: "active",
        visible: true,
      },
    ],
    role_menus: [],
    menu_api_bindings: [],
  },
};

const syntheticWorkspaces = {
  items: [
    {
      workspace_id: "workspace-id",
      workspace_type: "personal",
      name: "合成个人空间",
      status: "active",
      membership_type: "owner",
      membership_status: "active",
    },
  ],
};

/** 构造应用壳层测试使用的 JSON 响应。 */
function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

/** 按请求路径返回发布菜单测试所需的最小合成依赖。 */
function publishedNavigationResponse(input: RequestInfo | URL) {
  const url = String(input);
  if (url.endsWith("/menu-releases/current")) return jsonResponse(publishedMenuRelease);
  if (url.endsWith("/api/v1/workspaces")) return jsonResponse(syntheticWorkspaces);
  if (url.endsWith("/api/v1/platform/administration"))
    return jsonResponse({ is_platform_administrator: false });
  if (url.includes("/entitlements"))
    return jsonResponse({
      workspace_id: "workspace-id",
      plan_code: "personal_local",
      entitlement_version: 1,
      open_api_allowed: false,
      open_api_enabled: false,
      quotas: [],
    });
  if (url.includes("/roles/effective/"))
    return jsonResponse({
      account_id: "account-id",
      membership_id: "membership-id",
      role_version: 1,
      roles: [],
    });
  if (url.endsWith("/api/v1/health/ready"))
    return jsonResponse({
      service: "ai-platform-api",
      status: "ok",
      version: "0.0.0",
      environment: "local",
      checks: { api: "ok", configuration: "ok" },
    });
  return jsonResponse({ items: [] });
}

describe("平台路由与运行状态", () => {
  beforeEach(() => {
    useSessionStore.getState().clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("未登录访问业务页面时进入登录页", async () => {
    renderApp(pageRoutes.WorkspaceOverviewPage);

    expect(await screen.findByRole("heading", { name: "进入平台" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "登录" })).toBeInTheDocument();
  });

  it("现有业务导航全部来自权限资源注册表", () => {
    expect(workspaceNavigation.map((item) => [item.label, item.to, item.permissionCode])).toEqual([
      ["空间总览", "/workspace/overview", "workspace.overview.access"],
      ["成员管理", "/workspace/members", "workspace.members.access"],
      ["组织架构", "/workspace/organization", "organization.structure.access"],
      ["权限与审计", "/workspace/permissions-audit", "authorization.role_permission.read"],
      ["知识生产", "/workspace/knowledge", "knowledge.production.access"],
      ["企业知识库", "/workspace/enterprise-knowledge", "enterprise.knowledge.access"],
      ["知识问答", "/workspace/assistant", "assistant.page.access"],
      ["Agent 控制台", "/workspace/agents", "agent.page.access"],
      ["AI 企业大脑", "/workspace/enterprise-brain", "enterprise.brain.access"],
      ["工作流", "/workspace/workflows", "workflow.page.access"],
      ["服务发布", "/workspace/services", "service.page.access"],
      ["Release 运营", "/workspace/agent-operations", "agent.operations.read"],
      ["工具执行", "/workspace/tools", "tool.page.access"],
      ["工具任务", "/workspace/tool-runs", "tool.run.read"],
      ["运行状态", "/status", "system.runtime.access"],
      ["治理控制台", "/workspace/control-tower", "operations.control_tower.read"],
    ]);
  });

  it("按发布快照导航，并在路由变化后聚焦主内容", async () => {
    // 1. 建立已登录的合成个人空间，确保动态菜单请求具有可信会话上下文。
    useSessionStore
      .getState()
      .setAuthenticated("account-id", "workspace-id", "synthetic-csrf-token");
    // 2. 同时模拟菜单快照及页面依赖，验证导航只消费后端当前发布事实。
    vi.spyOn(globalThis, "fetch").mockImplementation(publishedNavigationResponse);

    // 3. 渲染真实应用壳层，并从最终页面文案确认发布菜单已经驱动路由。
    renderApp(pageRoutes.WorkspaceOverviewPage);

    expect((await screen.findAllByText("已发布总览")).length).toBeGreaterThanOrEqual(1);
    expect(await screen.findByRole("link", { name: "跳到主要内容" })).toHaveAttribute(
      "href",
      "#main-content",
    );
    expect(screen.queryByRole("navigation", { name: "平台治理" })).not.toBeInTheDocument();
    const desktopNavigationScrollRegion = screen.getByRole("navigation", {
      name: "空间管理",
    }).parentElement;
    expect(desktopNavigationScrollRegion).toHaveClass(
      "min-h-0",
      "flex-1",
      "overflow-y-auto",
      "overscroll-contain",
    );

    // 4. 下拉选项必须暴露业务名称，不能把内部 UUID 当成辅助技术可读标签。
    const workspaceSelect = screen.getByRole("combobox", { name: "切换工作空间" });
    fireEvent.mouseDown(workspaceSelect);
    expect(await screen.findByRole("option", { name: "合成个人空间" })).toBeInTheDocument();
    fireEvent.keyDown(workspaceSelect, { key: "Escape" });

    // 5. 导航到另一个真实页面，确认路由变化后焦点回到主要内容阅读起点。
    fireEvent.click(screen.getByRole("link", { name: "运行状态" }));
    expect(await screen.findByRole("heading", { level: 1, name: "运行状态" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("main")).toHaveFocus());
  });

  it("菜单策略不可用时立即清空旧导航并失败关闭", async () => {
    useSessionStore
      .getState()
      .setAuthenticated("account-id", "workspace-id", "synthetic-csrf-token");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/menu-releases/current")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              code: "POLICY_UNAVAILABLE",
              message: "权限策略暂时不可用，操作已默认拒绝",
              retryable: true,
              request_id: "10000000-0000-4000-8000-000000000209",
              trace_id: "9123456789abcdef0123456789abcdef",
            }),
            { status: 503, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      if (url.endsWith("/api/v1/workspaces")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              items: [
                {
                  workspace_id: "workspace-id",
                  workspace_type: "personal",
                  name: "合成个人空间",
                  status: "active",
                  membership_type: "owner",
                  membership_status: "active",
                },
              ],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    });

    renderApp(pageRoutes.WorkspaceOverviewPage);

    expect(
      await screen.findByRole("heading", { level: 1, name: "菜单暂时无法加载" }),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("link", { name: "空间总览" })).not.toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "运行状态" })).not.toBeInTheDocument();
    });
  });

  it("展示后端返回的健康状态", async () => {
    useSessionStore
      .getState()
      .setAuthenticated("account-id", "workspace-id", "synthetic-csrf-token");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      const body = url.endsWith("/api/v1/workspaces")
        ? {
            items: [
              {
                workspace_id: "workspace-id",
                workspace_type: "personal",
                name: "合成个人空间",
                status: "active",
                membership_type: "owner",
                membership_status: "active",
              },
            ],
          }
        : {
            service: "ai-platform-api",
            status: "ok",
            version: "0.0.0",
            environment: "local",
            checks: { api: "ok", configuration: "ok" },
          };
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    });

    renderApp(pageRoutes.StatusPage);

    expect(await screen.findByText("基础服务运行正常")).toBeInTheDocument();
    expect(screen.getByText("平台 API")).toBeInTheDocument();
    expect(screen.getByText("配置中心")).toBeInTheDocument();
  });
});
