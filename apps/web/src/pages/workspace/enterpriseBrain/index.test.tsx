/** @description P6B-06 企业大脑页面状态、会话、问答流、报告和权限边界测试。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App as AntdApp } from "antd";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PlatformApiError } from "@/api/client";
import type { AssistantMessage, AssistantRun } from "@/api/services/assistant";
import type { EnterpriseBrainReport } from "@/api/services/enterpriseBrain";

const workspaceHarness = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
const menuHarness = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
const api = vi.hoisted(() => ({
  archiveEnterpriseBrainConversation: vi.fn(),
  cancelEnterpriseBrainRun: vi.fn(),
  createEnterpriseBrainConversation: vi.fn(),
  createEnterpriseBrainMessage: vi.fn(),
  createEnterpriseBrainReport: vi.fn(),
  downloadEnterpriseBrainReport: vi.fn(),
  getEnterpriseBrainConversations: vi.fn(),
  getEnterpriseBrainFeedback: vi.fn(),
  getEnterpriseBrainMessages: vi.fn(),
  getEnterpriseBrainOverview: vi.fn(),
  getEnterpriseBrainReport: vi.fn(),
  getEnterpriseBrainReports: vi.fn(),
  getEnterpriseBrainRuns: vi.fn(),
  getEnterpriseBrainSources: vi.fn(),
  streamEnterpriseBrainRun: vi.fn(),
  submitEnterpriseBrainFeedback: vi.fn(),
}));
const knowledgeApi = vi.hoisted(() => ({ getEnterpriseKnowledgePortal: vi.fn() }));

vi.mock("@/hooks/useCurrentWorkspace", () => ({
  useCurrentWorkspace: () => workspaceHarness.value,
}));
vi.mock("@/hooks/useWorkspaceMenuNavigation", () => ({
  useWorkspaceMenuNavigation: () => menuHarness.value,
}));
vi.mock("@/api/services/enterpriseBrain", () => api);
vi.mock("@/api/services/enterpriseKnowledge", () => knowledgeApi);
vi.mock("@/pages/workspace/assistant/components/MessageThread", () => ({
  MessageThread: ({
    messages,
    onSources,
    onFeedback,
  }: {
    messages: ReadonlyArray<AssistantMessage>;
    onSources: (messageId: string) => void;
    onFeedback: (messageId: string, rating: "helpful" | "unhelpful") => void;
  }) => (
    <div>
      {messages.map((message) => (
        <div key={message.message_id}>{message.parts.map((part) => part.text).join("")}</div>
      ))}
      <button type="button" onClick={() => onSources(ASSISTANT_MESSAGE_ID)}>
        查看来源
      </button>
      <button type="button" onClick={() => onFeedback(ASSISTANT_MESSAGE_ID, "helpful")}>
        回答有帮助
      </button>
    </div>
  ),
}));
vi.mock("@/pages/workspace/assistant/components/AssistantSourcesDrawer", () => ({
  AssistantSourcesDrawer: ({
    open,
    items,
  }: {
    open: boolean;
    items: ReadonlyArray<{ quote: string }>;
  }) => (open ? <div role="dialog">{items.map((item) => item.quote)}</div> : null),
}));
vi.mock("@/pages/workspace/assistant/components/AssistantFeedbackModal", () => ({
  AssistantFeedbackModal: () => null,
}));

import EnterpriseBrainPage from "./index";

const WORKSPACE_ID = "20000000-0000-4000-8000-000000000906";
const DOMAIN_ID = "40000000-0000-4000-8000-000000000906";
const ACTIVE_CONVERSATION_ID = "30000000-0000-4000-8000-000000000906";
const ARCHIVED_CONVERSATION_ID = "30000000-0000-4000-8000-000000000907";
const RUN_ID = "50000000-0000-4000-8000-000000000906";
const USER_MESSAGE_ID = "51000000-0000-4000-8000-000000000906";
const ASSISTANT_MESSAGE_ID = "52000000-0000-4000-8000-000000000906";
const REPORT_ID = "60000000-0000-4000-8000-000000000906";

const overview = {
  workspace_id: WORKSPACE_ID,
  workspace_name: "合成企业大脑",
  generated_at: "2026-09-04T08:00:00Z",
  window_started_at: "2026-08-05T08:00:00Z",
  window_ended_at: "2026-09-04T08:00:00Z",
  active_conversation_count: 1,
  archived_conversation_count: 1,
  run_count_30d: 3,
  completed_run_count_30d: 2,
  failed_run_count_30d: 1,
  cancelled_run_count_30d: 0,
  token_count_30d: 1200,
  estimated_cost_microunits_30d: 300,
  knowledge_domain_ids: [DOMAIN_ID],
};
const activeConversation = {
  conversation_id: ACTIVE_CONVERSATION_ID,
  workspace_id: WORKSPACE_ID,
  created_by_account_id: "70000000-0000-4000-8000-000000000906",
  knowledge_domain_id: DOMAIN_ID,
  knowledge_domain_policy_version: 4,
  title: "活动企业问答",
  status: "active" as const,
  created_at: "2026-09-04T07:00:00Z",
  updated_at: "2026-09-04T07:30:00Z",
  version: 1,
};
const archivedConversation = {
  ...activeConversation,
  conversation_id: ARCHIVED_CONVERSATION_ID,
  title: "已归档企业问答",
  status: "archived" as const,
};
const run = {
  run_id: RUN_ID,
  workspace_id: WORKSPACE_ID,
  conversation_id: ACTIVE_CONVERSATION_ID,
  user_message_id: USER_MESSAGE_ID,
  assistant_message_id: ASSISTANT_MESSAGE_ID,
  agent_release_id: "80000000-0000-4000-8000-000000000906",
  runtime_config_version_id: "81000000-0000-4000-8000-000000000906",
  knowledge_base_ids: ["90000000-0000-4000-8000-000000000906"],
  document_ids: null,
  attachment_ids: [],
  status: "queued" as const,
  trace_id: "a".repeat(32),
  created_at: "2026-09-04T07:40:00Z",
  updated_at: "2026-09-04T07:40:00Z",
  completed_at: null,
  error_code: null,
} satisfies AssistantRun;
const messages = [
  {
    message_id: USER_MESSAGE_ID,
    workspace_id: WORKSPACE_ID,
    conversation_id: ACTIVE_CONVERSATION_ID,
    role: "user" as const,
    status: "completed" as const,
    parts: [{ part_id: "p1", sequence_no: 1, type: "text" as const, text: "合成问题" }],
    created_by_account_id: activeConversation.created_by_account_id,
    created_at: "2026-09-04T07:40:00Z",
    updated_at: "2026-09-04T07:40:00Z",
    version: 1,
  },
  {
    message_id: ASSISTANT_MESSAGE_ID,
    workspace_id: WORKSPACE_ID,
    conversation_id: ACTIVE_CONVERSATION_ID,
    role: "assistant" as const,
    status: "completed" as const,
    parts: [{ part_id: "p2", sequence_no: 1, type: "text" as const, text: "合成答案" }],
    created_by_account_id: activeConversation.created_by_account_id,
    created_at: "2026-09-04T07:40:00Z",
    updated_at: "2026-09-04T07:41:00Z",
    version: 1,
  },
] satisfies ReadonlyArray<AssistantMessage>;
const report = {
  report_id: REPORT_ID,
  workspace_id: WORKSPACE_ID,
  created_by_account_id: activeConversation.created_by_account_id,
  conversation_id: ACTIVE_CONVERSATION_ID,
  message_id: ASSISTANT_MESSAGE_ID,
  run_id: RUN_ID,
  template: "briefing" as const,
  title: "合成报告",
  content: "# 合成报告\n\n合成正文",
  content_sha256: "b".repeat(64),
  citation_count: 1,
  idempotency_key: "synthetic-report",
  created_at: "2026-09-04T07:50:00Z",
} satisfies EnterpriseBrainReport;

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntdApp>
        <EnterpriseBrainPage />
      </AntdApp>
    </QueryClientProvider>,
  );
}

describe("P6B-06 AI 企业大脑页面", () => {
  beforeEach(() => {
    // 1. 先建立活动企业空间与完整菜单权限，作为各交互场景的可信默认上下文。
    workspaceHarness.value = {
      workspaceId: WORKSPACE_ID,
      currentWorkspace: { workspace_id: WORKSPACE_ID, workspace_type: "enterprise" },
      workspaces: { isLoading: false, isError: false, error: null },
    };
    menuHarness.value = {
      isLoading: false,
      error: null,
      visiblePermissionCodes: new Set([
        "enterprise.brain.read",
        "enterprise.brain.conversation.create",
        "enterprise.brain.message.create",
        "enterprise.brain.conversation.archive",
        "enterprise.brain.run.cancel",
        "enterprise.brain.source.read",
        "enterprise.brain.feedback.manage",
        "enterprise.brain.report.create",
        "enterprise.brain.report.read",
      ]),
    };
    // 2. 再注入只含合成数据的查询结果，覆盖会话、知识域、消息、报告与来源。
    api.getEnterpriseBrainOverview.mockResolvedValue(overview);
    api.getEnterpriseBrainConversations.mockResolvedValue([
      activeConversation,
      archivedConversation,
    ]);
    api.getEnterpriseBrainMessages.mockResolvedValue(messages);
    api.getEnterpriseBrainRuns.mockResolvedValue([]);
    api.getEnterpriseBrainReports.mockResolvedValue([report]);
    api.getEnterpriseBrainSources.mockResolvedValue([{ quote: "合成来源" }]);
    api.getEnterpriseBrainFeedback.mockResolvedValue(null);
    knowledgeApi.getEnterpriseKnowledgePortal.mockResolvedValue({
      workspace_id: WORKSPACE_ID,
      workspace_name: "合成企业大脑",
      statistics: {
        active_categories: 0,
        active_domains: 1,
        classified_documents: 0,
        governed_knowledge_bases: 1,
      },
      categories: [],
      domains: [
        {
          domain_id: DOMAIN_ID,
          name: "合成研发知识域",
          description: "合成验收",
          member_ids: [],
          department_ids: [],
          knowledge_base_ids: [],
          rag_policy: { policy_version: 4, mode: "balanced", top_k: 8, minimum_score: 0.2 },
          status: "active",
          created_at: "2026-09-04T07:00:00Z",
          updated_at: "2026-09-04T07:00:00Z",
          version: 1,
        },
      ],
      documents: [],
      knowledge_bases: [],
      departments: [],
      members: [],
      generated_at: "2026-09-04T08:00:00Z",
    });
    // 3. 最后配置写操作和 SSE 终态，使每个用例可以按需覆盖单一分支而不调用外部服务。
    api.createEnterpriseBrainConversation.mockResolvedValue(activeConversation);
    api.createEnterpriseBrainMessage.mockResolvedValue({ message: messages[0], run });
    api.archiveEnterpriseBrainConversation.mockResolvedValue(archivedConversation);
    api.cancelEnterpriseBrainRun.mockResolvedValue({
      ...run,
      status: "cancelled",
      error_code: "RUN_CANCELLED",
    });
    api.submitEnterpriseBrainFeedback.mockResolvedValue({
      feedback_id: "a0000000-0000-4000-8000-000000000906",
      workspace_id: WORKSPACE_ID,
      conversation_id: ACTIVE_CONVERSATION_ID,
      message_id: ASSISTANT_MESSAGE_ID,
      run_id: RUN_ID,
      rating: "helpful",
      issue_codes: [],
      comment: null,
      created_at: "2026-09-04T08:00:00Z",
      updated_at: "2026-09-04T08:00:00Z",
      version: 1,
    });
    api.createEnterpriseBrainReport.mockResolvedValue(report);
    api.getEnterpriseBrainReport.mockResolvedValue(report);
    api.downloadEnterpriseBrainReport.mockResolvedValue({
      blob: new Blob([report.content], { type: "text/markdown" }),
      fileName: "合成报告",
    });
    api.streamEnterpriseBrainRun.mockImplementation(async function* () {
      yield {
        eventId: "b0000000-0000-4000-8000-000000000906",
        eventType: "message.snapshot",
        sequenceNo: 1,
        payload: { text: "合成流式答案", citations: [] },
        persisted: false,
        raw: {},
      };
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("区分加载、个人空间、菜单拒绝和服务端拒绝", async () => {
    workspaceHarness.value = {
      ...workspaceHarness.value,
      workspaces: { isLoading: true, isError: false, error: null },
    };
    const loading = renderPage();
    expect(loading.container.querySelector(".ant-skeleton")).toBeInTheDocument();
    loading.unmount();

    workspaceHarness.value = {
      ...workspaceHarness.value,
      currentWorkspace: { workspace_id: WORKSPACE_ID, workspace_type: "personal" },
      workspaces: { isLoading: false, isError: false, error: null },
    };
    const personal = renderPage();
    expect(screen.getByText("个人空间不启用企业大脑")).toBeInTheDocument();
    personal.unmount();

    workspaceHarness.value = {
      ...workspaceHarness.value,
      currentWorkspace: { workspace_id: WORKSPACE_ID, workspace_type: "enterprise" },
    };
    menuHarness.value = { ...menuHarness.value, visiblePermissionCodes: new Set<string>() };
    const denied = renderPage();
    expect(screen.getByText("当前账号没有企业大脑权限")).toBeInTheDocument();
    denied.unmount();

    menuHarness.value = {
      ...menuHarness.value,
      visiblePermissionCodes: new Set(["enterprise.brain.read"]),
    };
    api.getEnterpriseBrainOverview.mockRejectedValueOnce(
      new PlatformApiError(403, "POLICY_DENIED", "拒绝", false),
    );
    renderPage();
    expect(await screen.findByText("当前账号没有企业大脑权限")).toBeInTheDocument();
  });

  it("展示统计、知识域、活动与归档筛选，并只允许从企业知识域创建会话", async () => {
    renderPage();
    expect(await screen.findByText("AI 企业大脑")).toBeInTheDocument();
    expect(screen.getByText(/在 合成企业大脑 的获权知识域内提问/)).toBeInTheDocument();
    expect(screen.getByText("活动企业问答")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();

    fireEvent.mouseDown(screen.getAllByRole("combobox")[0]);
    const domainOptions = await screen.findAllByText(/合成研发知识域/);
    fireEvent.click(domainOptions.at(-1)!);
    fireEvent.click(screen.getByRole("button", { name: "新建会话" }));
    await waitFor(() =>
      expect(api.createEnterpriseBrainConversation).toHaveBeenCalledWith(WORKSPACE_ID, DOMAIN_ID),
    );

    fireEvent.click(screen.getByText("已归档"));
    expect(screen.getByText("已归档企业问答")).toBeInTheDocument();
    expect(screen.queryByText("活动企业问答")).not.toBeInTheDocument();
  });

  it("快捷任务只预填问题，发送后调用企业独立 Run 流", async () => {
    renderPage();
    await screen.findByText("合成答案");
    fireEvent.mouseDown(screen.getAllByRole("combobox")[1]);
    fireEvent.click(await screen.findByText("总结要点"));
    const input = screen.getByPlaceholderText("询问企业制度、风险或差异；Enter 发送");
    expect(input).toHaveValue("请基于当前企业知识域总结核心要点，并按重要性排序。");
    expect(api.createEnterpriseBrainMessage).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() =>
      expect(api.createEnterpriseBrainMessage).toHaveBeenCalledWith(
        WORKSPACE_ID,
        ACTIVE_CONVERSATION_ID,
        "请基于当前企业知识域总结核心要点，并按重要性排序。",
        expect.stringContaining("enterprise-brain-"),
      ),
    );
    await waitFor(() =>
      expect(api.streamEnterpriseBrainRun).toHaveBeenCalledWith(
        WORKSPACE_ID,
        ACTIVE_CONVERSATION_ID,
        RUN_ID,
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
  });

  it("归档会话、来源、反馈和报告操作均按当前会话边界执行", async () => {
    renderPage();
    await screen.findByText("合成答案");

    fireEvent.click(screen.getByRole("button", { name: "归档会话 活动企业问答" }));
    await screen.findByText("归档这个会话？");
    const confirmButton = document.querySelector(".ant-popconfirm .ant-btn-primary");
    expect(confirmButton).not.toBeNull();
    fireEvent.click(confirmButton!);
    await waitFor(() =>
      expect(api.archiveEnterpriseBrainConversation).toHaveBeenCalledWith(
        WORKSPACE_ID,
        ACTIVE_CONVERSATION_ID,
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "查看来源" }));
    await waitFor(() =>
      expect(api.getEnterpriseBrainSources).toHaveBeenCalledWith(
        WORKSPACE_ID,
        ACTIVE_CONVERSATION_ID,
        ASSISTANT_MESSAGE_ID,
        expect.any(AbortSignal),
      ),
    );
    expect(await screen.findByRole("dialog")).toHaveTextContent("合成来源");

    fireEvent.click(screen.getByRole("button", { name: "回答有帮助" }));
    await waitFor(() =>
      expect(api.submitEnterpriseBrainFeedback).toHaveBeenCalledWith(
        WORKSPACE_ID,
        ACTIVE_CONVERSATION_ID,
        ASSISTANT_MESSAGE_ID,
        { rating: "helpful", issue_codes: [] },
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "从此回答生成报告" }));
    fireEvent.click(screen.getByRole("button", { name: "生成报告" }));
    await waitFor(() =>
      expect(api.createEnterpriseBrainReport).toHaveBeenCalledWith(
        WORKSPACE_ID,
        { message_id: ASSISTANT_MESSAGE_ID, template: "briefing", title: "企业问答报告" },
        expect.stringContaining("enterprise-brain-report-"),
      ),
    );
  });

  it("报告查看与下载只使用报告标识、Blob 和安全文件名", async () => {
    const createObjectURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:synthetic");
    const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    renderPage();
    await screen.findByText("合成报告");
    fireEvent.click(screen.getByRole("button", { name: "查看" }));
    await waitFor(() =>
      expect(api.getEnterpriseBrainReport).toHaveBeenCalledWith(WORKSPACE_ID, REPORT_ID),
    );
    await waitFor(() => expect(screen.getByText(/合成正文/)).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole("button", { name: "下载" })[0]);
    await waitFor(() =>
      expect(api.downloadEnterpriseBrainReport).toHaveBeenCalledWith(WORKSPACE_ID, REPORT_ID),
    );
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:synthetic");
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(document.body.textContent).not.toContain("object-key");
  });

  it("无可访问知识域时明确失败关闭且不显示创建入口", async () => {
    knowledgeApi.getEnterpriseKnowledgePortal.mockResolvedValueOnce({
      workspace_id: WORKSPACE_ID,
      workspace_name: "合成企业大脑",
      statistics: {
        active_categories: 0,
        active_domains: 0,
        classified_documents: 0,
        governed_knowledge_bases: 0,
      },
      categories: [],
      domains: [],
      documents: [],
      knowledge_bases: [],
      departments: [],
      members: [],
      generated_at: "2026-09-04T08:00:00Z",
    });
    renderPage();
    expect(
      await screen.findByText("暂无可访问知识域；请联系企业管理员配置范围。"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "新建会话" })).not.toBeInTheDocument();
  });
});
