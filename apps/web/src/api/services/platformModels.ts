/**
 * @description 平台模型供应商与运行配置 API Service
 * 浏览器不读取明文凭证，所有平台管理员资格和数据政策由后端复核。
 */
import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

/** 模型供应商配置的脱敏治理视图，不包含明文 API Key。 */
export type ModelProvider = components["schemas"]["ModelProviderConfigurationResponse"];
/** 新建 OpenAI-compatible 或国内兼容供应商的请求契约。 */
export type CreateModelProviderRequest = components["schemas"]["CreateModelProviderRequest"];
/** 人工复核供应商数据保存和出境政策的请求契约。 */
export type ReviewModelProviderDataPolicyRequest =
  components["schemas"]["ReviewModelProviderDataPolicyRequest"];
/** 不可变 AI 运行配置版本及其发布状态。 */
export type AiRuntimeConfig = components["schemas"]["AiRuntimeConfigResponse"];
/** 创建主备路由、预算和超时组合的请求契约。 */
export type CreateAiRuntimeConfigRequest = components["schemas"]["CreateAiRuntimeConfigRequest"];

/** 查询当前浏览器账号的平台管理员资格，不读取供应商或运行配置。 */
export function getPlatformAdministration(signal?: AbortSignal) {
  return apiRequest<components["schemas"]["PlatformAdministrationResponse"]>(
    "/api/v1/platform/administration",
    { workspaceId: null, signal },
  );
}

/** 查询平台全部脱敏模型供应商配置。 */
export async function getPlatformModelProviders(signal?: AbortSignal) {
  const response = await apiRequest<
    components["schemas"]["ModelProviderConfigurationListResponse"]
  >("/api/v1/platform/model-providers", { workspaceId: null, signal });
  return response.items;
}

/** 创建供应商配置；凭证只在本次 TLS 请求中提交给后端加密。 */
export function createPlatformModelProvider(body: CreateModelProviderRequest) {
  return apiRequest<ModelProvider>("/api/v1/platform/model-providers", {
    method: "POST",
    workspaceId: null,
    body,
  });
}

/** 轮换指定供应商凭证，旧凭证是否失效由服务端事务保证。 */
export function rotatePlatformModelProviderCredential(providerId: string, apiKey: string) {
  return apiRequest<ModelProvider>(
    `/api/v1/platform/model-providers/${providerId}/credentials/rotate`,
    { method: "POST", workspaceId: null, body: { api_key: apiKey } },
  );
}

/** 提交供应商数据政策人工复核结论，未通过时不能激活外部路由。 */
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

/** 使用服务端固定合成提示探测供应商能力和连通性。 */
export function probePlatformModelProvider(providerId: string) {
  return mutateProvider(providerId, "probe");
}

/** 激活已通过凭证、能力和数据政策检查的供应商。 */
export function activatePlatformModelProvider(providerId: string) {
  return mutateProvider(providerId, "activate");
}

/** 禁用供应商，后续路由选择必须排除该配置。 */
export function disablePlatformModelProvider(providerId: string) {
  return mutateProvider(providerId, "disable");
}

/** 查询平台全部不可变运行配置版本。 */
export async function getPlatformAiRuntimeConfigs(signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["AiRuntimeConfigListResponse"]>(
    "/api/v1/platform/ai-runtime-configs",
    { workspaceId: null, signal },
  );
  return response.items;
}

/** 查询当前激活运行配置；尚未发布时返回空项目。 */
export async function getCurrentPlatformAiRuntimeConfig(signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["CurrentAiRuntimeConfigResponse"]>(
    "/api/v1/platform/ai-runtime-configs/current",
    { workspaceId: null, signal },
  );
  return response.item;
}

/** 创建新的不可变运行配置草稿，不自动替换当前激活版本。 */
export function createPlatformAiRuntimeConfig(body: CreateAiRuntimeConfigRequest) {
  return apiRequest<AiRuntimeConfig>("/api/v1/platform/ai-runtime-configs", {
    method: "POST",
    workspaceId: null,
    body,
  });
}

/** 原子发布指定运行配置版本并返回新的当前指针。 */
export function activatePlatformAiRuntimeConfig(runtimeConfigVersionId: string) {
  return apiRequest<components["schemas"]["AiRuntimeConfigPublicationResponse"]>(
    `/api/v1/platform/ai-runtime-configs/${runtimeConfigVersionId}/activate`,
    { method: "POST", workspaceId: null },
  );
}
