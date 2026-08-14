/**
 * @description 浏览器统一 API Client
 * 负责会话 Cookie、CSRF、工作空间上下文和稳定错误转换，不承担业务授权判断。
 */
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

/** 后端稳定错误响应，保留错误码、重试语义和 Trace 标识供页面恢复。 */
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
  /** 允许的只读或变更请求方法，默认使用 `GET`。 */
  method?: "GET" | MutationMethod;
  /** 由 Client 统一序列化的 JSON 数据或保持原样发送的 `FormData`。 */
  body?: unknown;
  /** 显式覆盖会话空间；传入 `null` 用于平台级和认证接口。 */
  workspaceId?: string | null;
}

function isErrorResponse(value: unknown): value is ErrorResponse {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return typeof item.code === "string" && typeof item.message === "string";
}

/** 统一附加工作空间、CSRF 与同源 Cookie，并保留后端稳定错误码供页面判断。 */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  // 1. 从会话生成可信请求头；平台接口可显式清空空间，但业务调用默认继承当前空间。
  const session = getApiSession();
  const method = options.method ?? "GET";
  const headers = new Headers(options.headers);
  const isMultipart = options.body instanceof FormData;
  headers.set("Accept", "application/json");
  const workspaceId = options.workspaceId === undefined ? session.workspaceId : options.workspaceId;
  if (workspaceId) headers.set("X-Workspace-ID", workspaceId);
  if (method !== "GET" && session.csrfToken) {
    headers.set("X-CSRF-Token", session.csrfToken);
  }
  if (options.body !== undefined && !isMultipart) headers.set("Content-Type", "application/json");
  const requestBody: BodyInit | undefined =
    options.body === undefined
      ? undefined
      : options.body instanceof FormData
        ? options.body
        : JSON.stringify(options.body);

  // 2. 统一解析后端错误契约；401 先清理失效会话，其他错误保留稳定错误码供页面分流。
  const response = await fetch(path, {
    ...options,
    method,
    headers,
    credentials: "same-origin",
    body: requestBody,
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

/** 把未知请求失败收敛为可展示文案，不向界面泄露原始响应结构。 */
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "请求未完成，请稍后重试";
}
