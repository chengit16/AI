import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

type LoginRequest = components["schemas"]["LoginRequest"];
type LoginResponse = components["schemas"]["LoginResponse"];
type RegistrationRequest = components["schemas"]["RegistrationRequest"];
type RegistrationResponse = components["schemas"]["RegistrationResponse"];

export function loginWithPassword(body: LoginRequest, signal?: AbortSignal) {
  return apiRequest<LoginResponse>("/api/v1/auth/login", {
    method: "POST",
    body,
    workspaceId: null,
    signal,
  });
}

export function registerPersonalAccount(body: RegistrationRequest, signal?: AbortSignal) {
  return apiRequest<RegistrationResponse>("/api/v1/auth/register", {
    method: "POST",
    body,
    workspaceId: null,
    signal,
  });
}

export function logoutCurrentSession() {
  return apiRequest<components["schemas"]["LogoutResponse"]>("/api/v1/auth/logout", {
    method: "POST",
  });
}
