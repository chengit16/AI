/** @description 知识生产 Hook 缓存刷新、任务轮询和失败提示测试。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntdApp } from "antd";
import type { PropsWithChildren } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  createKnowledgeBase: vi.fn(),
  getKnowledgeBases: vi.fn(),
  getKnowledgeDocuments: vi.fn(),
  getKnowledgeIngestionJobs: vi.fn(),
  markKnowledgeDocumentVersionReady: vi.fn(),
  publishKnowledgeDocumentVersion: vi.fn(),
  retryKnowledgeIngestionJob: vi.fn(),
  uploadKnowledgeDocument: vi.fn(),
  uploadKnowledgeDocumentVersion: vi.fn(),
}));

vi.mock("@/api/services/knowledge", () => api);
vi.mock("@/hooks/useCurrentWorkspace", () => ({
  useCurrentWorkspace: () => ({ workspaceId: "20000000-0000-4000-8000-000000000707" }),
}));

import { useKnowledgeProduction } from "./useKnowledgeProduction";

const KNOWLEDGE_BASE_ID = "30000000-0000-4000-8000-000000000707";

describe("P1D-07 知识生产查询同步", () => {
  afterEach(() => vi.clearAllMocks());

  it("入库任务获得新快照后同步刷新文档事实", async () => {
    api.getKnowledgeBases.mockResolvedValue([]);
    api.getKnowledgeDocuments.mockResolvedValue([]);
    api.getKnowledgeIngestionJobs.mockResolvedValue([
      {
        ingestion_job_id: "50000000-0000-4000-8000-000000000707",
        status: "succeeded",
      },
    ]);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    const Wrapper = ({ children }: PropsWithChildren) => (
      <QueryClientProvider client={queryClient}>
        <AntdApp>{children}</AntdApp>
      </QueryClientProvider>
    );

    const view = renderHook(() => useKnowledgeProduction(KNOWLEDGE_BASE_ID), {
      wrapper: Wrapper,
    });

    await waitFor(() => expect(api.getKnowledgeIngestionJobs).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.getKnowledgeDocuments).toHaveBeenCalledTimes(2));

    view.unmount();
    queryClient.clear();
  });
});
