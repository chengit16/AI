/** @description 工具目录、任务控制、确认审批和脱敏历史 API Service。 */
import { apiRequest } from "@/api/client";
import type { components } from "@/api/generated/platform-api.v1";

/** 当前套餐、注册状态和 PDP 共同允许的工具。 */
export type ToolCatalogItem = components["schemas"]["ToolCatalogItemResponse"];
/** 工具任务的低敏聚合摘要。 */
export type ToolRunSummary = components["schemas"]["ToolRunSummaryResponse"];
/** 工具任务、步骤、确认和尝试的脱敏详情。 */
export type ToolRunDetail = components["schemas"]["ToolRunDetailResponse"];
/** 创建并冻结工具计划的请求。 */
export type CreateToolRunRequest = components["schemas"]["CreateToolRunRequest"];

/** 查询当前工作空间可执行的工具交集。 */
export async function getAvailableTools(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["ToolCatalogResponse"]>(
    `/api/v1/workspaces/${workspaceId}/tools`,
    { signal },
  );
  return response.items;
}

/** 查询当前资源范围内的工具任务历史。 */
export async function getToolRuns(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["ToolRunListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/tool-runs`,
    { signal },
  );
  return response.items;
}

/** 查询一个工具任务的脱敏详情。 */
export function getToolRun(workspaceId: string, runId: string, signal?: AbortSignal) {
  return apiRequest<ToolRunDetail>(`/api/v1/workspaces/${workspaceId}/tool-runs/${runId}`, {
    signal,
  });
}

/** 幂等创建 Run 并冻结完整工具计划。 */
export function createToolRun(workspaceId: string, body: CreateToolRunRequest) {
  return apiRequest<ToolRunDetail>(`/api/v1/workspaces/${workspaceId}/tool-runs`, {
    method: "POST",
    headers: { "Idempotency-Key": `tool-run-${crypto.randomUUID()}` },
    body,
  });
}

/** 响应当前工具确认；最终批准后仍由服务端重新执行 PDP。 */
export function confirmToolCall(workspaceId: string, runId: string, confirmationId: string) {
  return respondToToolConfirmation(workspaceId, runId, confirmationId, "confirm");
}

/** 驳回当前工具确认并由服务端收口未执行任务。 */
export function rejectToolCall(workspaceId: string, runId: string, confirmationId: string) {
  return respondToToolConfirmation(workspaceId, runId, confirmationId, "reject");
}

/** 请求取消工具任务；迟到结果仍由服务端隔离。 */
export function cancelToolRun(workspaceId: string, runId: string) {
  return apiRequest<ToolRunDetail>(`/api/v1/workspaces/${workspaceId}/tool-runs/${runId}/cancel`, {
    method: "POST",
  });
}

function respondToToolConfirmation(
  workspaceId: string,
  runId: string,
  confirmationId: string,
  action: "confirm" | "reject",
) {
  return apiRequest<ToolRunDetail>(
    `/api/v1/workspaces/${workspaceId}/tool-runs/${runId}/confirmations/${confirmationId}/${action}`,
    {
      method: "POST",
      body: { idempotency_key: `tool-confirmation-${crypto.randomUUID()}`, reason_code: null },
    },
  );
}
