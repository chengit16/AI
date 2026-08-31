/** @description P6B-03 企业知识 Hook 的启用、统一刷新与乐观冲突反馈测试。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, screen, waitFor } from "@testing-library/react";
import { App as AntdApp } from "antd";
import type { PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PlatformApiError } from "@/api/client";

const api = vi.hoisted(() => ({
  archiveEnterpriseCategory: vi.fn(),
  archiveTeamKnowledgeDomain: vi.fn(),
  createEnterpriseCategory: vi.fn(),
  createTeamKnowledgeDomain: vi.fn(),
  getEnterpriseKnowledgePortal: vi.fn(),
  replaceEnterpriseCategoryDocuments: vi.fn(),
  replaceTeamKnowledgeDomainScope: vi.fn(),
  resolveTeamKnowledgeDomainScope: vi.fn(),
  updateEnterpriseCategory: vi.fn(),
  updateTeamKnowledgeDomain: vi.fn(),
}));
vi.mock("@/api/services/enterpriseKnowledge", () => api);

import { shouldRetryEnterpriseKnowledge, useEnterpriseKnowledge } from "./useEnterpriseKnowledge";

const WORKSPACE_ID = "20000000-0000-4000-8000-000000000904";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  const Wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>
      <AntdApp>{children}</AntdApp>
    </QueryClientProvider>
  );
  return { queryClient, Wrapper };
}

describe("P6B-03 企业知识 Hook", () => {
  afterEach(() => vi.clearAllMocks());

  it("没有读取条件时不请求门户，只重试明确可恢复的策略传播错误", () => {
    const { queryClient, Wrapper } = createWrapper();
    const view = renderHook(
      () => useEnterpriseKnowledge({ workspaceId: WORKSPACE_ID, enabled: false }),
      { wrapper: Wrapper },
    );
    expect(api.getEnterpriseKnowledgePortal).not.toHaveBeenCalled();
    const propagating = new PlatformApiError(503, "POLICY_UNAVAILABLE", "传播中", true);
    expect(shouldRetryEnterpriseKnowledge(3, propagating)).toBe(true);
    expect(shouldRetryEnterpriseKnowledge(4, propagating)).toBe(false);
    expect(shouldRetryEnterpriseKnowledge(0, new Error("合成失败"))).toBe(false);
    view.unmount();
    queryClient.clear();
  });

  it("分类写入成功后只刷新当前空间的统一快照", async () => {
    api.getEnterpriseKnowledgePortal.mockResolvedValue({ categories: [], domains: [] });
    api.createEnterpriseCategory.mockResolvedValue({ category_id: "category-1" });
    const { queryClient, Wrapper } = createWrapper();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const view = renderHook(
      () => useEnterpriseKnowledge({ workspaceId: WORKSPACE_ID, enabled: true }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(api.getEnterpriseKnowledgePortal).toHaveBeenCalledOnce());
    await act(() =>
      view.result.current.createCategory.mutateAsync({
        name: "合成分类",
        description: null,
        parent_category_id: null,
        visibility: "public",
        department_ids: [],
        document_ids: [],
      }),
    );
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["enterprise-knowledge", WORKSPACE_ID],
    });
    view.unmount();
    queryClient.clear();
  });

  it("乐观版本冲突显示稳定提示且不伪造成功", async () => {
    api.getEnterpriseKnowledgePortal.mockResolvedValue({ categories: [], domains: [] });
    api.createEnterpriseCategory.mockRejectedValue(
      new PlatformApiError(409, "KNOWLEDGE_CONFLICT", "原始服务端文本", false),
    );
    const { queryClient, Wrapper } = createWrapper();
    const view = renderHook(
      () => useEnterpriseKnowledge({ workspaceId: WORKSPACE_ID, enabled: true }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(api.getEnterpriseKnowledgePortal).toHaveBeenCalledOnce());
    await act(async () => {
      await expect(
        view.result.current.createCategory.mutateAsync({
          name: "冲突分类",
          description: null,
          parent_category_id: null,
          visibility: "public",
          department_ids: [],
          document_ids: [],
        }),
      ).rejects.toBeInstanceOf(PlatformApiError);
    });
    expect(await screen.findByText("数据已被其他操作更新，请刷新后重试")).toBeInTheDocument();
    view.unmount();
    queryClient.clear();
  });

  it("活动子分类阻止归档时保留服务端稳定业务提示", async () => {
    api.getEnterpriseKnowledgePortal.mockResolvedValue({ categories: [], domains: [] });
    api.archiveEnterpriseCategory.mockRejectedValue(
      new PlatformApiError(
        409,
        "CATEGORY_HAS_ACTIVE_CHILDREN",
        "分类仍存在活动子分类，请先归档子分类",
        false,
      ),
    );
    const { queryClient, Wrapper } = createWrapper();
    const view = renderHook(
      () => useEnterpriseKnowledge({ workspaceId: WORKSPACE_ID, enabled: true }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(api.getEnterpriseKnowledgePortal).toHaveBeenCalledOnce());
    await act(async () => {
      await expect(
        view.result.current.archiveCategory.mutateAsync({
          category_id: "30000000-0000-4000-8000-000000000904",
          parent_category_id: null,
          name: "合成父分类",
          description: null,
          visibility: "public",
          department_ids: [],
          document_ids: [],
          status: "active",
          created_at: "2026-08-31T00:00:00Z",
          updated_at: "2026-08-31T00:00:00Z",
          version: 3,
        }),
      ).rejects.toBeInstanceOf(PlatformApiError);
    });
    expect(await screen.findByText("分类仍存在活动子分类，请先归档子分类")).toBeInTheDocument();
    view.unmount();
    queryClient.clear();
  });
});
