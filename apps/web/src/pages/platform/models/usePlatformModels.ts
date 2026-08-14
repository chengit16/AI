import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";

import { errorMessage } from "@/api/client";
import {
  activatePlatformAiRuntimeConfig,
  activatePlatformModelProvider,
  createPlatformAiRuntimeConfig,
  createPlatformModelProvider,
  disablePlatformModelProvider,
  getCurrentPlatformAiRuntimeConfig,
  getPlatformAiRuntimeConfigs,
  probePlatformModelProvider,
  reviewPlatformModelProviderDataPolicy,
  rotatePlatformModelProviderCredential,
  type CreateAiRuntimeConfigRequest,
  type CreateModelProviderRequest,
  type ReviewModelProviderDataPolicyRequest,
} from "@/api/services/platformModels";
import { usePlatformAdministration } from "@/hooks/usePlatformAdministration";

export function usePlatformModels() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { providers } = usePlatformAdministration();
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
  const invalidateProviders = () =>
    queryClient.invalidateQueries({ queryKey: ["platform-model-providers"] });
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
    onError: notifyError,
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
