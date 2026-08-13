import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
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
      ["运行状态", "/status", "system.runtime.access"],
    ]);
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
