/**
 * @description 受控运营工作台 API Service
 * 只映射登记接口和稳定响应；危险操作的最终权限、确认、幂等和状态复核由服务端负责。
 */
import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

/** 工作空间任务、索引、Outbox 与生命周期的低基数聚合快照。 */
export type OperationsOverview = components["schemas"]["OperationsOverviewResponse"];
/** 跨知识库入库任务的脱敏运营投影，不包含对象键与租约。 */
export type OperationsIngestionJob = components["schemas"]["OperationsIngestionJobResponse"];
/** 人工索引维护命令的排队、重试和完成状态。 */
export type IndexMaintenanceRequest = components["schemas"]["IndexMaintenanceRequestResponse"];
/** 已完成索引维护的计数与可复算摘要。 */
export type IndexMaintenanceRun = components["schemas"]["IndexMaintenanceRunResponse"];
/** 导出、业务清除和保留期执行的统一历史投影。 */
export type LifecycleOperation = components["schemas"]["LifecycleOperationResponse"];
/** 不暴露自由属性的授权审计记录。 */
export type AuditRecord = components["schemas"]["AuditRecordResponse"];
/** 不返回 Payload 的 Outbox 事件运营元数据。 */
export type OutboxEvent = components["schemas"]["OutboxEventResponse"];
/** Outbox 积压、Schema 与消费者回执的一致性巡检。 */
export type IntegrationInspection = components["schemas"]["IntegrationInspectionResponse"];
/** 用量计数器、明细累计值与最后结果的三方对账。 */
export type UsageReconciliation = components["schemas"]["UsageReconciliationResponse"];
/** 服务端分别授权和确认的三类索引维护命令。 */
export type IndexCommand = "inspection" | "full_rebuild" | "cleanup";

/** 返回任务、索引、Outbox 和生命周期的低基数聚合快照。 */
export function getOperationsOverview(workspaceId: string, signal?: AbortSignal) {
  return apiRequest<OperationsOverview>(
    `/api/v1/workspaces/${workspaceId}/operations/workbench/overview`,
    { signal },
  );
}

/** 跨知识库查询最近入库任务，不返回对象键和内部租约。 */
export async function getOperationsIngestionJobs(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["OperationsIngestionJobListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/operations/workbench/ingestion-jobs`,
    { signal },
  );
  return response.items;
}

/** 查询人工索引维护请求的排队、执行和恢复状态。 */
export async function getIndexMaintenanceRequests(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["IndexMaintenanceRequestListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/operations/workbench/index-maintenance/requests`,
    {
      signal,
    },
  );
  return response.items;
}

/** 查询已完成索引维护的计数与结果摘要。 */
export async function getIndexMaintenanceRuns(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["IndexMaintenanceRunListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/operations/workbench/index-maintenance/runs`,
    { signal },
  );
  return response.items;
}

/** 查询导出、清除和保留期执行的统一历史摘要。 */
export async function getLifecycleOperations(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["LifecycleOperationListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/operations/workbench/lifecycle-operations`,
    { signal },
  );
  return response.items;
}

/** 登记索引维护命令；三种路径分别绑定独立后端权限。 */
export function requestIndexMaintenance(
  workspaceId: string,
  command: IndexCommand,
  values: { idempotencyKey: string; reasonCode: string; confirmation: string },
) {
  const pathByCommand = {
    inspection: "inspections",
    full_rebuild: "rebuilds",
    cleanup: "cleanups",
  } as const;
  return apiRequest<IndexMaintenanceRequest>(
    `/api/v1/workspaces/${workspaceId}/operations/workbench/index-maintenance/${pathByCommand[command]}`,
    {
      method: "POST",
      headers: { "Idempotency-Key": values.idempotencyKey },
      body: { reason_code: values.reasonCode, confirmation: values.confirmation },
    },
  );
}

/** 取消仍可转换为稳定取消终态的入库任务。 */
export function cancelOperationsIngestionJob(workspaceId: string, ingestionJobId: string) {
  return apiRequest<components["schemas"]["IngestionJobResponse"]>(
    `/api/v1/workspaces/${workspaceId}/ingestion-jobs/${ingestionJobId}/cancel`,
    { method: "POST" },
  );
}

/** 读取最近审计事实；自由属性不会进入响应。 */
export async function getOperationsAuditRecords(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["AuditRecordPageResponse"]>(
    `/api/v1/workspaces/${workspaceId}/operations/audit-records?limit=100`,
    { signal },
  );
  return response.items;
}

/** 读取 Outbox 元数据和重放次数，不返回 Payload。 */
export async function getOperationsOutboxEvents(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["OutboxEventPageResponse"]>(
    `/api/v1/workspaces/${workspaceId}/operations/outbox-events?limit=100`,
    { signal },
  );
  return response.items;
}

/** 读取 Outbox 积压、Schema 和消费者幂等巡检摘要。 */
export function getIntegrationInspection(workspaceId: string, signal?: AbortSignal) {
  return apiRequest<IntegrationInspection>(
    `/api/v1/workspaces/${workspaceId}/operations/integration-inspection`,
    { signal },
  );
}

/** 读取用量计数器与不可变明细的逐项对账结果。 */
export async function getUsageReconciliation(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["UsageReconciliationListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/operations/usage-reconciliation`,
    { signal },
  );
  return response.items;
}

/** 保留原事件 ID 请求重放，消费者仍按原回执去重。 */
export function replayOutboxEvent(
  workspaceId: string,
  eventId: string,
  idempotencyKey: string,
  reasonCode: string,
) {
  return apiRequest<components["schemas"]["OutboxReplayResponse"]>(
    `/api/v1/workspaces/${workspaceId}/operations/outbox-events/${eventId}/replay`,
    { method: "POST", body: { idempotency_key: idempotencyKey, reason_code: reasonCode } },
  );
}

/** 同步生成工作空间导出包并返回可校验摘要。 */
export function createWorkspaceExport(workspaceId: string, idempotencyKey: string) {
  return apiRequest<components["schemas"]["LifecycleExportResponse"]>(
    `/api/v1/workspaces/${workspaceId}/lifecycle/exports`,
    { method: "POST", headers: { "Idempotency-Key": idempotencyKey } },
  );
}

/** 执行冻结保留期；同一幂等键只能对应一次运行边界。 */
export function executeWorkspaceRetention(workspaceId: string, idempotencyKey: string) {
  return apiRequest<components["schemas"]["RetentionRunResponse"]>(
    `/api/v1/workspaces/${workspaceId}/lifecycle/retention-runs`,
    { method: "POST", headers: { "Idempotency-Key": idempotencyKey } },
  );
}

/** 清除业务事实和派生介质，同时保留治理壳层及删除证明。 */
export function purgeWorkspaceBusinessData(
  workspaceId: string,
  idempotencyKey: string,
  confirmedWorkspaceName: string,
  reasonCode: string,
) {
  return apiRequest<components["schemas"]["LifecyclePurgeResponse"]>(
    `/api/v1/workspaces/${workspaceId}/lifecycle/purges`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: { confirmed_workspace_name: confirmedWorkspaceName, reason_code: reasonCode },
    },
  );
}
