/** @description P6B-01 企业控制台加载、错误、空态、统计和真实快捷入口测试。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { App as AntdApp } from "antd";
import type { PropsWithChildren } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const workspaceApi = vi.hoisted(() => ({ getEnterpriseConsole: vi.fn() }));

vi.mock("@/api/services/workspaces", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/services/workspaces")>()),
  getEnterpriseConsole: workspaceApi.getEnterpriseConsole,
}));

import type { EnterpriseConsole as EnterpriseConsoleSnapshot } from "@/api/services/workspaces";
import { pageRoutes } from "@/config/resources";

import { EnterpriseConsole } from "./EnterpriseConsole";

const WORKSPACE_ID = "20000000-0000-4000-8000-000000000901";
const snapshot: EnterpriseConsoleSnapshot = {
  workspace: {
    workspace_id: WORKSPACE_ID,
    workspace_type: "enterprise",
    name: "合成企业空间",
    status: "active",
    description: "仅用于自动化测试的企业资料。",
    logo_url: null,
  },
  statistics: {
    active_member_count: 18,
    active_knowledge_base_count: 4,
    active_document_count: 32,
    published_document_count: 27,
    processing_document_count: 3,
    failed_document_count: 1,
    storage_used_bytes: 2 * 1024 ** 3,
    storage_limit_bytes: 10 * 1024 ** 3,
  },
  trend: [
    { period: "2026-03", document_count: 0 },
    { period: "2026-04", document_count: 3 },
    { period: "2026-05", document_count: 5 },
    { period: "2026-06", document_count: 7 },
    { period: "2026-07", document_count: 8 },
    { period: "2026-08", document_count: 9 },
  ],
  recent_documents: [
    {
      document_id: "40000000-0000-4000-8000-000000000901",
      knowledge_base_id: "30000000-0000-4000-8000-000000000901",
      knowledge_base_name: "合成制度库",
      title: "合成安全制度",
      updated_at: "2026-08-31T08:30:00Z",
      published_at: "2026-08-31T08:20:00Z",
      status: "published",
    },
  ],
  generated_at: "2026-08-31T08:35:00Z",
  time_window_start: "2026-03-01T00:00:00Z",
  time_window_end: "2026-08-31T08:35:00Z",
  consistency: "eventually_consistent",
};

const clients: QueryClient[] = [];

function renderConsole(onAcceptInvitation = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  clients.push(client);
  const Wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={client}>
      <AntdApp>
        <MemoryRouter initialEntries={[pageRoutes.WorkspaceOverviewPage]}>{children}</MemoryRouter>
      </AntdApp>
    </QueryClientProvider>
  );
  return render(
    <EnterpriseConsole workspaceId={WORKSPACE_ID} onAcceptInvitation={onAcceptInvitation} />,
    { wrapper: Wrapper },
  );
}

describe("P6B-01 企业控制台", () => {
  beforeEach(() => workspaceApi.getEnterpriseConsole.mockResolvedValue(snapshot));

  afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    vi.clearAllMocks();
  });

  it("加载期间显示骨架，失败后提供可恢复入口", async () => {
    workspaceApi.getEnterpriseConsole.mockReturnValueOnce(new Promise(() => undefined));
    const loading = renderConsole();
    expect(loading.container.querySelector(".ant-skeleton")).toBeInTheDocument();
    loading.unmount();

    workspaceApi.getEnterpriseConsole.mockRejectedValueOnce(
      new Error("synthetic enterprise console failure"),
    );
    renderConsole();
    expect(await screen.findByText("企业控制台未能加载")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新加载" })).toBeInTheDocument();
  });

  it("展示统一统计、趋势、存储和最近内容", async () => {
    renderConsole();
    expect(await screen.findAllByText("合成企业空间")).toHaveLength(2);
    expect(screen.getByText("18")).toBeInTheDocument();
    expect(screen.getByText("27 篇已发布")).toBeInTheDocument();
    expect(screen.getByText("1 篇需关注")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "知识增长趋势：0、3、5、7、8、9" })).toBeInTheDocument();
    expect(screen.getByText("2.0 GB")).toBeInTheDocument();
    expect(screen.getByText("合成安全制度")).toBeInTheDocument();
    expect(workspaceApi.getEnterpriseConsole).toHaveBeenCalledWith(
      WORKSPACE_ID,
      expect.any(AbortSignal),
    );
  });

  it("最近内容被字段权限裁剪时显示明确空态", async () => {
    workspaceApi.getEnterpriseConsole.mockResolvedValueOnce({
      ...snapshot,
      statistics: { ...snapshot.statistics, storage_used_bytes: 0, storage_limit_bytes: 0 },
      recent_documents: [],
    });
    renderConsole();
    expect(await screen.findByText("暂无可展示的最近内容")).toBeInTheDocument();
    expect(screen.getByText(/已使用 0%/)).toBeInTheDocument();
  });

  it("邀请操作和治理入口只连接现有真实路由", async () => {
    const onAcceptInvitation = vi.fn();
    renderConsole(onAcceptInvitation);
    await screen.findByText("合成安全制度");
    fireEvent.click(screen.getByRole("button", { name: "接受邀请" }));
    expect(onAcceptInvitation).toHaveBeenCalledOnce();
    expect(screen.getByRole("link", { name: /成员管理/ })).toHaveAttribute(
      "href",
      pageRoutes.WorkspaceMembersPage,
    );
    expect(screen.getByRole("link", { name: /组织架构/ })).toHaveAttribute(
      "href",
      pageRoutes.WorkspaceOrganizationPage,
    );
    expect(screen.getByRole("link", { name: /知识生产/ })).toHaveAttribute(
      "href",
      pageRoutes.KnowledgeProductionPage,
    );
    expect(screen.getByRole("link", { name: /知识助手/ })).toHaveAttribute(
      "href",
      pageRoutes.AssistantConversationsPage,
    );
  });
});
