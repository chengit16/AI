export type HealthStatus = "ok" | "degraded";

export interface HealthResponse {
  service: string;
  status: HealthStatus;
  version: string;
  environment: string;
  checks: Record<string, HealthStatus>;
}

export async function getPlatformHealth(): Promise<HealthResponse> {
  const response = await fetch("/api/v1/health/ready", {
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    throw new Error(`健康检查失败：HTTP ${response.status}`);
  }

  return response.json() as Promise<HealthResponse>;
}
