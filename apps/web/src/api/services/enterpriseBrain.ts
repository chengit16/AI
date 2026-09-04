/** @description AI 企业大脑聚合、会话和报告 API Service。 */
import type { components } from "@/api/generated/platform-api.v1";
import { apiDownloadRequest, apiRequest, type ApiDownload } from "@/api/client";
import type {
  AssistantRun,
  AssistantSource,
  FeedbackRequest,
  MessageFeedback,
} from "@/api/services/assistant";
import { streamAssistantRun } from "@/api/assistantSse";

/** 企业大脑页面使用的低敏聚合统计。 */
export type EnterpriseBrainOverview = components["schemas"]["EnterpriseBrainOverviewResponse"];
/** 冻结团队知识域身份的企业大脑会话。 */
export type EnterpriseBrainConversation =
  components["schemas"]["EnterpriseBrainConversationResponse"];
/** 企业大脑会话中的不可变消息。 */
export type EnterpriseBrainMessage = components["schemas"]["MessageResponse"];
/** 由已完成回答生成的不可变 Markdown 报告。 */
export type EnterpriseBrainReport = components["schemas"]["EnterpriseBrainReportResponse"];
/** 冻结知识域与检索范围的企业大脑运行。 */
export type EnterpriseBrainRun = AssistantRun;
/** 经过当前文档授权复核的回答来源。 */
export type EnterpriseBrainSource = AssistantSource;
/** 当前账号对企业大脑回答的可修订反馈。 */
export type EnterpriseBrainFeedback = MessageFeedback;

/** 查询企业大脑低敏聚合统计。 */
export function getEnterpriseBrainOverview(workspaceId: string, signal?: AbortSignal) {
  return apiRequest<EnterpriseBrainOverview>(`/api/v1/workspaces/${workspaceId}/enterprise-brain`, {
    signal,
  });
}

/** 查询当前账号自己的企业大脑会话。 */
export async function getEnterpriseBrainConversations(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<
    components["schemas"]["EnterpriseBrainConversationListResponse"]
  >(`/api/v1/workspaces/${workspaceId}/enterprise-brain/conversations`, { signal });
  return response.items;
}

/** 创建并冻结一个团队知识域会话。 */
export function createEnterpriseBrainConversation(
  workspaceId: string,
  knowledgeDomainId: string,
  title?: string,
) {
  return apiRequest<EnterpriseBrainConversation>(
    `/api/v1/workspaces/${workspaceId}/enterprise-brain/conversations`,
    {
      method: "POST",
      body: { knowledge_domain_id: knowledgeDomainId, title: title?.trim() || null },
    },
  );
}

/** 查询企业大脑会话消息历史。 */
export async function getEnterpriseBrainMessages(
  workspaceId: string,
  conversationId: string,
  signal?: AbortSignal,
) {
  const response = await apiRequest<components["schemas"]["MessageListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/enterprise-brain/conversations/${conversationId}/messages`,
    { signal },
  );
  return response.items;
}

/** 查询会话 Run，用于刷新后恢复活动状态与终态。 */
export async function getEnterpriseBrainRuns(
  workspaceId: string,
  conversationId: string,
  signal?: AbortSignal,
) {
  const response = await apiRequest<components["schemas"]["AssistantRunListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/enterprise-brain/conversations/${conversationId}/runs`,
    { signal },
  );
  return response.items as readonly EnterpriseBrainRun[];
}

/** 幂等提交一条企业问答消息。 */
export function createEnterpriseBrainMessage(
  workspaceId: string,
  conversationId: string,
  text: string,
  idempotencyKey: string,
) {
  return apiRequest<components["schemas"]["UserMessageCreatedResponse"]>(
    `/api/v1/workspaces/${workspaceId}/enterprise-brain/conversations/${conversationId}/messages`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: { parts: [{ type: "text", text }], attachment_ids: [] },
    },
  );
}

