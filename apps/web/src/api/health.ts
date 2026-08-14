/** @description 平台健康状态查询契约与请求封装。 */
export type HealthStatus = "ok" | "degraded";

/** 平台就绪探针返回的服务状态和受治理依赖检查结果。 */
export interface HealthResponse {
  service: string;
  status: HealthStatus;
  version: string;
  environment: string;
  checks: Record<string, HealthStatus>;
}

/** 查询匿名就绪探针；非成功响应作为页面不可达状态抛出。 */
export async function getPlatformHealth(): Promise<HealthResponse> {
  const response = await fetch("/api/v1/health/ready", {
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    throw new Error(`健康检查失败：HTTP ${response.status}`);
  }

  return response.json() as Promise<HealthResponse>;
}
