import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

export type ModelProvider = components["schemas"]["ModelProviderConfigurationResponse"];
export type CreateModelProviderRequest = components["schemas"]["CreateModelProviderRequest"];
export type ReviewModelProviderDataPolicyRequest =
  components["schemas"]["ReviewModelProviderDataPolicyRequest"];
export type AiRuntimeConfig = components["schemas"]["AiRuntimeConfigResponse"];
export type CreateAiRuntimeConfigRequest = components["schemas"]["CreateAiRuntimeConfigRequest"];

export async function getPlatformModelProviders(signal?: AbortSignal) {
  const response = await apiRequest<
    components["schemas"]["ModelProviderConfigurationListResponse"]
  >("/api/v1/platform/model-providers", { workspaceId: null, signal });
  return response.items;
}

export function createPlatformModelProvider(body: CreateModelProviderRequest) {
  return apiRequest<ModelProvider>("/api/v1/platform/model-providers", {
    method: "POST",
    workspaceId: null,
    body,
  });
}

export function rotatePlatformModelProviderCredential(providerId: string, apiKey: string) {
  return apiRequest<ModelProvider>(
    `/api/v1/platform/model-providers/${providerId}/credentials/rotate`,
    { method: "POST", workspaceId: null, body: { api_key: apiKey } },
  );
}

export function reviewPlatformModelProviderDataPolicy(
  providerId: string,
  body: ReviewModelProviderDataPolicyRequest,
) {
  return apiRequest<ModelProvider>(
    `/api/v1/platform/model-providers/${providerId}/data-policy/review`,
    { method: "POST", workspaceId: null, body },
  );
}

function mutateProvider(providerId: string, action: "probe" | "activate" | "disable") {
  return apiRequest<ModelProvider>(`/api/v1/platform/model-providers/${providerId}/${action}`, {
    method: "POST",
    workspaceId: null,
  });
}

export function probePlatformModelProvider(providerId: string) {
  return mutateProvider(providerId, "probe");
}

export function activatePlatformModelProvider(providerId: string) {
  return mutateProvider(providerId, "activate");
}

export function disablePlatformModelProvider(providerId: string) {
  return mutateProvider(providerId, "disable");
}

export async function getPlatformAiRuntimeConfigs(signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["AiRuntimeConfigListResponse"]>(
    "/api/v1/platform/ai-runtime-configs",
    { workspaceId: null, signal },
  );
  return response.items;
}

export async function getCurrentPlatformAiRuntimeConfig(signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["CurrentAiRuntimeConfigResponse"]>(
    "/api/v1/platform/ai-runtime-configs/current",
    { workspaceId: null, signal },
  );
  return response.item;
}

export function createPlatformAiRuntimeConfig(body: CreateAiRuntimeConfigRequest) {
  return apiRequest<AiRuntimeConfig>("/api/v1/platform/ai-runtime-configs", {
    method: "POST",
    workspaceId: null,
    body,
  });
}

export function activatePlatformAiRuntimeConfig(runtimeConfigVersionId: string) {
  return apiRequest<components["schemas"]["AiRuntimeConfigPublicationResponse"]>(
    `/api/v1/platform/ai-runtime-configs/${runtimeConfigVersionId}/activate`,
    { method: "POST", workspaceId: null },
  );
}
