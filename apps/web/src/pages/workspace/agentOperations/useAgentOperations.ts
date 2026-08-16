/**
 * @description Release 运营页面的服务端状态编排
 *
 * 服务清单复用服务治理缓存；运营报告按工作空间、服务和窗口独立缓存，切换条件时不拼接旧指标。
 */
import { useQuery } from "@tanstack/react-query";

import { getAgentReleaseOperations } from "@/api/services/agentOperations";
import { getServices } from "@/api/services/services";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";

import type { OperationsWindowHours } from "./config";

/** 返回服务选择清单和当前查询条件对应的脱敏运营报告。 */
export function useAgentOperations(serviceId: string | null, windowHours: OperationsWindowHours) {
  const { workspaceId } = useCurrentWorkspace();
  const services = useQuery({
    queryKey: ["services", workspaceId],
    queryFn: ({ signal }) => getServices(workspaceId!, signal),
    enabled: Boolean(workspaceId),
    retry: false,
  });
  const report = useQuery({
    queryKey: ["agent-operations", workspaceId, serviceId, windowHours],
    queryFn: ({ signal }) =>
      getAgentReleaseOperations(workspaceId!, serviceId!, windowHours, signal),
    enabled: Boolean(workspaceId && serviceId),
    retry: false,
  });
  return { services, report };
}
