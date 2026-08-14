/** @description P1E-06 问答页恢复、取消、来源和反馈状态编排测试。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  cancelAssistantRun: vi.fn(),
  createAssistantConversation: vi.fn(),
  createAssistantMessage: vi.fn(),
  getAssistantConversations: vi.fn(),
  getAssistantFeedback: vi.fn(),
  getAssistantMessages: vi.fn(),
  getAssistantRuns: vi.fn(),
  getAssistantSources: vi.fn(),
  submitAssistantFeedback: vi.fn(),
}));
const streamApi = vi.hoisted(() => ({ streamAssistantRun: vi.fn() }));

vi.mock("@/api/services/assistant", () => api);
vi.mock("@/api/assistantSse", () => ({
  AssistantSseProtocolError: class AssistantSseProtocolError extends Error {},
  streamAssistantRun: streamApi.streamAssistantRun,
}));
vi.mock("@/hooks/useCurrentWorkspace", () => ({
  useCurrentWorkspace: () => ({ workspaceId: "20000000-0000-4000-8000-000000000806" }),
}));

import type { AssistantStreamEvent } from "@/api/assistantSse";
import type {
  AssistantConversation,
  AssistantMessage,
  AssistantRun,
  MessageFeedback,
} from "@/api/services/assistant";

import { useAssistantConversation } from "./useAssistantConversation";

const WORKSPACE_ID = "20000000-0000-4000-8000-000000000806";
const CONVERSATION_ID = "30000000-0000-4000-8000-000000000806";
const RUN_ID = "40000000-0000-4000-8000-000000000806";
const USER_MESSAGE_ID = "50000000-0000-4000-8000-000000000806";
const ASSISTANT_MESSAGE_ID = "51000000-0000-4000-8000-000000000806";

const conversation: AssistantConversation = {
  conversation_id: CONVERSATION_ID,
  workspace_id: WORKSPACE_ID,
  created_by_account_id: "10000000-0000-4000-8000-000000000806",
  title: "合成恢复会话",
  status: "active",
  created_at: "2026-08-15T12:00:00Z",
  updated_at: "2026-08-15T12:00:00Z",
  version: 1,
};
const activeRun: AssistantRun = {
  run_id: RUN_ID,
  workspace_id: WORKSPACE_ID,
  conversation_id: CONVERSATION_ID,
  user_message_id: USER_MESSAGE_ID,
  assistant_message_id: ASSISTANT_MESSAGE_ID,
  agent_release_id: "60000000-0000-4000-8000-000000000806",
  runtime_config_version_id: "70000000-0000-4000-8000-000000000806",
  status: "running",
  trace_id: "a".repeat(32),
  created_at: "2026-08-15T12:00:00Z",
  updated_at: "2026-08-15T12:00:01Z",
  completed_at: null,
  error_code: null,
};
const streamingMessage: AssistantMessage = {
  message_id: ASSISTANT_MESSAGE_ID,
  workspace_id: WORKSPACE_ID,
  conversation_id: CONVERSATION_ID,
  role: "assistant",
  status: "streaming",
  parts: [],
  created_by_account_id: conversation.created_by_account_id,
  created_at: "2026-08-15T12:00:00Z",
  updated_at: "2026-08-15T12:00:01Z",
  version: 1,
};

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

function queryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
}

function completedEvent(): AssistantStreamEvent {
  return {
    eventId: "80000000-0000-4000-8000-000000000806",
    eventType: "message.snapshot",
    sequenceNo: 3,
    payload: { text: "合成恢复答案", citations: [] },
    persisted: false,
    raw: {},
  };
}

function prepareQueries(runs: AssistantRun[] = []) {
  api.getAssistantConversations.mockResolvedValue([conversation]);
  api.getAssistantMessages.mockResolvedValue([streamingMessage]);
  api.getAssistantRuns.mockResolvedValue(runs);
  api.getAssistantSources.mockResolvedValue([]);
  api.getAssistantFeedback.mockResolvedValue(null);
}

describe("P1E-06 问答页状态编排", () => {
  afterEach(() => vi.clearAllMocks());

  it("刷新后从活动 Run 恢复 SSE 并刷新消息事实", async () => {
    prepareQueries([activeRun]);
    streamApi.streamAssistantRun.mockImplementation(async function* () {
      yield completedEvent();
    });
    const client = queryClient();
    const view = renderHook(() => useAssistantConversation(), {
      wrapper: createWrapper(client),
    });

    await waitFor(() => expect(view.result.current.stream.status).toBe("completed"));

    expect(streamApi.streamAssistantRun).toHaveBeenCalledWith(
      WORKSPACE_ID,
      CONVERSATION_ID,
      RUN_ID,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(view.result.current.stream.text).toBe("合成恢复答案");
    expect(api.getAssistantMessages.mock.calls.length).toBeGreaterThanOrEqual(2);
    view.unmount();
    client.clear();
  });

  it("取消活动 Run 后收敛临时状态并刷新服务端事实", async () => {
    prepareQueries([activeRun]);
    streamApi.streamAssistantRun.mockImplementation(async function* () {
      yield {
        ...completedEvent(),
        eventType: "tool.status",
        payload: { stage: "retrieval" },
        persisted: true,
      };
    });
    api.cancelAssistantRun.mockResolvedValue({
      ...activeRun,
      status: "cancelled",
      completed_at: "2026-08-15T12:00:02Z",
      error_code: "RUN_CANCELLED",
    });
    const client = queryClient();
    const view = renderHook(() => useAssistantConversation(), {
      wrapper: createWrapper(client),
    });
    await waitFor(() => expect(view.result.current.activeRun?.run_id).toBe(RUN_ID));

    act(() => view.result.current.cancelRun.mutate(activeRun));
    await waitFor(() => expect(view.result.current.stream.status).toBe("cancelled"));

    expect(api.cancelAssistantRun).toHaveBeenCalledWith(WORKSPACE_ID, CONVERSATION_ID, RUN_ID);
    expect(view.result.current.stream.errorCode).toBe("RUN_CANCELLED");
    view.unmount();
    client.clear();
  });

  it("来源与反馈始终按当前会话和消息标识查询", async () => {
    // 1. 先建立已完成消息的查询状态，再分别打开来源和反馈面板。
    prepareQueries();
    const feedback: MessageFeedback = {
      feedback_id: "90000000-0000-4000-8000-000000000806",
      workspace_id: WORKSPACE_ID,
      conversation_id: CONVERSATION_ID,
      message_id: ASSISTANT_MESSAGE_ID,
      run_id: RUN_ID,
      rating: "helpful",
      issue_codes: [],
      comment: null,
      created_at: "2026-08-15T12:00:03Z",
      updated_at: "2026-08-15T12:00:03Z",
      version: 1,
    };
    api.submitAssistantFeedback.mockResolvedValue(feedback);
    const client = queryClient();
    const view = renderHook(() => useAssistantConversation(), {
      wrapper: createWrapper(client),
    });
    await waitFor(() => expect(view.result.current.selectedConversationId).toBe(CONVERSATION_ID));

    // 2. 打开来源和反馈并提交选择，随后核对边界标识没有沿用其他会话状态。
    act(() => {
      view.result.current.openSources(ASSISTANT_MESSAGE_ID);
      view.result.current.openFeedback(ASSISTANT_MESSAGE_ID);
    });
    await waitFor(() => expect(api.getAssistantSources).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.getAssistantFeedback).toHaveBeenCalledTimes(1));
    act(() => {
      view.result.current.submitFeedback.mutate({
        messageId: ASSISTANT_MESSAGE_ID,
        body: { rating: "helpful", issue_codes: [] },
      });
    });
    await waitFor(() => expect(api.submitAssistantFeedback).toHaveBeenCalledTimes(1));

    expect(api.getAssistantSources).toHaveBeenCalledWith(
      WORKSPACE_ID,
      CONVERSATION_ID,
      ASSISTANT_MESSAGE_ID,
      expect.any(AbortSignal),
    );
    expect(api.submitAssistantFeedback).toHaveBeenCalledWith(
      WORKSPACE_ID,
      CONVERSATION_ID,
      ASSISTANT_MESSAGE_ID,
      { rating: "helpful", issue_codes: [] },
    );
    view.unmount();
    client.clear();
  });
});
