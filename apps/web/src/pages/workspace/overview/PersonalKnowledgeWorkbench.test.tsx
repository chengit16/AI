/** @description P6A-04 个人工作台加载、搜索、权限裁剪和快捷路由行为测试。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App as AntdApp } from "antd";
import type { PropsWithChildren } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const knowledgeApi = vi.hoisted(() => ({
  getKnowledgeBases: vi.fn(),
  getPersonalKnowledgeWorkbench: vi.fn(),
  recordPersonalWorkbenchDocumentAccess: vi.fn(),
  searchPublishedKnowledgeDocuments: vi.fn(),
}));
const assistantApi = vi.hoisted(() => ({ getAssistantConversations: vi.fn() }));
const authorization = vi.hoisted(() => ({ permissionCodes: new Set<string>() }));

vi.mock("@/api/services/knowledge", () => knowledgeApi);
vi.mock("@/api/services/assistant", () => assistantApi);
vi.mock("@/hooks/useCurrentWorkspace", () => ({
  useCurrentWorkspace: () => ({
    workspaceId: "20000000-0000-4000-8000-000000000804",
    currentWorkspace: {
      workspace_id: "20000000-0000-4000-8000-000000000804",
      name: "合成个人空间",
      workspace_type: "personal",
    },
  }),
}));
vi.mock("@/hooks/useWorkspaceMenuNavigation", () => ({
  useWorkspaceMenuNavigation: () => ({
    visiblePermissionCodes: authorization.permissionCodes,
  }),
}));

import type { KnowledgeSearchResponse, PersonalKnowledgeWorkbench } from "@/api/services/knowledge";
import { AssistantComposer } from "@/pages/workspace/assistant/components/AssistantComposer";

import { PersonalKnowledgeWorkbench as PersonalKnowledgeWorkbenchPage } from "./PersonalKnowledgeWorkbench";

const WORKSPACE_ID = "20000000-0000-4000-8000-000000000804";
const KNOWLEDGE_BASE_ID = "30000000-0000-4000-8000-000000000804";
const DOCUMENT_ID = "40000000-0000-4000-8000-000000000804";
const CONVERSATION_ID = "50000000-0000-4000-8000-000000000804";
const ALL_PERMISSIONS = new Set([
  "knowledge.document.read",
  "knowledge.base.read",
  "knowledge.folder.create",
  "knowledge.document.create",
  "assistant.page.access",
  "assistant.conversation.read",
  "assistant.message.create",
]);

const document = {
  document_id: DOCUMENT_ID,
  knowledge_base_id: KNOWLEDGE_BASE_ID,
  knowledge_base_name: "合成知识库",
  title: "合成制度手册",
  updated_at: "2026-08-25T08:00:00Z",
  published_at: "2026-08-25T08:01:00Z",
  last_accessed_at: "2026-08-25T08:02:00Z",
  is_favorite: true,
  is_indexed: true,
} as const;

const workbench: PersonalKnowledgeWorkbench = {
  statistics: {
    knowledge_base_count: 1,
    document_count: 3,
    published_document_count: 2,
    favorite_document_count: 1,
    indexed_document_count: 1,
    pending_index_document_count: 1,
  },
  recent_documents: [document],
  favorite_documents: [document],
};

const searchResponse: KnowledgeSearchResponse = {
  items: [
    {
      document_id: DOCUMENT_ID,
      knowledge_base_id: KNOWLEDGE_BASE_ID,
      knowledge_base_name: "合成知识库",
      title: "合成制度手册",
      updated_at: "2026-08-25T08:00:00Z",
      published_at: "2026-08-25T08:01:00Z",
      is_favorite: true,
      matched_by: "content",
      excerpt: "这是只用于测试的合成正文命中片段。",
      chunk_id: "60000000-0000-4000-8000-000000000804",
      sequence_no: 2,
    },
  ],
  total: 9,
  limit: 8,
  offset: 0,
  unavailable_index_document_count: 2,
  content_search_available: true,
};

const clients: QueryClient[] = [];

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>;
}

function renderWorkbench(onAcceptInvitation = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  clients.push(client);
  const Wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={client}>
      <AntdApp>
        <MemoryRouter initialEntries={["/workspace/overview"]}>
          {children}
          <LocationProbe />
        </MemoryRouter>
      </AntdApp>
    </QueryClientProvider>
  );
  return render(<PersonalKnowledgeWorkbenchPage onAcceptInvitation={onAcceptInvitation} />, {
    wrapper: Wrapper,
  });
}

describe("P6A-04 个人知识工作台", () => {
  beforeEach(() => {
    authorization.permissionCodes = new Set(ALL_PERMISSIONS);
    knowledgeApi.getPersonalKnowledgeWorkbench.mockResolvedValue(workbench);
    knowledgeApi.getKnowledgeBases.mockResolvedValue([
      {
        knowledge_base_id: KNOWLEDGE_BASE_ID,
        name: "合成知识库",
        description: null,
        default_visibility: "private",
        default_security_level: "INTERNAL",
        updated_at: "2026-08-25T08:00:00Z",
      },
    ]);
    knowledgeApi.recordPersonalWorkbenchDocumentAccess.mockResolvedValue(undefined);
    knowledgeApi.searchPublishedKnowledgeDocuments.mockResolvedValue(searchResponse);
    assistantApi.getAssistantConversations.mockResolvedValue([
      {
        conversation_id: CONVERSATION_ID,
        workspace_id: WORKSPACE_ID,
        created_by_account_id: "10000000-0000-4000-8000-000000000804",
        title: "合成问答会话",
        status: "active",
        created_at: "2026-08-25T08:00:00Z",
        updated_at: "2026-08-25T08:03:00Z",
        version: 1,
      },
    ]);
  });

  afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    vi.clearAllMocks();
  });

  it("加载期间显示骨架，失败后提供重试状态", async () => {
    knowledgeApi.getPersonalKnowledgeWorkbench.mockReturnValueOnce(new Promise(() => undefined));
    const loading = renderWorkbench();
    expect(loading.container.querySelector(".ant-skeleton")).toBeInTheDocument();
    loading.unmount();

    knowledgeApi.getPersonalKnowledgeWorkbench.mockRejectedValueOnce(
      new Error("synthetic workbench failure"),
    );
    renderWorkbench();
    expect(await screen.findByText("个人工作台未能加载")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新加载" })).toBeInTheDocument();
  });

  it("没有文档读取权限时不启动任何工作台查询", () => {
    authorization.permissionCodes = new Set();
    renderWorkbench();

    expect(screen.getByText("没有个人知识读取权限")).toBeInTheDocument();
    expect(knowledgeApi.getPersonalKnowledgeWorkbench).not.toHaveBeenCalled();
    expect(knowledgeApi.searchPublishedKnowledgeDocuments).not.toHaveBeenCalled();
  });

  it("空数据和无问答权限都有明确状态", async () => {
    authorization.permissionCodes.delete("assistant.conversation.read");
    knowledgeApi.getPersonalKnowledgeWorkbench.mockResolvedValueOnce({
      statistics: {
        knowledge_base_count: 0,
        document_count: 0,
        published_document_count: 0,
        favorite_document_count: 0,
        indexed_document_count: 0,
        pending_index_document_count: 0,
      },
      recent_documents: [],
      favorite_documents: [],
    });
    renderWorkbench();

    expect(await screen.findByText("暂无最近文档")).toBeInTheDocument();
    expect(screen.getByText("当前角色不能读取问答会话")).toBeInTheDocument();
    expect(assistantApi.getAssistantConversations).not.toHaveBeenCalled();
  });

  it("搜索筛选、部分索引提示和翻页使用同一服务端查询", async () => {
    renderWorkbench();
    await screen.findByText("合成问答会话");

    fireEvent.change(screen.getByLabelText("全局搜索已发布文档"), {
      target: { value: "合成制度" },
    });
    fireEvent.click(screen.getByText("内容"));
    fireEvent.mouseDown(screen.getByLabelText("按知识库筛选"));
    fireEvent.click(
      await screen.findByText("合成知识库", { selector: ".ant-select-item-option-content" }),
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "仅收藏" }));
    fireEvent.click(screen.getByRole("button", { name: /搜\s*索/ }));

    expect(await screen.findByText("2 份已发布文档的索引尚未就绪。")).toBeInTheDocument();
    expect(knowledgeApi.searchPublishedKnowledgeDocuments).toHaveBeenNthCalledWith(
      1,
      WORKSPACE_ID,
      {
        query: "合成制度",
        knowledgeBaseId: KNOWLEDGE_BASE_ID,
        matchType: "content",
        favoriteOnly: true,
        limit: 8,
        offset: 0,
      },
      expect.any(AbortSignal),
    );

    fireEvent.click(screen.getByTitle("2"));
    await waitFor(() =>
      expect(knowledgeApi.searchPublishedKnowledgeDocuments).toHaveBeenLastCalledWith(
        WORKSPACE_ID,
        expect.objectContaining({ offset: 8 }),
        expect.any(AbortSignal),
      ),
    );
  });

  it("正文字段受限时降级为标题结果，标题模式不显示无关警告", async () => {
    knowledgeApi.searchPublishedKnowledgeDocuments.mockResolvedValue({
      ...searchResponse,
      items: [],
      total: 0,
      content_search_available: false,
    });
    renderWorkbench();
    await screen.findByText("合成问答会话");
    fireEvent.change(screen.getByLabelText("全局搜索已发布文档"), {
      target: { value: "合成制度" },
    });
    fireEvent.click(screen.getByRole("button", { name: /搜\s*索/ }));
    expect(
      await screen.findByText("当前字段权限不允许搜索文档正文，结果仅包含标题命中。"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByText("标题"));
    fireEvent.click(screen.getByRole("button", { name: /搜\s*索/ }));
    await waitFor(() =>
      expect(knowledgeApi.searchPublishedKnowledgeDocuments).toHaveBeenLastCalledWith(
        WORKSPACE_ID,
        expect.objectContaining({ matchType: "title" }),
        expect.any(AbortSignal),
      ),
    );
    await waitFor(() =>
      expect(
        screen.queryByText("当前字段权限不允许搜索文档正文，结果仅包含标题命中。"),
      ).not.toBeInTheDocument(),
    );
  });

  it("文档访问通过服务端复核后跳转，问答只携带预填草稿", async () => {
    renderWorkbench();
    await screen.findByText("合成问答会话");
    fireEvent.change(screen.getByLabelText("全局搜索已发布文档"), {
      target: { value: "合成制度" },
    });
    fireEvent.click(screen.getByRole("button", { name: /搜\s*索/ }));
    await screen.findByText("这是只用于测试的合成正文命中片段。");

    fireEvent.click(screen.getByRole("button", { name: /打\s*开/ }));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        `/workspace/knowledge?base=${KNOWLEDGE_BASE_ID}&document=${DOCUMENT_ID}`,
      ),
    );
    expect(knowledgeApi.recordPersonalWorkbenchDocumentAccess).toHaveBeenCalledWith(
      WORKSPACE_ID,
      DOCUMENT_ID,
    );

    fireEvent.click(screen.getByRole("button", { name: "进入问答" }));
    expect(screen.getByTestId("location").textContent).toContain("/workspace/assistant?prompt=");
    expect(decodeURIComponent(screen.getByTestId("location").textContent ?? "")).toContain(
      "请根据《合成制度手册》回答我的问题：",
    );
  });

  it("快捷入口复用现有流程，问答编辑器只预填不自动发送", async () => {
    const onSend = vi.fn();
    const page = renderWorkbench();
    await screen.findByText("合成问答会话");

    fireEvent.click(screen.getByRole("button", { name: "上传文件" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/workspace/knowledge?action=upload");
    fireEvent.click(screen.getByRole("button", { name: "新建文件夹" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/workspace/knowledge?action=folder");
    page.unmount();

    render(
      <AssistantComposer
        initialValue="请根据《合成制度手册》回答我的问题："
        disabled={false}
        sending={false}
        cancellable={false}
        onSend={onSend}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("输入知识问答问题")).toHaveValue(
      "请根据《合成制度手册》回答我的问题：",
    );
    expect(onSend).not.toHaveBeenCalled();
  });
});
