/**
 * @description 平台模型治理业务 Hook
 * 组合供应商和运行配置 Query/Mutation，并集中维护跨列表缓存刷新。
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";

import { errorMessage, PlatformApiError } from "@/api/client";
import {
  activatePlatformAiRuntimeConfig,
  activatePlatformModelProvider,
  createPlatformAiRuntimeConfig,
  createPlatformModelProvider,
  disablePlatformModelProvider,
  getCurrentPlatformAiRuntimeConfig,
  getPlatformAiRuntimeConfigs,
  probePlatformModelProvider,
  getPlatformModelProviders,
  reviewPlatformModelProviderDataPolicy,
  rotatePlatformModelProviderCredential,
  type CreateAiRuntimeConfigRequest,
  type CreateModelProviderRequest,
  type ReviewModelProviderDataPolicyRequest,
} from "@/api/services/platformModels";
import { useSessionStore } from "@/store/session";

/**
 * 返回模型供应商和运行配置的查询、写操作及统一加载状态。
 *
 * 供应商变化只刷新供应商治理视图；运行配置创建或发布会同时刷新版本清单和当前指针，
 * 防止页面显示已发布版本与详情列表不一致。
 */
export function usePlatformModels() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const accountId = useSessionStore((state) => state.accountId);
  // 1. 进入管理员页面后才读取治理业务数据；全局壳层只消费独立的低敏资格接口。
  const providers = useQuery({
    queryKey: ["platform-model-providers", accountId],
    queryFn: ({ signal }) => getPlatformModelProviders(signal),
    enabled: Boolean(accountId),
    retry: false,
  });
  const runtimeConfigs = useQuery({
    queryKey: ["platform-ai-runtime-configs"],
    queryFn: ({ signal }) => getPlatformAiRuntimeConfigs(signal),
    retry: false,
  });
  const currentRuntime = useQuery({
    queryKey: ["platform-ai-runtime-config-current"],
    queryFn: ({ signal }) => getCurrentPlatformAiRuntimeConfig(signal),
    retry: false,
  });
  // 2. 写操作按供应商和运行配置两类事实集中失效，避免版本清单与当前指针局部陈旧。
  const invalidateProviders = () =>
    queryClient.invalidateQueries({ queryKey: ["platform-model-providers"] });
  // 运行配置写操作同时影响版本清单和当前指针，两份缓存必须作为同一展示事实刷新。
  const invalidateRuntime = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["platform-ai-runtime-configs"] }),
      queryClient.invalidateQueries({ queryKey: ["platform-ai-runtime-config-current"] }),
    ]);
  const notifyError = (error: unknown) => void message.error(errorMessage(error));
  const createProvider = useMutation({
    mutationFn: (body: CreateModelProviderRequest) => createPlatformModelProvider(body),
    onSuccess: async () => {
      await invalidateProviders();
      void message.success("模型供应商已创建，凭证仅保存加密事实");
    },
    onError: (error) => {
      const messageText =
        error instanceof PlatformApiError && error.code === "MODEL_PROVIDER_CONFIGURATION_INVALID"
          ? "供应商配置无效：请确认 Base URL 域名已加入 MODEL_PROVIDER_ALLOWED_HOSTS，并选择与中转一致的调用协议"
          : errorMessage(error);
      void message.error(messageText);
    },
  });
  const rotateCredential = useMutation({
    mutationFn: ({ providerId, apiKey }: { providerId: string; apiKey: string }) =>
      rotatePlatformModelProviderCredential(providerId, apiKey),
    onSuccess: async () => {
      await invalidateProviders();
      void message.success("模型凭证已轮换");
    },
    onError: notifyError,
  });
  const reviewPolicy = useMutation({
    mutationFn: ({
      providerId,
      body,
    }: {
      providerId: string;
      body: ReviewModelProviderDataPolicyRequest;
    }) => reviewPlatformModelProviderDataPolicy(providerId, body),
    onSuccess: async () => {
      await invalidateProviders();
      void message.success("数据政策审核结果已记录");
    },
    onError: notifyError,
  });
  const providerAction = useMutation({
    mutationFn: ({
      providerId,
      action,
    }: {
      providerId: string;
      action: "probe" | "activate" | "disable";
    }) => {
      if (action === "probe") return probePlatformModelProvider(providerId);
      if (action === "activate") return activatePlatformModelProvider(providerId);
      return disablePlatformModelProvider(providerId);
    },
    onSuccess: async (_, values) => {
      await invalidateProviders();
      const labels = { probe: "能力探测已完成", activate: "供应商已启用", disable: "供应商已停用" };
      void message.success(labels[values.action]);
    },
    onError: notifyError,
  });
  const createRuntime = useMutation({
    mutationFn: (body: CreateAiRuntimeConfigRequest) => createPlatformAiRuntimeConfig(body),
    onSuccess: async () => {
      await invalidateRuntime();
      void message.success("不可变运行配置版本已创建");
    },
    onError: notifyError,
  });
  const activateRuntime = useMutation({
    mutationFn: activatePlatformAiRuntimeConfig,
    onSuccess: async () => {
      await invalidateRuntime();
      void message.success("当前运行配置已切换");
    },
    onError: notifyError,
  });

  return {
    providers,
    runtimeConfigs,
    currentRuntime,
    createProvider,
    rotateCredential,
    reviewPolicy,
    providerAction,
    createRuntime,
    activateRuntime,
  };
}
