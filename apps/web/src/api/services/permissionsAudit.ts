/**
 * @description 权限矩阵与审计产品页 API Service
 * 只映射稳定契约；角色并发版本、字段脱敏、空间隔离和导出授权均由服务端执行。
 */
import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

/** 服务端聚合的角色治理快照，包含可信角色版本和影响成员。 */
export type RoleGovernance = components["schemas"]["RoleGovernanceResponse"];
/** 角色的一项数据范围、密级和字段遮罩授权。 */
export type RoleGrant = components["schemas"]["RolePermissionEntry"];
/** 不含自由属性的审计列表记录。 */
export type AuditRecord = components["schemas"]["AuditRecordResponse"];
/** 经服务端白名单化和字段策略裁剪的审计详情。 */
export type AuditRecordDetail = components["schemas"]["AuditRecordDetailResponse"];
/** 冻结筛选和字段权限后的异步审计导出状态。 */
export type AuditExport = components["schemas"]["AuditExportResponse"];
/** 审计记录允许筛选的稳定执行结果。 */
export type AuditOutcome = "succeeded" | "denied" | "failed";

/** 可恢复到 URL 的审计筛选条件。 */
export interface AuditFilters {
  actorId?: string;
  action?: string;
  resourceType?: string;
  outcome?: AuditOutcome;
  occurredFrom?: string;
  occurredTo?: string;
}

function auditSearch(filters: AuditFilters, limit: number): string {
  const params = new URLSearchParams({ limit: String(limit) });
  if (filters.actorId) params.set("actor_id", filters.actorId);
  if (filters.action) params.set("action", filters.action);
  if (filters.resourceType) params.set("resource_type", filters.resourceType);
  if (filters.outcome) params.set("outcome", filters.outcome);
  if (filters.occurredFrom) params.set("occurred_from", filters.occurredFrom);
  if (filters.occurredTo) params.set("occurred_to", filters.occurredTo);
  return params.toString();
}

/** 一次读取角色、授权、影响成员与权限目录。 */
export function getRoleGovernance(workspaceId: string, signal?: AbortSignal) {
  return apiRequest<RoleGovernance>("/api/v1/workspaces/" + workspaceId + "/roles/governance", {
    signal,
  });
}

/** 携带可信角色版本整体替换授权，过期页面由服务端返回 ROLE_CONFLICT。 */
export function replaceRolePermissions(
  workspaceId: string,
  roleId: string,
  expectedRoleVersion: number,
  items: readonly RoleGrant[],
) {
  return apiRequest<components["schemas"]["RolePermissionListResponse"]>(
    "/api/v1/workspaces/" + workspaceId + "/roles/" + roleId + "/permissions",
    { method: "PUT", body: { expected_role_version: expectedRoleVersion, items } },
  );
}

/** 按 URL 筛选读取一页审计事实，列表不包含自由属性。 */
export function listAuditRecords(workspaceId: string, filters: AuditFilters, signal?: AbortSignal) {
  return apiRequest<components["schemas"]["AuditRecordPageResponse"]>(
    "/api/v1/workspaces/" + workspaceId + "/operations/audit-records?" + auditSearch(filters, 50),
    { signal },
  );
}

/** 读取单条白名单化审计详情。 */
export function getAuditRecord(workspaceId: string, auditId: string, signal?: AbortSignal) {
  return apiRequest<AuditRecordDetail>(
    "/api/v1/workspaces/" + workspaceId + "/operations/audit-records/" + auditId,
    { signal },
  );
}

/** 读取最近异步导出状态，不返回内部哈希、对象键或原始文件。 */
export async function listAuditExports(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["AuditExportListResponse"]>(
    "/api/v1/workspaces/" + workspaceId + "/operations/audit-exports?limit=20",
    { signal },
  );
  return response.items;
}

/** 冻结当前筛选和字段权限，登记幂等异步导出请求。 */
export function createAuditExport(workspaceId: string, filters: AuditFilters) {
  return apiRequest<AuditExport>(
    "/api/v1/workspaces/" + workspaceId + "/operations/audit-exports",
    {
      method: "POST",
      body: {
        idempotency_key: "audit-export-" + crypto.randomUUID(),
        actor_id: filters.actorId || null,
        action: filters.action || null,
        resource_type: filters.resourceType || null,
        outcome: filters.outcome || null,
        occurred_from: filters.occurredFrom || null,
        occurred_to: filters.occurredTo || null,
      },
    },
  );
}
