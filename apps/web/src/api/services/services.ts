/**
 * @description 服务定义、访问策略与版本化路由 API Service
 *
 * 所有写操作携带独立幂等键；前端不直接推算下一 generation 或修改 Route 历史。
 */
import { apiRequest } from "@/api/client";
import type { components } from "@/api/generated/platform-api.v1";

/** 服务、当前策略、Route 与发布指针聚合。 */
export type ServiceDeployment = components["schemas"]["ServiceDeploymentResponse"];
/** 创建服务所需的 Release 与入口类型。 */
export type CreateServiceRequest = components["schemas"]["CreateServiceRequest"];
/** 更新服务定义或受众策略的乐观锁请求。 */
export type UpdateServiceRequest = components["schemas"]["UpdateServiceRequest"];

/** 查询当前空间可管理的服务。 */
export async function getServices(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["ServiceListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/services`,
    { signal },
  );
  return response.items;
}

/** 从有效自定义 Release 创建稳定服务身份和首个路由。 */
export function createService(workspaceId: string, body: CreateServiceRequest) {
  return apiRequest<ServiceDeployment>(`/api/v1/workspaces/${workspaceId}/services`, {
    method: "POST",
    headers: { "Idempotency-Key": `service-create-${crypto.randomUUID()}` },
    body,
  });
}

/** 更新名称、状态或访问策略，旧策略版本保持不可变。 */
export function updateService(workspaceId: string, serviceId: string, body: UpdateServiceRequest) {
  return apiRequest<ServiceDeployment>(`/api/v1/workspaces/${workspaceId}/services/${serviceId}`, {
    method: "PUT",
    headers: { "Idempotency-Key": `service-update-${crypto.randomUUID()}` },
    body,
  });
}

/** 追加固定百分比灰度 Route。 */
export function startServiceCanary(
  workspaceId: string,
  serviceId: string,
  releaseId: string,
  canaryPercent: number,
  expectedGeneration: number,
) {
  return apiRequest<ServiceDeployment>(
    `/api/v1/workspaces/${workspaceId}/services/${serviceId}/routes/canary`,
    {
      method: "POST",
      headers: { "Idempotency-Key": `service-canary-${crypto.randomUUID()}` },
      body: {
        release_id: releaseId,
        canary_percent: canaryPercent,
        expected_generation: expectedGeneration,
      },
    },
  );
}

/** 把指定 Release 晋级为唯一正式 Route。 */
export function promoteServiceRoute(
  workspaceId: string,
  serviceId: string,
  releaseId: string,
  expectedGeneration: number,
) {
  return apiRequest<ServiceDeployment>(
    `/api/v1/workspaces/${workspaceId}/services/${serviceId}/routes/promote`,
    {
      method: "POST",
      headers: { "Idempotency-Key": `service-promote-${crypto.randomUUID()}` },
      body: { release_id: releaseId, expected_generation: expectedGeneration },
    },
  );
}

/** 追加回滚 Route 并恢复最近稳定版本。 */
export function rollbackServiceRoute(
  workspaceId: string,
  serviceId: string,
  expectedGeneration: number,
) {
  return apiRequest<ServiceDeployment>(
    `/api/v1/workspaces/${workspaceId}/services/${serviceId}/routes/rollback`,
    {
      method: "POST",
      headers: { "Idempotency-Key": `service-rollback-${crypto.randomUUID()}` },
      body: { expected_generation: expectedGeneration },
    },
  );
}