/** 归档当前账号自己的企业大脑会话。 */
export function archiveEnterpriseBrainConversation(workspaceId: string, conversationId: string) {
  return apiRequest<EnterpriseBrainConversation>(
    `/api/v1/workspaces/${workspaceId}/enterprise-brain/conversations/${conversationId}/archive`,
    { method: "POST" },
  );
}

/** 取消排队或运行中的企业大脑 Run。 */
export function cancelEnterpriseBrainRun(
  workspaceId: string,
  conversationId: string,
  runId: string,
) {
  return apiRequest<components["schemas"]["AssistantRunResponse"]>(
    `/api/v1/workspaces/${workspaceId}/enterprise-brain/conversations/${conversationId}/runs/${runId}/cancel`,
    { method: "POST" },
  );
}

/** 读取当前仍获授权的企业大脑回答来源。 */
export async function getEnterpriseBrainSources(
  workspaceId: string,
  conversationId: string,
  messageId: string,
  signal?: AbortSignal,
) {
  const response = await apiRequest<components["schemas"]["AssistantSourceListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/enterprise-brain/conversations/${conversationId}/messages/${messageId}/sources`,
    { signal },
  );
  return response.items as readonly EnterpriseBrainSource[];
}

/** 读取当前账号对企业大脑回答的反馈。 */
export async function getEnterpriseBrainFeedback(
  workspaceId: string,
  conversationId: string,
  messageId: string,
  signal?: AbortSignal,
) {
  const response = await apiRequest<components["schemas"]["CurrentMessageFeedbackResponse"]>(
    `/api/v1/workspaces/${workspaceId}/enterprise-brain/conversations/${conversationId}/messages/${messageId}/feedback`,
    { signal },
  );
  return response.item as EnterpriseBrainFeedback | null;
}

/** 新增或修订当前账号对企业大脑回答的反馈。 */
export function submitEnterpriseBrainFeedback(
  workspaceId: string,
  conversationId: string,
  messageId: string,
  body: FeedbackRequest,
) {
  return apiRequest<EnterpriseBrainFeedback>(
    `/api/v1/workspaces/${workspaceId}/enterprise-brain/conversations/${conversationId}/messages/${messageId}/feedback`,
    { method: "PUT", body },
  );
}

/** 读取企业大脑 Run 的可恢复事件流；不创建新 Run。 */
export function streamEnterpriseBrainRun(
  workspaceId: string,
  conversationId: string,
  runId: string,
  options: { signal?: AbortSignal; lastEventId?: string | null; maxReconnects?: number } = {},
) {
  return streamAssistantRun(workspaceId, conversationId, runId, {
    ...options,
    eventsPath: `/api/v1/workspaces/${workspaceId}/enterprise-brain/conversations/${conversationId}/runs/${runId}/events`,
  });
}

/** 查询当前账号生成的不可变报告。 */
export async function getEnterpriseBrainReports(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["EnterpriseBrainReportListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/enterprise-brain/reports`,
    { signal },
  );
  return response.items;
}

/** 从已完成企业回答生成不可变 Markdown 报告。 */
export function createEnterpriseBrainReport(
  workspaceId: string,
  body: components["schemas"]["CreateEnterpriseBrainReportRequest"],
  idempotencyKey: string,
) {
  return apiRequest<EnterpriseBrainReport>(
    `/api/v1/workspaces/${workspaceId}/enterprise-brain/reports`,
    { method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body },
  );
}

/** 读取单个不可变报告正文。 */
export function getEnterpriseBrainReport(
  workspaceId: string,
  reportId: string,
  signal?: AbortSignal,
) {
  return apiRequest<EnterpriseBrainReport>(
    `/api/v1/workspaces/${workspaceId}/enterprise-brain/reports/${reportId}`,
    { signal },
  );
}

/** 下载报告附件；Client 只返回 Blob 与安全文件名，不暴露对象存储定位。 */
export function downloadEnterpriseBrainReport(
  workspaceId: string,
  reportId: string,
  signal?: AbortSignal,
): Promise<ApiDownload> {
  return apiDownloadRequest(
    `/api/v1/workspaces/${workspaceId}/enterprise-brain/reports/${reportId}/download`,
    signal,
  );
}
