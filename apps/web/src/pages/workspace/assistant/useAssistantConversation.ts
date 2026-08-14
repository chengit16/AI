/** @description 问答页状态编排，集中管理会话、消息、Run 恢复和来源/反馈查询。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { errorMessage, PlatformApiError } from "@/api/client";
import {
  AssistantSseProtocolError,
  streamAssistantRun,
  type AssistantStreamEvent,
} from "@/api/assistantSse";
import {
  cancelAssistantRun,
  createAssistantConversation,
  createAssistantMessage,
  getAssistantConversations,
  getAssistantFeedback,
  getAssistantMessages,
  getAssistantRuns,
  getAssistantSources,
  submitAssistantFeedback,
  type AssistantConversation,
  type AssistantRun,
  type FeedbackRequest,
} from "@/api/services/assistant";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";

/** 描述当前浏览器对一个助手 Run 的临时消费状态。 */
export type AssistantStreamStatus =
  "idle" | "connecting" | "streaming" | "completed" | "failed" | "cancelled" | "error";

/** 保存刷新前可丢弃的 SSE 展示状态，服务端消息和 Run 始终是恢复事实。 */
export interface AssistantStreamState {
  /** 当前消费的 Run；空值表示没有活动流。 */
  runId: string | null;
  /** 与活动 Run 绑定的助手占位消息。 */
  assistantMessageId: string | null;
  /** 当前连接、生成或终态。 */
  status: AssistantStreamStatus;
  /** 最近收到的工具阶段。 */
  stage: string | null;
  /** 由已校验增量拼接或终态快照覆盖的临时正文。 */
  text: string;
  /** 当前流中收到的引用摘要；来源详情仍需单独授权查询。 */
  citations: Array<Record<string, unknown>>;
  /** 面向页面的稳定错误码，不保存原始异常正文。 */
  errorCode: string | null;
}

const EMPTY_STREAM: AssistantStreamState = {
  runId: null,
  assistantMessageId: null,
  status: "idle",
  stage: null,
  text: "",
  citations: [],
  errorCode: null,
};

function nextStreamState(
  current: AssistantStreamState,
  event: AssistantStreamEvent,
): AssistantStreamState {
  const payload = event.payload;
  // 1. 进度、正文增量和引用分别合并，任何缺失字段都保持上一份可信状态。
  if (event.eventType === "tool.status") {
    return {
      ...current,
      status: "streaming",
      stage: typeof payload.stage === "string" ? payload.stage : current.stage,
    };
  }
  if (event.eventType === "message.delta") {
    return {
      ...current,
      status: "streaming",
      text: current.text + (typeof payload.delta === "string" ? payload.delta : ""),
    };
  }
  if (event.eventType === "message.citation") {
    return {
      ...current,
      citations: Array.isArray(payload.citations)
        ? (payload.citations as Array<Record<string, unknown>>)
        : current.citations,
    };
  }

  // 2. 终态事件覆盖临时正文；失败只保存稳定错误码，不展示原始异常。
  if (event.eventType === "message.completed" || event.eventType === "message.snapshot") {
    return {
      ...current,
      status: "completed",
      text: typeof payload.text === "string" ? payload.text : current.text,
      citations: Array.isArray(payload.citations)
        ? (payload.citations as Array<Record<string, unknown>>)
        : current.citations,
    };
  }
  if (event.eventType === "message.failed") {
    return {
      ...current,
      status: "failed",
      errorCode: typeof payload.error_code === "string" ? payload.error_code : "RUN_FAILED",
    };
  }
  return current;
}

/**
 * 问答页唯一状态入口。
 *
 * 服务端消息和 Run 是恢复事实；`stream` 只保存当前浏览器尚未收到终态消息的临时展示，
 * 因此刷新或断线后仍以接口返回的不可变消息重新建立视图。
 */
