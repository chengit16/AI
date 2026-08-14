/** @description 私有助手会话、运行、来源和反馈 API Service。 */
import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

/** 当前账号创建并可见的私有问答会话。 */
export type AssistantConversation = components["schemas"]["ConversationResponse"];
/** 会话内不可变消息及其文本 Part。 */
export type AssistantMessage = components["schemas"]["MessageResponse"];
/** 冻结运行配置、助手版本和当前终态的一次问答 Run。 */
export type AssistantRun = components["schemas"]["AssistantRunResponse"];
/** 经当前权限和版本复核后仍可展示的引用来源。 */
export type AssistantSource = components["schemas"]["AssistantSourceResponse"];
/** 当前账号对单条助手消息的最新反馈事实。 */
export type MessageFeedback = components["schemas"]["MessageFeedbackResponse"];
/** 提交帮助度、问题标签和可选说明的反馈请求。 */
export type FeedbackRequest = components["schemas"]["SubmitMessageFeedbackRequest"];

/** 查询当前账号创建的私有会话。 */
export async function getAssistantConversations(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["ConversationListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/conversations`,
    { signal },
  );
  return response.items;
}

/** 创建新的私有知识问答会话。 */
export function createAssistantConversation(workspaceId: string, title?: string) {
  return apiRequest<AssistantConversation>(`/api/v1/workspaces/${workspaceId}/conversations`, {
    method: "POST",
    body: { title: title?.trim() || null },
  });
}

/** 查询消息事实；流式临时正文由 Hook 维护，不写入服务端缓存。 */
export async function getAssistantMessages(
  workspaceId: string,
  conversationId: string,
  signal?: AbortSignal,
) {
  const response = await apiRequest<components["schemas"]["MessageListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/conversations/${conversationId}/messages`,
    { signal },
  );
  return response.items;
}

/** 幂等提交一条用户消息，并返回已排队的助手 Run。 */
export function createAssistantMessage(
  workspaceId: string,
  conversationId: string,
  parts: string[],
  idempotencyKey: string,
) {
  return apiRequest<components["schemas"]["UserMessageCreatedResponse"]>(
    `/api/v1/workspaces/${workspaceId}/conversations/${conversationId}/messages`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: { parts: parts.map((text) => ({ type: "text" as const, text })) },
    },
  );
}

/** 列出会话 Run，刷新后据此恢复 queued/running 流。 */
export async function getAssistantRuns(
  workspaceId: string,
  conversationId: string,
  signal?: AbortSignal,
) {
  const response = await apiRequest<components["schemas"]["AssistantRunListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/conversations/${conversationId}/runs`,
    { signal },
  );
  return response.items;
}

/** 条件取消排队或运行中的助手 Run。 */
export function cancelAssistantRun(workspaceId: string, conversationId: string, runId: string) {
  return apiRequest<AssistantRun>(
    `/api/v1/workspaces/${workspaceId}/conversations/${conversationId}/runs/${runId}/cancel`,
    { method: "POST" },
  );
}

/** 读取当前权限和文档版本复核后的来源列表。 */
export async function getAssistantSources(
  workspaceId: string,
  conversationId: string,
  messageId: string,
  signal?: AbortSignal,
) {
  const response = await apiRequest<components["schemas"]["AssistantSourceListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/conversations/${conversationId}/messages/${messageId}/sources`,
    { signal },
  );
  return response.items;
}

/** 读取当前账号对消息的最新反馈，不读取其他账号的自由文本。 */
export async function getAssistantFeedback(
  workspaceId: string,
  conversationId: string,
  messageId: string,
  signal?: AbortSignal,
) {
  const response = await apiRequest<components["schemas"]["CurrentMessageFeedbackResponse"]>(
    `/api/v1/workspaces/${workspaceId}/conversations/${conversationId}/messages/${messageId}/feedback`,
    { signal },
  );
  return response.item;
}

/** 新增或修订当前账号对消息的反馈。 */
export function submitAssistantFeedback(
  workspaceId: string,
  conversationId: string,
  messageId: string,
  body: FeedbackRequest,
) {
  return apiRequest<MessageFeedback>(
    `/api/v1/workspaces/${workspaceId}/conversations/${conversationId}/messages/${messageId}/feedback`,
    { method: "PUT", body },
  );
}
