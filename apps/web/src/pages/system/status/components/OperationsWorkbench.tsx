/** @description 运营工作台摘要、任务和治理 Tabs 编排组件。 */
import { Button, Tabs } from "antd";
import { RefreshCw } from "lucide-react";

import { StateView } from "@/components/StateView/StateView";

import { useOperationsWorkbench } from "../useOperationsWorkbench";
import { AuditUsagePanel } from "./AuditUsagePanel";
import { IndexOperationsPanel } from "./IndexOperationsPanel";
import { IngestionOperationsPanel } from "./IngestionOperationsPanel";
import { LifecycleOperationsPanel } from "./LifecycleOperationsPanel";
import { OperationsSummary } from "./OperationsSummary";
import { OutboxOperationsPanel } from "./OutboxOperationsPanel";

interface OperationsWorkbenchProps {
  /** 所有运营请求必须绑定的当前可信工作空间。 */
  workspaceId: string | null;
  /** 生命周期清除确认使用的当前空间展示名称。 */
  workspaceName: string;
  /** 当前菜单发布快照计算出的动作权限集合。 */
  permissions: ReadonlySet<string>;
}

/** 聚合已登记 API；权限不可用时清空工作台内容，按钮隐藏不替代服务端 PDP。 */
export function OperationsWorkbench(props: OperationsWorkbenchProps) {
  const canRead = props.permissions.has("operations.records.read");
  const model = useOperationsWorkbench(props.workspaceId, canRead);
  if (!canRead) {
    return (
      <section className="mt-6" aria-labelledby="operations-title">
        <h2 id="operations-title" className="mb-4 mt-0 text-[19px]">
          运营工作台
        </h2>
        <StateView
          kind="denied"
          title="当前角色没有运营记录权限"
          description="菜单权限变更传播后，页面会重新计算可见入口；所有接口仍由后端独立授权。"
        />
      </section>
    );
  }
  const queries = [
    model.overview,
    model.jobs,
    model.indexRequests,
    model.indexRuns,
    model.outbox,
    model.integrationInspection,
    model.audit,
    model.usage,
    model.lifecycle,
  ];
  const hasError = queries.some((query) => query.isError);
  const isFetching = queries.some((query) => query.isFetching);
  return (
    <section className="mt-8" aria-labelledby="operations-title">
      <div className="mb-4 flex items-center justify-between gap-4 nav-mobile:items-start">
        <div>
          <h2 id="operations-title" className="m-0 text-[19px]">
            运营工作台
          </h2>
          <p className="mb-0 mt-2 text-sm text-text-muted">
            管理任务恢复、索引维护、事件投递和数据生命周期。
          </p>
        </div>
        <Button
          aria-label="刷新运营工作台"
          icon={<RefreshCw size={16} />}
          loading={isFetching}
          onClick={() => void model.refresh()}
        >
          刷新
        </Button>
      </div>
      {hasError ? (
        <StateView
          kind="error"
          title="运营数据暂时无法加载"
          description="旧数据不会继续显示，请恢复服务或权限后重新加载。"
          action={<Button onClick={() => void model.refresh()}>重新加载</Button>}
        />
      ) : (
        <>
          <OperationsSummary overview={model.overview.data} isLoading={model.overview.isLoading} />
          <div className="ui-surface-panel mt-5 px-5 pb-5 nav-mobile:px-3">
            <Tabs
              items={[
                {
                  key: "ingestion",
                  label: `任务 ${model.jobs.data?.length ?? 0}`,
                  children: (
                    <IngestionOperationsPanel
                      items={model.jobs.data ?? []}
                      isLoading={model.jobs.isLoading}
                      canCancel={props.permissions.has("knowledge.ingestion.cancel")}
                      canRetry={props.permissions.has("knowledge.ingestion.retry")}
                      isMutating={model.cancelJob.isPending || model.retryJob.isPending}
                      onCancel={(jobId) => model.cancelJob.mutate(jobId)}
                      onRetry={(jobId) => model.retryJob.mutate(jobId)}
                    />
                  ),
                },
                {
                  key: "index",
                  label: `索引 ${model.indexRequests.data?.length ?? 0}`,
                  children: (
                    <IndexOperationsPanel
                      requests={model.indexRequests.data ?? []}
                      runs={model.indexRuns.data ?? []}
                      isLoading={model.indexRequests.isLoading || model.indexRuns.isLoading}
                      isSubmitting={model.requestIndex.isPending}
                      permissions={props.permissions}
                      onSubmit={model.requestIndex.mutateAsync}
                    />
                  ),
                },
                {
                  key: "outbox",
                  label: `Outbox ${model.outbox.data?.length ?? 0}`,
                  children: (
                    <OutboxOperationsPanel
                      items={model.outbox.data ?? []}
                      inspection={model.integrationInspection.data}
                      isLoading={model.outbox.isLoading || model.integrationInspection.isLoading}
                      canReplay={props.permissions.has("operations.outbox.replay")}
                      isReplaying={model.replayOutbox.isPending}
                      onReplay={model.replayOutbox.mutateAsync}
                    />
                  ),
                },
                {
                  key: "audit",
                  label: "审计与用量",
                  children: (
                    <AuditUsagePanel
                      audit={model.audit.data ?? []}
                      usage={model.usage.data ?? []}
                      isLoading={model.audit.isLoading || model.usage.isLoading}
                    />
                  ),
                },
                {
                  key: "lifecycle",
                  label: `生命周期 ${model.lifecycle.data?.length ?? 0}`,
                  children: (
                    <LifecycleOperationsPanel
                      workspaceName={props.workspaceName}
                      items={model.lifecycle.data ?? []}
                      isLoading={model.lifecycle.isLoading}
                      permissions={props.permissions}
                      isMutating={
                        model.exportWorkspace.isPending ||
                        model.executeRetention.isPending ||
                        model.purgeWorkspace.isPending
                      }
                      onExport={() => model.exportWorkspace.mutate()}
                      onRetention={() => model.executeRetention.mutate()}
                      onPurge={model.purgeWorkspace.mutateAsync}
                    />
                  ),
                },
              ]}
            />
          </div>
        </>
      )}
    </section>
  );
}
