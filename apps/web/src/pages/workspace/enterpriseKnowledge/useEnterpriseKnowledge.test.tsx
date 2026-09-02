/** @description P6B-04 企业知识 Hook 的治理、发布审批刷新与错误反馈测试。 */
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
  getEnterpriseDocumentDetail: vi.fn(),
  listEnterpriseDocumentPublishRequests: vi.fn(),
  requestEnterpriseDocumentPublish: vi.fn(),
  replaceEnterpriseCategoryDocuments: vi.fn(),
  replaceTeamKnowledgeDomainScope: vi.fn(),
  resolveTeamKnowledgeDomainScope: vi.fn(),
  updateEnterpriseCategory: vi.fn(),
  updateTeamKnowledgeDomain: vi.fn(),
}));
vi.mock("@/api/services/enterpriseKnowledge", () => api);
const workflowApi = vi.hoisted(() => ({
  actOnApproval: vi.fn(),
  transferApproval: vi.fn(),
}));
vi.mock("@/api/services/workflows", () => workflowApi);

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

describe("P6B-04 企业知识 Hook", () => {
  afterEach(() => vi.clearAllMocks());

  it("没有读取条件时不请求门户，只重试明确可恢复的策略传播错误", () => {
    const { queryClient, Wrapper } = createWrapper();
    const view = renderHook(
      () =>
        useEnterpriseKnowledge({
          workspaceId: WORKSPACE_ID,
          enabled: false,
          publishRequestsEnabled: false,
        }),
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
      () =>
        useEnterpriseKnowledge({
          workspaceId: WORKSPACE_ID,
          enabled: true,
          publishRequestsEnabled: false,
        }),
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
        approval_required: false,
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
      () =>
        useEnterpriseKnowledge({
          workspaceId: WORKSPACE_ID,
          enabled: true,
          publishRequestsEnabled: false,
        }),
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
          approval_required: false,
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
      () =>
        useEnterpriseKnowledge({
          workspaceId: WORKSPACE_ID,
          enabled: true,
          publishRequestsEnabled: false,
        }),
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
          approval_required: false,
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

  it("提交发布申请后刷新参与者台账和企业知识快照", async () => {
    api.getEnterpriseKnowledgePortal.mockResolvedValue({ categories: [], domains: [] });
    api.listEnterpriseDocumentPublishRequests.mockResolvedValue([]);
    api.requestEnterpriseDocumentPublish.mockResolvedValue({ status: "pending" });
    const { queryClient, Wrapper } = createWrapper();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const view = renderHook(
      () =>
        useEnterpriseKnowledge({
          workspaceId: WORKSPACE_ID,
          enabled: true,
          publishRequestsEnabled: true,
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(api.listEnterpriseDocumentPublishRequests).toHaveBeenCalledOnce());

    await act(() =>
      view.result.current.requestPublish.mutateAsync({
        documentId: "document-1",
        documentVersionId: "version-1",
        idempotencyKey: "synthetic-p6b04-request",
      }),
    );

    expect(api.requestEnterpriseDocumentPublish).toHaveBeenCalledWith(
      WORKSPACE_ID,
      "document-1",
      "version-1",
      "synthetic-p6b04-request",
    );
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["enterprise-document-publish-requests", WORKSPACE_ID],
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["enterprise-knowledge", WORKSPACE_ID],
    });
    view.unmount();
    queryClient.clear();
  });

  it("发布候选不就绪时显示稳定提示且不刷新成功事实", async () => {
    api.getEnterpriseKnowledgePortal.mockResolvedValue({ categories: [], domains: [] });
    api.requestEnterpriseDocumentPublish.mockRejectedValue(
      new PlatformApiError(409, "DOCUMENT_PUBLISH_NOT_READY", "原始服务端文本", false),
    );
    const { queryClient, Wrapper } = createWrapper();
    const view = renderHook(
      () =>
        useEnterpriseKnowledge({
          workspaceId: WORKSPACE_ID,
          enabled: true,
          publishRequestsEnabled: false,
        }),
      { wrapper: Wrapper },
    );
    await act(async () => {
      await expect(
        view.result.current.requestPublish.mutateAsync({
          documentId: "document-1",
          documentVersionId: "version-1",
          idempotencyKey: "synthetic-p6b04-not-ready",
        }),
      ).rejects.toBeInstanceOf(PlatformApiError);
    });
    expect(
      await screen.findByText("当前版本尚未满足发布申请条件，请刷新后重试"),
    ).toBeInTheDocument();
    view.unmount();
    queryClient.clear();
  });
});
