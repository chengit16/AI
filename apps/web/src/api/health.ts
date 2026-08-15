/** @description 平台健康状态查询契约与请求封装。 */
export type HealthStatus = "ok" | "degraded";

/** 单项依赖探针的安全详情；原因使用稳定码，不展示后端异常文本。 */
export interface HealthCheckDetail {
  status: HealthStatus;
  critical: boolean;
  latency_ms: number;
  checked_at: string;
  reason_code?: string | null;
}

/** 平台就绪探针返回的服务状态和受治理依赖检查结果。 */
export interface HealthResponse {
  service: string;
  status: HealthStatus;
  version: string;
  environment: string;
  checks: Record<string, HealthStatus>;
  checked_at?: string | null;
  details?: Record<string, HealthCheckDetail> | null;
}

function isHealthCheckDetail(value: unknown): value is HealthCheckDetail {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<HealthCheckDetail>;
  return (
    (candidate.status === "ok" || candidate.status === "degraded") &&
    typeof candidate.critical === "boolean" &&
    typeof candidate.latency_ms === "number" &&
    Number.isFinite(candidate.latency_ms) &&
    candidate.latency_ms >= 0 &&
    typeof candidate.checked_at === "string" &&
    (candidate.reason_code === undefined ||
      candidate.reason_code === null ||
      typeof candidate.reason_code === "string")
  );
}

function isHealthResponse(value: unknown): value is HealthResponse {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<HealthResponse>;
  const checks = candidate.checks;
  const details = candidate.details;
  return (
    typeof candidate.service === "string" &&
    (candidate.status === "ok" || candidate.status === "degraded") &&
    typeof candidate.version === "string" &&
    typeof candidate.environment === "string" &&
    Boolean(checks) &&
    typeof checks === "object" &&
    !Array.isArray(checks) &&
    Object.values(checks).every((status) => status === "ok" || status === "degraded") &&
    (candidate.checked_at === undefined ||
      candidate.checked_at === null ||
      typeof candidate.checked_at === "string") &&
    (details === undefined ||
      details === null ||
      (typeof details === "object" &&
        !Array.isArray(details) &&
        Object.values(details).every(isHealthCheckDetail)))
  );
}

/** 查询匿名就绪探针；依赖降级的 503 响应仍保留结构化健康详情。 */
export async function getPlatformHealth(): Promise<HealthResponse> {
  const response = await fetch("/api/v1/health/ready", {
    headers: { Accept: "application/json" },
  });

  const payload: unknown = await response.json().catch(() => null);
  // Readiness 使用 503 表示“进程可达但依赖未就绪”，页面必须与网络不可达明确区分。
  if (response.status === 503 && isHealthResponse(payload)) return payload;
  if (!response.ok || !isHealthResponse(payload)) {
    throw new Error(`健康检查失败：HTTP ${response.status}`);
  }
  return payload;
}
