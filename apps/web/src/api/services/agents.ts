/**
 * @description Agent 控制台 API Service
 *
 * 只处理类型化 HTTP、幂等请求头和稳定路径，不在此解释测试、审批或发布状态机。
 */
import { apiRequest } from "@/api/client";
import type { components } from "@/api/generated/platform-api.v1";

/** Agent 定义与当前草稿聚合。 */
export type AgentDetail = components["schemas"]["AgentDetailResponse"];
/** 发布候选、最近测试与审批状态聚合。 */
export type AgentCandidateControl = components["schemas"]["AgentCandidateControlResponse"];
/** 不可变 Agent Release 摘要。 */
export type AgentRelease = components["schemas"]["AgentReleaseResponse"];
/** 创建 Agent 的完整定义与版本引用配置。 */
export type CreateAgentRequest = components["schemas"]["CreateAgentRequest"];

/** 查询当前空间的全部自定义 Agent 和当前草稿。 */
export async function getAgents(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["AgentListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/agents`,
    { signal },
  );
  return response.items;
}

/** 原子创建 Agent 与首个草稿。 */
export function createAgent(workspaceId: string, body: CreateAgentRequest) {
  return apiRequest<AgentDetail>(`/api/v1/workspaces/${workspaceId}/agents`, {
    method: "POST",
    headers: { "Idempotency-Key": `agent-create-${crypto.randomUUID()}` },
    body,
  });
}

/** 按 revision 整体替换草稿配置。 */
export function updateAgentDraft(
  workspaceId: string,
  agentId: string,
  expectedRevision: number,
  configuration: Record<string, unknown>,
) {
  return apiRequest<components["schemas"]["AgentDraftResponse"]>(
    `/api/v1/workspaces/${workspaceId}/agents/${agentId}/draft`,
    {
      method: "PUT",
      headers: { "Idempotency-Key": `agent-draft-${crypto.randomUUID()}` },
      body: { expected_revision: expectedRevision, configuration },
    },
  );
}

/** 归档 Agent，历史候选、Release 和服务路由不删除。 */
export function archiveAgent(workspaceId: string, agentId: string, expectedVersion: number) {
  return apiRequest<components["schemas"]["AgentResponse"]>(
    `/api/v1/workspaces/${workspaceId}/agents/${agentId}/archive`,
    {
      method: "POST",
      headers: { "Idempotency-Key": `agent-archive-${crypto.randomUUID()}` },
      body: { expected_version: expectedVersion },
    },
  );
}

/** 冻结当前草稿 revision 为发布候选。 */
export function requestAgentRelease(
  workspaceId: string,
  agentId: string,
  expectedRevision: number,
) {
  return apiRequest<components["schemas"]["AgentCandidateResponse"]>(
    `/api/v1/workspaces/${workspaceId}/agents/${agentId}/release-requests`,
    {
      method: "POST",
      headers: { "Idempotency-Key": `agent-candidate-${crypto.randomUUID()}` },
      body: { expected_revision: expectedRevision },
    },
  );
}

/** 查询候选流水及其最近测试和审批摘要。 */
export async function getAgentCandidates(
  workspaceId: string,
  agentId: string,
  signal?: AbortSignal,
) {
  const response = await apiRequest<components["schemas"]["AgentCandidateListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/agents/${agentId}/release-requests`,
    { signal },
  );
  return response.items;
}

/** 执行平台固定五类确定性门禁，浏览器不能上传测试观测。 */
export function runAgentEvaluation(workspaceId: string, agentId: string, candidateId: string) {
  return apiRequest<components["schemas"]["AgentEvaluationResponse"]>(
    `/api/v1/workspaces/${workspaceId}/agents/${agentId}/release-requests/${candidateId}/evaluations`,
    { method: "POST" },
  );
}

/** 为已通过测试的候选发起个人所有者确认或企业多级审批。 */
export function requestAgentApproval(workspaceId: string, agentId: string, candidateId: string) {
  return apiRequest<components["schemas"]["AgentApprovalResponse"]>(
    `/api/v1/workspaces/${workspaceId}/agents/${agentId}/release-requests/${candidateId}/approval`,
    {
      method: "POST",
      headers: { "Idempotency-Key": `agent-approval-${crypto.randomUUID()}` },
    },
  );
}

/** 把已通过测试和审批的候选固化为不可变 Release。 */
export function publishAgentRelease(workspaceId: string, agentId: string, candidateId: string) {
  return apiRequest<AgentRelease>(
    `/api/v1/workspaces/${workspaceId}/agents/${agentId}/release-requests/${candidateId}/publish`,
    {
      method: "POST",
      headers: { "Idempotency-Key": `agent-publish-${crypto.randomUUID()}` },
    },
  );
}

/** 查询 Agent 的不可变 Release 历史。 */
export async function getAgentReleases(workspaceId: string, agentId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["AgentReleaseListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/agents/${agentId}/releases`,
    { signal },
  );
  return response.items;
}
