/** @description 工作空间套餐权益查询与平台功能开关 API Service。 */
import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

/** 当前空间经过服务端计算的套餐、功能开关和用量快照。 */
export type Entitlement = components["schemas"]["EntitlementResponse"];

/** 查询当前主体可见的空间权益快照。 */
export function getWorkspaceEntitlement(workspaceId: string, signal?: AbortSignal) {
  return apiRequest<Entitlement>(`/api/v1/workspaces/${workspaceId}/entitlements`, { signal });
}

/** 启停空间 Open API 功能；套餐和操作者权限仍由后端最终校验。 */
export function setWorkspaceOpenApiFeature(workspaceId: string, enabled: boolean) {
  return apiRequest<Entitlement>(
    `/api/v1/workspaces/${workspaceId}/entitlements/features/open-api`,
    { method: "POST", body: { enabled } },
  );
}
