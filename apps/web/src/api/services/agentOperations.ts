/**
 * @description AgentRelease 运营聚合 API Service
 *
 * 只读取服务与 Release 级低基数指标；页面不得通过该接口获得 Run、Actor、Trace、Prompt 或正文。
 */
import { apiRequest } from "@/api/client";
import type { components } from "@/api/generated/platform-api.v1";

/** 当前 Route 的主版本、比较版本、告警与晋级证据。 */
export type AgentOperationsReport = components["schemas"]["AgentOperationsReportResponse"];
/** 单个不可变 Release 在固定时间窗口内的聚合指标。 */
export type ReleaseOperationsMetrics = components["schemas"]["ReleaseOperationsMetricsResponse"];
/** 由稳定阈值生成的结构化告警。 */
export type OperationsAlert = components["schemas"]["OperationsAlertResponse"];

/** 查询一个服务当前 Route 的脱敏 Release 运营报告。 */
export function getAgentReleaseOperations(
  workspaceId: string,
  serviceId: string,
  windowHours: number,
  signal?: AbortSignal,
) {
  const query = new URLSearchParams({
    service_id: serviceId,
    window_hours: String(windowHours),
  });
  return apiRequest<AgentOperationsReport>(
    `/api/v1/workspaces/${workspaceId}/agent-release-operations?${query.toString()}`,
    { signal },
  );
}
