/** @description P3-11 服务页面错误清空、权限裁剪和 Route 操作测试。 */
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ServiceDeployment } from "@/api/services/services";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";

import ServiceManagementPage from ".";
import { ServiceRoutePanel } from "./components/ServiceRoutePanel";
import { useServiceManagement } from "./useServiceManagement";

vi.mock("./useServiceManagement", () => ({ useServiceManagement: vi.fn() }));
vi.mock("@/hooks/useWorkspaceMenuNavigation", () => ({
  useWorkspaceMenuNavigation: vi.fn(),
}));

const deployment: ServiceDeployment = {
  service: {
    service_id: "b1000000-0000-4000-8000-000000000001",
    agent_id: "b1000000-0000-4000-8000-000000000002",
    service_key: "synthetic-service",
    name: "合成制度服务",
    service_type: "custom_knowledge_agent",
    status: "active",
    version: 3,
    updated_at: "2026-08-16T08:00:00Z",
  },
  access_policy: {
    access_policy_version_id: "b2000000-0000-4000-8000-000000000001",
    version: 2,
    visibility: "workspace",
    allowed_department_ids: [],
    allowed_account_ids: [],
    policy_hash: "a".repeat(64),
  },
  route: {
    route_id: "b3000000-0000-4000-8000-000000000001",
    route_version: 4,
    route_mode: "canary",
    primary_release_id: "b4000000-0000-4000-8000-000000000001",
    canary_release_id: "b4000000-0000-4000-8000-000000000002",
    canary_percent: 10,
    previous_route_id: "b3000000-0000-4000-8000-000000000000",
    route_hash: "b".repeat(64),
    created_at: "2026-08-16T08:00:00Z",
  },
  publication: {
    route_id: "b3000000-0000-4000-8000-000000000001",
    generation: 4,
    published_at: "2026-08-16T08:00:00Z",
  },
};

function query(data?: unknown, overrides: Record<string, unknown> = {}) {
  return {
    data,
    error: null,
    isError: false,
    isLoading: false,
    refetch: vi.fn(),
    ...overrides,
  };
}

function mutation() {
  return { isPending: false, mutate: vi.fn(), mutateAsync: vi.fn() };
}

function pageModel(overrides: Record<string, unknown> = {}) {
  return {
    services: query([deployment]),
    releaseOptions: [],
    isReleaseOptionsError: false,
    isReleaseOptionsLoading: false,
    create: mutation(),
    update: mutation(),
    startCanary: mutation(),
    promote: mutation(),
    rollback: mutation(),
    ...overrides,
  };
}

describe("P3-11 服务发布页面", () => {
  afterEach(cleanup);

  it("服务查询失败时清空旧 Route 和服务定义", () => {
    vi.mocked(useServiceManagement).mockReturnValue(
      pageModel({
        services: query([deployment], { isError: true, error: new Error("synthetic failure") }),
      }) as never,
    );
    vi.mocked(useWorkspaceMenuNavigation).mockReturnValue({
      visiblePermissionCodes: new Set<string>(),
    } as never);

    render(
      <MemoryRouter
        initialEntries={[`/workspace/services?service=${deployment.service.service_id}`]}
      >
        <ServiceManagementPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("服务发布页面未能加载")).toBeInTheDocument();
    expect(screen.queryByText("合成制度服务")).not.toBeInTheDocument();
    expect(screen.queryByText("当前 Route")).not.toBeInTheDocument();
  });

  it("只读角色看不到策略、灰度、晋级和回滚动作", () => {
    render(
      <ServiceRoutePanel
        deployment={deployment}
        canUpdate={false}
        canCanary={false}
        canPromote={false}
        canRollback={false}
        isMutating={false}
        onEdit={vi.fn()}
        onCanary={vi.fn()}
        onUpdate={vi.fn()}
        onPromote={vi.fn()}
        onRollback={vi.fn()}
      />,
    );

    expect(screen.getByText("合成制度服务")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑策略" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "晋级正式版本" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "一键回滚" })).not.toBeInTheDocument();
  });

  it("灰度 Route 只在对应权限下显示晋级和回滚", () => {
    render(
      <ServiceRoutePanel
        deployment={deployment}
        canUpdate={false}
        canCanary
        canPromote
        canRollback
        isMutating={false}
        onEdit={vi.fn()}
        onCanary={vi.fn()}
        onUpdate={vi.fn()}
        onPromote={vi.fn()}
        onRollback={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "晋级正式版本" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "一键回滚" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "启动灰度" })).not.toBeInTheDocument();
  });
});
