import { Button, Tabs } from "antd";
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
import styles from "./PlatformModelsPage.module.css";
import { usePlatformModels } from "./usePlatformModels";

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
      <section className={styles.tableSection} aria-label="平台模型治理">
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
              children:
                model.runtimeConfigs.isError || model.currentRuntime.isError ? (
                  <StateView
                    kind="error"
                    title="运行配置未能加载"
                    description={errorMessage(
                      model.runtimeConfigs.error ?? model.currentRuntime.error,
                    )}
                  />
                ) : (
                  <RuntimeTable
                    items={model.runtimeConfigs.data ?? []}
                    currentId={model.currentRuntime.data?.runtime_config_version_id ?? null}
                    isLoading={model.runtimeConfigs.isLoading || model.currentRuntime.isLoading}
                    isActivating={model.activateRuntime.isPending}
                    onActivate={(runtimeConfigVersionId) =>
                      model.activateRuntime.mutate(runtimeConfigVersionId)
                    }
                  />
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
