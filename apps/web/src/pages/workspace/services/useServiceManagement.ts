/**
 * @description 服务发布页面服务端状态编排
 *
 * 负责 Service、访问策略、Route 和 Release 选项的查询与命令刷新；
 * 不在浏览器推算路由版本，也不在查询失败后继续暴露旧 Route 事实。
 */
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";

import { errorMessage } from "@/api/client";
import { getAgentReleases, getAgents, type AgentRelease } from "@/api/services/agents";
import {
  createService,
  getServices,
  promoteServiceRoute,
  rollbackServiceRoute,
  startServiceCanary,
  updateService,
  type CreateServiceRequest,
  type UpdateServiceRequest,
} from "@/api/services/services";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";

/** 服务创建和灰度选择器使用的已发布版本。 */
export interface ServiceReleaseOption {
  /** Release 所属 Agent ID。 */
  agentId: string;
  /** Release 所属 Agent 展示名称。 */
  agentName: string;
  /** 不可变 Release 摘要。 */
  release: AgentRelease;
}

/** 启动灰度所需的当前 generation 与目标版本。 */
export interface StartCanaryInput {
  /** 目标不可变 Release ID。 */
  releaseId: string;
  /** 固定到 1～99 的灰度百分比。 */
  canaryPercent: number;
  /** 当前发布指针 generation。 */
  expectedGeneration: number;
}

/** 晋级路由所需的目标版本和乐观锁。 */
export interface PromoteRouteInput {
  /** 要晋级为唯一正式版本的 Release ID。 */
  releaseId: string;
  /** 当前发布指针 generation。 */
  expectedGeneration: number;
}

/**
 * 返回服务治理查询、Release 选项和全部版本化路由命令。
 *
 * Release 选项按当前 Agent 列表动态查询；任一选项查询失败时整体清空，避免创建或
 * 灰度弹窗混用部分陈旧版本。所有服务写命令成功后只刷新当前空间服务列表。
 */
export function useServiceManagement(serviceId: string | null) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { workspaceId } = useCurrentWorkspace();

  // 1. 服务列表包含当前策略和 Route；Agent/Release 只为创建与灰度目标选择提供只读选项。
  const services = useQuery({
    queryKey: ["services", workspaceId],
    queryFn: ({ signal }) => getServices(workspaceId!, signal),
    enabled: Boolean(workspaceId),
    retry: false,
  });
  const agents = useQuery({
    queryKey: ["agents", workspaceId],
    queryFn: ({ signal }) => getAgents(workspaceId!, signal),
    enabled: Boolean(workspaceId),
    retry: false,
  });
  const agentItems = agents.isError ? [] : (agents.data ?? []);
  const releaseQueries = useQueries({
    queries: agentItems.map((item) => ({
      queryKey: ["agent-releases", workspaceId, item.agent.agent_id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        getAgentReleases(workspaceId!, item.agent.agent_id, signal),
      enabled: Boolean(workspaceId),
      retry: false,
    })),
  });
  const isReleaseOptionsError = agents.isError || releaseQueries.some((query) => query.isError);
  const isReleaseOptionsLoading =
    agents.isLoading || releaseQueries.some((query) => query.isLoading);
  const releaseOptions: ServiceReleaseOption[] = isReleaseOptionsError
    ? []
    : releaseQueries.flatMap((query, index) =>
        (query.data ?? []).map((release) => ({
          agentId: agentItems[index].agent.agent_id,
          agentName: agentItems[index].agent.name,
          release,
        })),
      );

  // 2. Route、策略和服务状态在同一列表聚合中返回，任一命令完成后整体刷新该事实。
  const refreshServices = () =>
    queryClient.invalidateQueries({ queryKey: ["services", workspaceId] });
  const notifyError = (error: unknown) => void message.error(errorMessage(error));
  const create = useMutation({
    mutationFn: (body: CreateServiceRequest) => createService(workspaceId!, body),
    onSuccess: async () => {
      await refreshServices();
      void message.success("服务与首个正式 Route 已创建");
    },
    onError: notifyError,
  });
  const update = useMutation({
    mutationFn: (body: UpdateServiceRequest) => updateService(workspaceId!, serviceId!, body),
    onSuccess: async () => {
      await refreshServices();
      void message.success("服务定义或访问策略已更新");
    },
    onError: notifyError,
  });

  // 3. 三类路由命令必须携带服务端当前 generation，并发竞争失败时由后端拒绝。
  const startCanary = useMutation({
    mutationFn: (input: StartCanaryInput) =>
      startServiceCanary(
        workspaceId!,
        serviceId!,
        input.releaseId,
        input.canaryPercent,
        input.expectedGeneration,
      ),
    onSuccess: async () => {
      await refreshServices();
      void message.success("灰度 Route 已生效");
    },
    onError: notifyError,
  });
  const promote = useMutation({
    mutationFn: (input: PromoteRouteInput) =>
      promoteServiceRoute(workspaceId!, serviceId!, input.releaseId, input.expectedGeneration),
    onSuccess: async () => {
      await refreshServices();
      void message.success("灰度版本已晋级为正式版本");
    },
    onError: notifyError,
  });
  const rollback = useMutation({
    mutationFn: (expectedGeneration: number) =>
      rollbackServiceRoute(workspaceId!, serviceId!, expectedGeneration),
    onSuccess: async () => {
      await refreshServices();
      void message.success("服务已回滚到最近稳定版本");
    },
    onError: notifyError,
  });

  return {
    services,
    releaseOptions,
    isReleaseOptionsError,
    isReleaseOptionsLoading,
    create,
    update,
    startCanary,
    promote,
    rollback,
  };
}