export function useAssistantConversation() {
  // 1. 先建立工作空间、会话选择和服务端恢复事实查询，默认选择由查询结果派生。
  const queryClient = useQueryClient();
  const { workspaceId } = useCurrentWorkspace();
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [pendingRun, setPendingRun] = useState<AssistantRun | null>(null);
  const [stream, setStream] = useState<AssistantStreamState>(EMPTY_STREAM);
  const [sourceMessageId, setSourceMessageId] = useState<string | null>(null);
  const [feedbackMessageId, setFeedbackMessageId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const consumedRunRef = useRef<string | null>(null);

  const conversations = useQuery({
    queryKey: ["assistant-conversations", workspaceId],
    queryFn: ({ signal }) => getAssistantConversations(workspaceId!, signal),
    enabled: Boolean(workspaceId),
    retry: false,
  });
  const effectiveConversationId = useMemo(() => {
    const selectedStillExists = conversations.data?.some(
      (item) => item.conversation_id === selectedConversationId,
    );
    return selectedStillExists
      ? selectedConversationId
      : (conversations.data?.[0]?.conversation_id ?? null);
  }, [conversations.data, selectedConversationId]);
  const selectedConversation = useMemo<AssistantConversation | null>(
    () =>
      conversations.data?.find((item) => item.conversation_id === effectiveConversationId) ?? null,
    [conversations.data, effectiveConversationId],
  );

  const messages = useQuery({
    queryKey: ["assistant-messages", workspaceId, effectiveConversationId],
    queryFn: ({ signal }) => getAssistantMessages(workspaceId!, effectiveConversationId!, signal),
    enabled: Boolean(workspaceId && effectiveConversationId),
    retry: false,
  });
  const runs = useQuery({
    queryKey: ["assistant-runs", workspaceId, effectiveConversationId],
    queryFn: ({ signal }) => getAssistantRuns(workspaceId!, effectiveConversationId!, signal),
    enabled: Boolean(workspaceId && effectiveConversationId),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.some((run) => ["queued", "running"].includes(run.status)) ? 3_000 : false,
  });

  const activeRun = useMemo(
    () =>
      pendingRun ?? runs.data?.find((run) => ["queued", "running"].includes(run.status)) ?? null,
    [pendingRun, runs.data],
  );

  const invalidateConversation = useCallback(
    async (conversationId: string) => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["assistant-messages", workspaceId, conversationId],
        }),
        queryClient.invalidateQueries({
          queryKey: ["assistant-runs", workspaceId, conversationId],
        }),
        queryClient.invalidateQueries({ queryKey: ["assistant-conversations", workspaceId] }),
      ]);
    },
    [queryClient, workspaceId],
  );

  // 2. 写操作完成后只失效相关查询，临时流状态不写入 TanStack Query 缓存。
  const createConversation = useMutation({
    mutationFn: (title?: string) => createAssistantConversation(workspaceId!, title),
    onSuccess: async (conversation) => {
      setSelectedConversationId(conversation.conversation_id);
      await queryClient.invalidateQueries({ queryKey: ["assistant-conversations", workspaceId] });
    },
  });
  const createMessage = useMutation({
    mutationFn: ({ text, conversationId }: { text: string; conversationId: string }) =>
      createAssistantMessage(
        workspaceId!,
        conversationId,
        [text],
        `assistant-${crypto.randomUUID()}`,
      ),
    onSuccess: async (submission) => {
      setPendingRun(submission.run);
      setStream({
        ...EMPTY_STREAM,
        runId: submission.run.run_id,
        assistantMessageId: submission.run.assistant_message_id,
        status: "connecting",
      });
      await invalidateConversation(submission.run.conversation_id);
    },
  });
  const cancelRun = useMutation({
    mutationFn: (run: AssistantRun) =>
      cancelAssistantRun(workspaceId!, run.conversation_id, run.run_id),
    onSuccess: async (run) => {
      abortRef.current?.abort();
      setPendingRun(null);
      setStream((current) => ({ ...current, status: "cancelled", errorCode: run.error_code }));
      await invalidateConversation(run.conversation_id);
    },
  });

  const sources = useQuery({
    queryKey: ["assistant-sources", workspaceId, effectiveConversationId, sourceMessageId],
    queryFn: ({ signal }) =>
      getAssistantSources(workspaceId!, effectiveConversationId!, sourceMessageId!, signal),
    enabled: Boolean(workspaceId && effectiveConversationId && sourceMessageId),
    retry: false,
  });
  const feedback = useQuery({
    queryKey: ["assistant-feedback", workspaceId, effectiveConversationId, feedbackMessageId],
    queryFn: ({ signal }) =>
      getAssistantFeedback(workspaceId!, effectiveConversationId!, feedbackMessageId!, signal),
    enabled: Boolean(workspaceId && effectiveConversationId && feedbackMessageId),
    retry: false,
  });
  const submitFeedback = useMutation({
    mutationFn: ({ messageId, body }: { messageId: string; body: FeedbackRequest }) =>
      submitAssistantFeedback(workspaceId!, effectiveConversationId!, messageId, body),
    onSuccess: async (value) => {
      await queryClient.invalidateQueries({
        queryKey: ["assistant-feedback", workspaceId, effectiveConversationId, value.message_id],
      });
    },
  });

  // 3. SSE 生命周期仅依赖稳定标识，轮询产生的新对象不会误触发清理并中断当前连接。
  const activeRunId = activeRun?.run_id ?? null;
  const activeRunConversationId = activeRun?.conversation_id ?? null;
  const activeRunAssistantMessageId = activeRun?.assistant_message_id ?? null;
  useEffect(() => {
    if (
      !activeRunId ||
      !activeRunConversationId ||
      !workspaceId ||
      !effectiveConversationId ||
      consumedRunRef.current === activeRunId
    ) {
      return;
    }
    consumedRunRef.current = activeRunId;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    void (async () => {
      try {
        for await (const event of streamAssistantRun(
          workspaceId,
          activeRunConversationId,
          activeRunId,
          { signal: controller.signal },
        )) {
          setStream((current) =>
            nextStreamState(
              {
                ...current,
                runId: activeRunId,
                assistantMessageId: activeRunAssistantMessageId,
                errorCode: null,
              },
              event,
            ),
          );
        }
        await invalidateConversation(activeRunConversationId);
        setPendingRun(null);
      } catch (error) {
        if (controller.signal.aborted) return;
        const code =
          error instanceof PlatformApiError
            ? error.code
            : error instanceof AssistantSseProtocolError
              ? "SSE_PROTOCOL_ERROR"
              : "SSE_STREAM_ERROR";
        setStream((current) => ({
          ...current,
          runId: activeRunId,
          assistantMessageId: activeRunAssistantMessageId,
          status: "error",
          errorCode: code,
        }));
      }
    })();
    return () => controller.abort();
  }, [
    activeRunAssistantMessageId,
    activeRunConversationId,
    activeRunId,
    effectiveConversationId,
    invalidateConversation,
    workspaceId,
  ]);

  useEffect(() => () => abortRef.current?.abort(), []);

  function selectConversation(conversationId: string) {
    abortRef.current?.abort();
    consumedRunRef.current = null;
    setStream(EMPTY_STREAM);
    setPendingRun(null);
    setSourceMessageId(null);
    setFeedbackMessageId(null);
    setSelectedConversationId(conversationId);
  }

  function sendMessage(text: string) {
    if (!effectiveConversationId || createMessage.isPending) return;
    createMessage.mutate({ conversationId: effectiveConversationId, text });
  }

  const selectedAssistantMessage =
    messages.data?.find((message) => message.message_id === sourceMessageId) ?? null;
  const feedbackMessage =
    messages.data?.find((message) => message.message_id === feedbackMessageId) ?? null;

  return {
    workspaceId,
    conversations,
    selectedConversation,
    selectedConversationId: effectiveConversationId,
    selectConversation,
    createConversation,
    messages,
    runs,
    activeRun,
    stream,
    sendMessage,
    createMessage,
    cancelRun,
    sources,
    sourceMessageId,
    selectedAssistantMessage,
    openSources: setSourceMessageId,
    closeSources: () => setSourceMessageId(null),
    feedback,
    feedbackMessageId,
    feedbackMessage,
    openFeedback: setFeedbackMessageId,
    closeFeedback: () => setFeedbackMessageId(null),
    submitFeedback,
    errorMessage,
  };
}
