/** @description 平台模型治理页面编排，组合供应商生命周期和不可变运行配置发布入口。 */
import { Alert, Button, Tabs } from "antd";
import { Cpu, Plus } from "lucide-react";
import { useState } from "react";

import { errorMessage } from "@/api/client";
import type { ModelProvider } from "@/api/services/platformModels";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";

import { ProviderDialogs } from "./components/ProviderDialogs";
import { ProviderTable } from "./components/ProviderTable";
import { RuntimeDialog } from "./components/RuntimeDialog";
import { RuntimeTable } from "./components/RuntimeTable";
import { usePlatformModels } from "./usePlatformModels";

/**
 * 展示平台级模型供应商和运行配置治理能力。
 *
 * 页面依赖平台管理员上下文改善体验，凭证、政策、探测和发布动作仍由服务端逐请求授权。
 */
export default function PlatformModelsPage() {
  const model = usePlatformModels();
  const [activeTab, setActiveTab] = useState("providers");
  const [createProviderOpen, setCreateProviderOpen] = useState(false);
  const [runtimeOpen, setRuntimeOpen] = useState(false);
  const [credentialProvider, setCredentialProvider] = useState<ModelProvider | null>(null);
  const [policyProvider, setPolicyProvider] = useState<ModelProvider | null>(null);
  const providerMutating =
    model.createProvider.isPending ||
    model.rotateCredential.isPending ||
    model.reviewPolicy.isPending ||
    model.providerAction.isPending;
  const activeProviders = model.providers.data?.filter((item) => item.status === "active") ?? [];

  if (model.providers.isError) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="模型治理数据未能加载"
        description={errorMessage(model.providers.error)}
        action={<Button onClick={() => void model.providers.refetch()}>重新加载</Button>}
      />
    );
  }

  return (
    <>
      <PageHeader
        eyebrow="PLATFORM GOVERNANCE"
        title="模型与运行配置"
        description="集中管理供应商凭证、数据政策、能力探测和不可变主备运行配置。"
        actions={
          activeTab === "providers" ? (
            <Button
              type="primary"
              icon={<Plus size={17} />}
              onClick={() => setCreateProviderOpen(true)}
            >
              添加供应商
            </Button>
          ) : (
            <Button
              type="primary"
              icon={<Cpu size={17} />}
              disabled={activeProviders.length === 0}
              onClick={() => setRuntimeOpen(true)}
            >
              创建运行配置
            </Button>
          )
        }
      />
      <section
        className="ui-surface-panel overflow-hidden px-5 pb-5 form-down:px-3 form-down:pb-3"
        aria-label="平台模型治理"
      >
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: "providers",
              label: `模型供应商 ${model.providers.data?.length ?? 0}`,
              children: (
                <ProviderTable
                  items={model.providers.data ?? []}
                  isLoading={model.providers.isLoading}
                  isMutating={providerMutating}
                  onRotateCredential={setCredentialProvider}
                  onReviewPolicy={setPolicyProvider}
                  onAction={(providerId, action) =>
                    model.providerAction.mutate({ providerId, action })
                  }
                />
              ),
            },
            {
              key: "runtime",
              label: `运行配置 ${model.runtimeConfigs.data?.length ?? 0}`,
              children: model.runtimeConfigs.isError ? (
                <StateView
                  kind="error"
                  title="运行配置未能加载"
                  description={errorMessage(model.runtimeConfigs.error)}
                />
              ) : (
                <div className="grid gap-4">
                  {model.currentRuntime.isError && (
                    <Alert
                      type="warning"
                      showIcon
                      message="当前发布状态未能加载"
                      description="已创建的配置仍可查看；可重试读取，或重新发布目标版本以恢复当前指针。"
                      action={
                        <Button size="small" onClick={() => void model.currentRuntime.refetch()}>
                          重试发布状态
                        </Button>
                      }
                    />
                  )}
                  <RuntimeTable
                    items={model.runtimeConfigs.data ?? []}
                    providers={model.providers.data ?? []}
                    currentId={model.currentRuntime.data?.runtime_config_version_id ?? null}
                    isLoading={model.runtimeConfigs.isLoading}
                    isActivating={model.activateRuntime.isPending}
                    onActivate={(runtimeConfigVersionId) =>
                      model.activateRuntime.mutate(runtimeConfigVersionId)
                    }
                  />
                </div>
              ),
            },
          ]}
        />
      </section>
      <ProviderDialogs
        createOpen={createProviderOpen}
        credentialProvider={credentialProvider}
        policyProvider={policyProvider}
        isSubmitting={providerMutating}
        onCloseCreate={() => setCreateProviderOpen(false)}
        onCloseCredential={() => setCredentialProvider(null)}
        onClosePolicy={() => setPolicyProvider(null)}
        onCreate={model.createProvider.mutateAsync}
        onRotateCredential={(providerId, apiKey) =>
          model.rotateCredential.mutateAsync({ providerId, apiKey })
        }
        onReviewPolicy={(providerId, body) => model.reviewPolicy.mutateAsync({ providerId, body })}
      />
      <RuntimeDialog
        open={runtimeOpen}
        providers={activeProviders}
        isSubmitting={model.createRuntime.isPending}
        onClose={() => setRuntimeOpen(false)}
        onCreate={model.createRuntime.mutateAsync}
      />
    </>
  );
}
