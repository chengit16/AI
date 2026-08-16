/**
 * @description 服务发布页面编排
 *
 * 负责 URL 服务选择、动态菜单权限、策略弹窗和版本化 Route 命令组合；
 * 服务、Route、generation 和 Release 选项始终以服务端查询为事实来源。
 */
import { Button } from "antd";
import { Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import { errorMessage } from "@/api/client";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";

import { ServiceCanaryDialog } from "./components/ServiceCanaryDialog";
import { ServiceCreateDialog } from "./components/ServiceCreateDialog";
import { ServicePolicyDialog } from "./components/ServicePolicyDialog";
import { ServiceRail } from "./components/ServiceRail";
import { ServiceRoutePanel } from "./components/ServiceRoutePanel";
import { useServiceManagement } from "./useServiceManagement";

/** 展示服务定义、受众策略、灰度、晋级和一键回滚。 */
export default function ServiceManagementPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("service");
  const [createOpen, setCreateOpen] = useState(false);
  const [policyOpen, setPolicyOpen] = useState(false);
  const [canaryOpen, setCanaryOpen] = useState(false);
  const model = useServiceManagement(selectedId);
  const { visiblePermissionCodes } = useWorkspaceMenuNavigation();
  const has = (code: string) => visiblePermissionCodes.has(code);

  // 1. 服务选择进入 URL；查询失败时清空本地派生列表，不能继续展示旧 Route。
  const services = useMemo(
    () => (model.services.isError ? [] : (model.services.data ?? [])),
    [model.services.data, model.services.isError],
  );
  const selected = services.find((item) => item.service.service_id === selectedId) ?? null;
  const selectService = (serviceId: string) => {
    const next = new URLSearchParams(searchParams);
    next.set("service", serviceId);
    setSearchParams(next);
  };
  useEffect(() => {
    if (!selectedId && services[0]) {
      setSearchParams({ service: services[0].service.service_id }, { replace: true });
    }
  }, [selectedId, services, setSearchParams]);

  if (model.services.isError) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="服务发布页面未能加载"
        description={errorMessage(model.services.error)}
        action={<Button onClick={() => void model.services.refetch()}>重新加载</Button>}
      />
    );
  }

  // 2. 当前详情只消费同一次服务聚合响应，避免策略、Route 与 generation 跨版本拼接。
  const serviceArea = selected ? (
    <ServiceRoutePanel
      deployment={selected}
      canUpdate={has("service.definition.update")}
      canCanary={has("service.route.canary")}
      canPromote={has("service.route.promote")}
      canRollback={has("service.route.rollback")}
      isMutating={
        model.update.isPending ||
        model.startCanary.isPending ||
        model.promote.isPending ||
        model.rollback.isPending
      }
      onEdit={() => setPolicyOpen(true)}
      onCanary={() => setCanaryOpen(true)}
      onUpdate={(body) => model.update.mutate(body)}
      onPromote={(input) => model.promote.mutate(input)}
      onRollback={(generation) => model.rollback.mutate(generation)}
    />
  ) : (
    <StateView
      kind="empty"
      title="选择或创建一个服务"
      description="服务以稳定 Service ID 路由到不可变 Agent Release。"
    />
  );

  // 3. 按菜单动作权限组合入口；按钮隐藏只改善体验，接口仍由后端统一授权。
  return (
    <>
      <PageHeader
        eyebrow="SERVICE DELIVERY"
        title="服务发布"
        description="管理稳定服务身份、访问策略、版本化 Route、灰度晋级和一键回滚。"
        actions={
          has("service.definition.create") ? (
            <Button type="primary" icon={<Plus size={17} />} onClick={() => setCreateOpen(true)}>
              创建服务
            </Button>
          ) : undefined
        }
      />
      <div className="ui-surface-panel grid min-h-[680px] grid-cols-[minmax(220px,280px)_minmax(0,1fr)] overflow-hidden tablet-down:grid-cols-1">
        <ServiceRail
          items={services}
          selectedId={selectedId}
          isLoading={model.services.isLoading}
          onSelect={selectService}
        />
        <section className="min-w-0 p-5 phone-down:p-3" aria-label="服务当前视图">
          {serviceArea}
        </section>
      </div>
      <ServiceCreateDialog
        open={createOpen}
        releaseOptions={model.releaseOptions}
        isReleaseOptionsLoading={model.isReleaseOptionsLoading}
        isReleaseOptionsError={model.isReleaseOptionsError}
        isCreating={model.create.isPending}
        onClose={() => setCreateOpen(false)}
        onCreate={async (body) => {
          const created = await model.create.mutateAsync(body);
          selectService(created.service.service_id);
        }}
      />
      {selected && (
        <>
          <ServicePolicyDialog
            open={policyOpen}
            deployment={selected}
            isUpdating={model.update.isPending}
            onClose={() => setPolicyOpen(false)}
            onUpdate={model.update.mutateAsync}
          />
          <ServiceCanaryDialog
            open={canaryOpen}
            deployment={selected}
            releaseOptions={model.releaseOptions}
            isReleaseOptionsError={model.isReleaseOptionsError}
            isSubmitting={model.startCanary.isPending}
            onClose={() => setCanaryOpen(false)}
            onSubmit={model.startCanary.mutateAsync}
          />
        </>
      )}
    </>
  );
}
