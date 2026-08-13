import type { components } from "@/api/generated/platform-api.v1";

type ErrorResponse = components["schemas"]["ErrorResponse"];
type MutationMethod = "POST" | "PUT" | "PATCH" | "DELETE";

interface ApiSessionContext {
  workspaceId: string | null;
  csrfToken: string | null;
}

let getApiSession = (): ApiSessionContext => ({ workspaceId: null, csrfToken: null });
let handleUnauthorized: () => void = () => undefined;

/** 应用层注入会话读取器，API 层不反向依赖 Zustand 或路由实现。 */
export function configureApiClient(
  sessionProvider: () => ApiSessionContext,
  unauthorizedHandler: () => void,
) {
  getApiSession = sessionProvider;
  handleUnauthorized = unauthorizedHandler;
}

export class PlatformApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly retryable: boolean,
    public readonly traceId?: string,
  ) {
    super(message);
    this.name = "PlatformApiError";
  }
}

interface RequestOptions extends Omit<RequestInit, "body" | "method"> {
  method?: "GET" | MutationMethod;
  body?: unknown;
  workspaceId?: string | null;
}

function isErrorResponse(value: unknown): value is ErrorResponse {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return typeof item.code === "string" && typeof item.message === "string";
}

/** 统一附加工作空间、CSRF 与同源 Cookie，并保留后端稳定错误码供页面判断。 */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const session = getApiSession();
  const method = options.method ?? "GET";
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  const workspaceId = options.workspaceId === undefined ? session.workspaceId : options.workspaceId;
  if (workspaceId) headers.set("X-Workspace-ID", workspaceId);
  if (method !== "GET" && session.csrfToken) {
    headers.set("X-CSRF-Token", session.csrfToken);
  }
  if (options.body !== undefined) headers.set("Content-Type", "application/json");

  const response = await fetch(path, {
    ...options,
    method,
    headers,
    credentials: "same-origin",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const contentType = response.headers.get("content-type") ?? "";
  const payload: unknown = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    if (response.status === 401) handleUnauthorized();
    if (isErrorResponse(payload)) {
      throw new PlatformApiError(
        response.status,
        payload.code,
        payload.message,
        payload.retryable,
        payload.trace_id,
      );
    }
    throw new PlatformApiError(
      response.status,
      "HTTP_ERROR",
      `请求失败：HTTP ${response.status}`,
      false,
    );
  }
  return payload as T;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "请求未完成，请稍后重试";
}
