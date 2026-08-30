/** @description 私有助手会话、运行、来源和反馈 API Service。 */
import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

type AssistantConversationResponse = components["schemas"]["ConversationResponse"];
type AssistantRunResponse = components["schemas"]["AssistantRunResponse"];

/** 当前账号创建并可见的私有问答会话。 */
export type AssistantConversation = Omit<
  AssistantConversationResponse,
  "scope_mode" | "knowledge_base_ids" | "tag_ids"
> & {
  /** 会话检索范围；旧服务端缺省时按当前空间处理。 */
  readonly scope_mode: "workspace" | "selected";
  /** 指定范围内的知识库标识。 */
  readonly knowledge_base_ids: readonly string[];
  /** 指定范围内的标签标识。 */
  readonly tag_ids: readonly string[];
};
/** 会话内不可变消息及其文本 Part。 */
export type AssistantMessage = components["schemas"]["MessageResponse"];
/** 冻结运行配置、助手版本、知识范围和当前终态的一次问答 Run。 */
export type AssistantRun = Omit<
  AssistantRunResponse,
  "knowledge_base_ids" | "document_ids" | "attachment_ids"
> & {
  /** 空值表示工作空间范围，空数组表示指定范围解析后没有可检索资源。 */
  readonly knowledge_base_ids: readonly string[] | null;
  /** 标签范围冻结出的文档集合；空值表示没有标签维度。 */
  readonly document_ids: readonly string[] | null;
  /** 本次运行冻结使用的会话附件。 */
  readonly attachment_ids: readonly string[];
};
/** 经当前权限和版本复核后仍可展示的引用来源。 */
export type AssistantSource = components["schemas"]["AssistantSourceResponse"];
/** 当前账号对单条助手消息的最新反馈事实。 */
export type MessageFeedback = components["schemas"]["MessageFeedbackResponse"];
/** 提交帮助度、问题标签和可选说明的反馈请求。 */
export type FeedbackRequest = components["schemas"]["SubmitMessageFeedbackRequest"];
/** 不进入永久知识库的会话临时附件元数据。 */
export type ConversationAttachment = components["schemas"]["ConversationAttachmentResponse"];
/** 会话级知识范围更新请求。 */
export type ConversationScopeRequest = components["schemas"]["UpdateConversationScopeRequest"];

/** 查询当前账号创建的私有会话。 */
export async function getAssistantConversations(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["ConversationListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/conversations`,
    { signal },
  );
  return response.items.map(normalizeAssistantConversation);
}

/** 创建新的私有知识问答会话。 */
export async function createAssistantConversation(workspaceId: string, title?: string) {
  const response = await apiRequest<AssistantConversationResponse>(
    `/api/v1/workspaces/${workspaceId}/conversations`,
    {
      method: "POST",
      body: { title: title?.trim() || null },
    },
  );
  return normalizeAssistantConversation(response);
}

/** 更新活动会话的知识库和标签范围。 */
export async function updateAssistantConversationScope(
  workspaceId: string,
  conversationId: string,
  body: ConversationScopeRequest,
) {
  const response = await apiRequest<AssistantConversationResponse>(
    `/api/v1/workspaces/${workspaceId}/conversations/${conversationId}/scope`,
    { method: "PUT", body },
  );
  return normalizeAssistantConversation(response);
}

/** 归档没有活动 Run 的私有会话。 */
export async function archiveAssistantConversation(workspaceId: string, conversationId: string) {
  const response = await apiRequest<AssistantConversationResponse>(
    `/api/v1/workspaces/${workspaceId}/conversations/${conversationId}/archive`,
    { method: "POST" },
  );
  return normalizeAssistantConversation(response);
}

/** 查询会话内仍存在的临时附件。 */
export async function getAssistantAttachments(
  workspaceId: string,
  conversationId: string,
  signal?: AbortSignal,
) {
  const response = await apiRequest<components["schemas"]["ConversationAttachmentListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/conversations/${conversationId}/attachments`,
    { signal },
  );
  return response.items;
}

/** 上传小型 UTF-8 文本附件；服务端负责类型、大小和内容校验。 */
export function uploadAssistantAttachment(workspaceId: string, conversationId: string, file: File) {
  const body = new FormData();
  body.append("file", file);
  return apiRequest<ConversationAttachment>(
    `/api/v1/workspaces/${workspaceId}/conversations/${conversationId}/attachments`,
    { method: "POST", body },
  );
}

/** 删除会话临时附件；活动 Run 期间服务端会拒绝。 */
export function deleteAssistantAttachment(
  workspaceId: string,
  conversationId: string,
  attachmentId: string,
) {
  return apiRequest<void>(
    `/api/v1/workspaces/${workspaceId}/conversations/${conversationId}/attachments/${attachmentId}`,
    { method: "DELETE" },
  );
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
export async function createAssistantMessage(
  workspaceId: string,
  conversationId: string,
  parts: string[],
  idempotencyKey: string,
  attachmentIds: string[] = [],
) {
  const response = await apiRequest<components["schemas"]["UserMessageCreatedResponse"]>(
    `/api/v1/workspaces/${workspaceId}/conversations/${conversationId}/messages`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: {
        parts: parts.map((text) => ({ type: "text" as const, text })),
        attachment_ids: attachmentIds,
      },
    },
  );
  return { ...response, run: normalizeAssistantRun(response.run) };
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
  return response.items.map(normalizeAssistantRun);
}

/** 条件取消排队或运行中的助手 Run。 */
export async function cancelAssistantRun(
  workspaceId: string,
  conversationId: string,
  runId: string,
) {
  const response = await apiRequest<AssistantRunResponse>(
    `/api/v1/workspaces/${workspaceId}/conversations/${conversationId}/runs/${runId}/cancel`,
    { method: "POST" },
  );
  return normalizeAssistantRun(response);
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

function normalizeAssistantConversation(
  value: AssistantConversationResponse,
): AssistantConversation {
  return {
    ...value,
    scope_mode: value.scope_mode ?? "workspace",
    knowledge_base_ids: value.knowledge_base_ids ?? [],
    tag_ids: value.tag_ids ?? [],
  };
}

function normalizeAssistantRun(value: AssistantRunResponse): AssistantRun {
  return {
    ...value,
    knowledge_base_ids: value.knowledge_base_ids ?? null,
    document_ids: value.document_ids ?? null,
    attachment_ids: value.attachment_ids ?? [],
  };
}
