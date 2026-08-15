/**
 * @description 运营工作台查询、轮询和危险操作编排 Hook
 * 页面只决定展示与交互，服务端始终重新校验权限、确认、幂等和当前事实状态。
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";

import { errorMessage } from "@/api/client";
import { retryKnowledgeIngestionJob } from "@/api/services/knowledge";
import {
  cancelOperationsIngestionJob,
  createWorkspaceExport,
  executeWorkspaceRetention,
  getIndexMaintenanceRequests,
  getIndexMaintenanceRuns,
  getIntegrationInspection,
  getLifecycleOperations,
  getOperationsAuditRecords,
  getOperationsIngestionJobs,
  getOperationsOutboxEvents,
  getOperationsOverview,
  getUsageReconciliation,
  purgeWorkspaceBusinessData,
  replayOutboxEvent,
  requestIndexMaintenance,
  type IndexCommand,
} from "@/api/services/operations";

/** 由弹窗提交的索引维护完整命令。 */
export interface IndexCommandValues {
  command: IndexCommand;
  reasonCode: string;
  confirmation: string;
}

/** 返回工作台全部查询和受控变更，未授权时不发出运营读取请求。 */
export function useOperationsWorkbench(workspaceId: string | null, canRead: boolean) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const enabled = Boolean(workspaceId && canRead);
  const queryOptions = { enabled, refetchInterval: 5_000, refetchIntervalInBackground: true };

  // 1. 高频变化事实统一有界轮询；权限传播 Hook 失败时 canRead 立即变为 false 并停止读取。
  const overview = useQuery({
    queryKey: ["operations-overview", workspaceId],
    queryFn: ({ signal }) => getOperationsOverview(workspaceId!, signal),
    ...queryOptions,
  });
  const jobs = useQuery({
    queryKey: ["operations-ingestion-jobs", workspaceId],
    queryFn: ({ signal }) => getOperationsIngestionJobs(workspaceId!, signal),
    ...queryOptions,
    refetchInterval: 3_000,
  });
  const indexRequests = useQuery({
    queryKey: ["operations-index-requests", workspaceId],
    queryFn: ({ signal }) => getIndexMaintenanceRequests(workspaceId!, signal),
    ...queryOptions,
    refetchInterval: 3_000,
  });
  const indexRuns = useQuery({
    queryKey: ["operations-index-runs", workspaceId],
    queryFn: ({ signal }) => getIndexMaintenanceRuns(workspaceId!, signal),
    ...queryOptions,
  });
  const outbox = useQuery({
    queryKey: ["operations-outbox", workspaceId],
    queryFn: ({ signal }) => getOperationsOutboxEvents(workspaceId!, signal),
    ...queryOptions,
  });
  const integrationInspection = useQuery({
    queryKey: ["operations-integration-inspection", workspaceId],
    queryFn: ({ signal }) => getIntegrationInspection(workspaceId!, signal),
    ...queryOptions,
  });
  const audit = useQuery({
    queryKey: ["operations-audit", workspaceId],
    queryFn: ({ signal }) => getOperationsAuditRecords(workspaceId!, signal),
    enabled,
  });
  const usage = useQuery({
    queryKey: ["operations-usage", workspaceId],
    queryFn: ({ signal }) => getUsageReconciliation(workspaceId!, signal),
    enabled,
  });
  const lifecycle = useQuery({
    queryKey: ["operations-lifecycle", workspaceId],
    queryFn: ({ signal }) => getLifecycleOperations(workspaceId!, signal),
    ...queryOptions,
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({
      predicate: (query) => query.queryKey[0]?.toString().startsWith("operations-") ?? false,
    });
  };
  // 2. 所有命令成功后刷新聚合和对应事实列表；错误只展示稳定服务端文案与 Trace 语义。
  const cancelJob = useMutation({
    mutationFn: (jobId: string) => cancelOperationsIngestionJob(workspaceId!, jobId),
    onSuccess: async () => {
      await refresh();
      void message.success("入库任务已取消");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });
  const retryJob = useMutation({
    mutationFn: (jobId: string) => retryKnowledgeIngestionJob(workspaceId!, jobId),
    onSuccess: async () => {
      await refresh();
      void message.success("入库任务已重新排队");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });
  const requestIndex = useMutation({
    mutationFn: (values: IndexCommandValues) =>
      requestIndexMaintenance(workspaceId!, values.command, {
        idempotencyKey: operationKey(`index-${values.command}`),
        reasonCode: values.reasonCode,
        confirmation: values.confirmation,
      }),
    onSuccess: async () => {
      await refresh();
      void message.success("索引维护请求已登记");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });
  const replayOutbox = useMutation({
    mutationFn: (values: { eventId: string; reasonCode: string }) =>
      replayOutboxEvent(
        workspaceId!,
        values.eventId,
        operationKey("outbox-replay"),
        values.reasonCode,
      ),
    onSuccess: async () => {
      await refresh();
      void message.success("Outbox 事件已重新排队");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });
  const exportWorkspace = useMutation({
    mutationFn: () => createWorkspaceExport(workspaceId!, operationKey("workspace-export")),
    onSuccess: async () => {
      await refresh();
      void message.success("工作空间导出已完成");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });
  const executeRetention = useMutation({
    mutationFn: () => executeWorkspaceRetention(workspaceId!, operationKey("workspace-retention")),
    onSuccess: async () => {
      await refresh();
      void message.success("保留期策略已执行");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });
  const purgeWorkspace = useMutation({
    mutationFn: (values: { workspaceName: string; reasonCode: string }) =>
      purgeWorkspaceBusinessData(
        workspaceId!,
        operationKey("workspace-purge"),
        values.workspaceName,
        values.reasonCode,
      ),
    onSuccess: async () => {
      await refresh();
      void message.success("工作空间业务数据已清除");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });

  return {
    overview,
    jobs,
    indexRequests,
    indexRuns,
    outbox,
    integrationInspection,
    audit,
    usage,
    lifecycle,
    cancelJob,
    retryJob,
    requestIndex,
    replayOutbox,
    exportWorkspace,
    executeRetention,
    purgeWorkspace,
    refresh,
  };
}

/** 生成只在当前浏览器命令使用的稳定格式幂等键。 */
function operationKey(operation: string): string {
  return `p210:${operation}:${crypto.randomUUID()}`;
}
