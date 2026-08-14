/** @description 账号注册、登录和当前浏览器会话退出的 API Service。 */
import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

type LoginRequest = components["schemas"]["LoginRequest"];
type LoginResponse = components["schemas"]["LoginResponse"];
type RegistrationRequest = components["schemas"]["RegistrationRequest"];
type RegistrationResponse = components["schemas"]["RegistrationResponse"];

/** 使用账号密码建立 HttpOnly Cookie 会话，不在浏览器保存长期令牌。 */
export function loginWithPassword(body: LoginRequest, signal?: AbortSignal) {
  return apiRequest<LoginResponse>("/api/v1/auth/login", {
    method: "POST",
    body,
    workspaceId: null,
    signal,
  });
}

/** 注册账号并由服务端在同一事务创建唯一默认个人空间。 */
export function registerPersonalAccount(body: RegistrationRequest, signal?: AbortSignal) {
  return apiRequest<RegistrationResponse>("/api/v1/auth/register", {
    method: "POST",
    body,
    workspaceId: null,
    signal,
  });
}

/** 撤销当前服务端会话；调用方成功后负责清理本地展示状态。 */
export function logoutCurrentSession() {
  return apiRequest<components["schemas"]["LogoutResponse"]>("/api/v1/auth/logout", {
    method: "POST",
  });
}
