/** @description P6A-02 知识组织查询启用、批量关系写入和缓存刷新测试。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { App as AntdApp } from "antd";
import type { PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KnowledgeDocumentSummary } from "@/api/services/knowledge";

const organizationApi = vi.hoisted(() => ({
  bindKnowledgeDocumentFolder: vi.fn(),
  bindKnowledgeDocumentTags: vi.fn(),
  createKnowledgeFolder: vi.fn(),
  createKnowledgeTag: vi.fn(),
  deleteKnowledgeFolder: vi.fn(),
  deleteKnowledgeTag: vi.fn(),
  getKnowledgeFavorites: vi.fn(),
  getKnowledgeFolders: vi.fn(),
  getKnowledgeTags: vi.fn(),
  getKnowledgeTrash: vi.fn(),
  moveKnowledgeFolder: vi.fn(),
  purgeKnowledgeDocument: vi.fn(),
  purgeKnowledgeFolder: vi.fn(),
  renameKnowledgeFolder: vi.fn(),
  restoreKnowledgeDocument: vi.fn(),
  restoreKnowledgeFolder: vi.fn(),
  restoreKnowledgeTag: vi.fn(),
  setKnowledgeDocumentFavorite: vi.fn(),
  unbindKnowledgeDocumentTag: vi.fn(),
  updateKnowledgeTag: vi.fn(),
}));
const knowledgeApi = vi.hoisted(() => ({ deleteKnowledgeDocument: vi.fn() }));

vi.mock("@/api/services/knowledgeOrganization", () => organizationApi);
vi.mock("@/api/services/knowledge", () => knowledgeApi);
vi.mock("@/hooks/useCurrentWorkspace", () => ({
  useCurrentWorkspace: () => ({ workspaceId: "20000000-0000-4000-8000-000000000708" }),
}));

import { useKnowledgeOrganization } from "./useKnowledgeOrganization";
import { useKnowledgeOrganizationActions } from "./useKnowledgeOrganizationActions";

const document: KnowledgeDocumentSummary = {
  document_id: "40000000-0000-4000-8000-000000000708",
  title: "合成组织测试文档",
  visibility: "workspace",
  security_level: "INTERNAL",
  updated_at: "2026-08-24T09:00:00Z",
  latest_version: {
    document_version_id: "41000000-0000-4000-8000-000000000708",
    workspace_id: "20000000-0000-4000-8000-000000000708",
    document_id: "40000000-0000-4000-8000-000000000708",
    version_number: 1,
    status: "published",
    content_hash: "a".repeat(64),
    created_by_account_id: "10000000-0000-4000-8000-000000000708",
    created_at: "2026-08-24T09:00:00Z",
    published_at: "2026-08-24T09:05:00Z",
    record_version: 2,
  },
  source_id: "42000000-0000-4000-8000-000000000708",
  source_kind: "upload",
  source_name: "synthetic.md",
  current_document_version_id: "41000000-0000-4000-8000-000000000708",
  folder_id: "61000000-0000-4000-8000-000000000708",
  tag_ids: ["62000000-0000-4000-8000-000000000708"],
  is_favorite: false,
};

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

describe("P6A-02 知识组织 Hook", () => {
  afterEach(() => vi.clearAllMocks());

  it("没有对应菜单权限时不发起组织读取请求", () => {
    const { queryClient, Wrapper } = createWrapper();
    const view = renderHook(
      () =>
        useKnowledgeOrganization({
          canReadFolders: false,
          canReadTags: false,
          canReadFavorites: false,
          canReadTrash: false,
        }),
      { wrapper: Wrapper },
    );

    expect(organizationApi.getKnowledgeFolders).not.toHaveBeenCalled();
    expect(organizationApi.getKnowledgeTags).not.toHaveBeenCalled();
    expect(organizationApi.getKnowledgeFavorites).not.toHaveBeenCalled();
    expect(organizationApi.getKnowledgeTrash).not.toHaveBeenCalled();
    view.unmount();
    queryClient.clear();
  });

  it("批量移动逐篇提交空间化关系并刷新文档摘要", async () => {
    organizationApi.bindKnowledgeDocumentFolder.mockResolvedValue({ items: [] });
    const { queryClient, Wrapper } = createWrapper();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const view = renderHook(() => useKnowledgeOrganizationActions(), { wrapper: Wrapper });

    await act(() =>
      view.result.current.moveDocuments.mutateAsync({
        documentIds: [document.document_id, "40000000-0000-4000-8000-000000000709"],
        folderId: "61000000-0000-4000-8000-000000000708",
      }),
    );

    expect(organizationApi.bindKnowledgeDocumentFolder).toHaveBeenCalledTimes(2);
    expect(organizationApi.bindKnowledgeDocumentFolder).toHaveBeenCalledWith(
      "20000000-0000-4000-8000-000000000708",
      document.document_id,
      "61000000-0000-4000-8000-000000000708",
    );
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["knowledge-documents", "20000000-0000-4000-8000-000000000708"],
      }),
    );
    view.unmount();
    queryClient.clear();
  });

  it("单篇标签编辑增加缺失标签并移除未选标签", async () => {
    organizationApi.bindKnowledgeDocumentTags.mockResolvedValue({ items: [] });
    organizationApi.unbindKnowledgeDocumentTag.mockResolvedValue({ items: [] });
    const { queryClient, Wrapper } = createWrapper();
    const view = renderHook(() => useKnowledgeOrganizationActions(), { wrapper: Wrapper });

    await act(() =>
      view.result.current.replaceDocumentTags.mutateAsync({
        documents: [document],
        tagIds: ["62000000-0000-4000-8000-000000000709"],
        preserveExisting: false,
      }),
    );

    expect(organizationApi.bindKnowledgeDocumentTags).toHaveBeenCalledWith(
      "20000000-0000-4000-8000-000000000708",
      document.document_id,
      ["62000000-0000-4000-8000-000000000709"],
    );
    expect(organizationApi.unbindKnowledgeDocumentTag).toHaveBeenCalledWith(
      "20000000-0000-4000-8000-000000000708",
      document.document_id,
      "62000000-0000-4000-8000-000000000708",
    );
    view.unmount();
    queryClient.clear();
  });
});
