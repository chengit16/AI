/** @description 工具执行控制台的目录、服务、计划与创建命令编排。 */
import { useMutation, useQuery } from "@tanstack/react-query";
import { App } from "antd";

import { errorMessage } from "@/api/client";
import { createToolRun, getAvailableTools, type CreateToolRunRequest } from "@/api/services/tools";
import { getServices } from "@/api/services/services";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";

/** 返回控制台只读事实与单一创建命令。 */
export function useToolExecutionConsole() {
  const { message } = App.useApp();
  const { workspaceId } = useCurrentWorkspace();
  const tools = useQuery({
    queryKey: ["available-tools", workspaceId],
    queryFn: ({ signal }) => getAvailableTools(workspaceId!, signal),
    enabled: Boolean(workspaceId),
    retry: false,
  });
  const services = useQuery({
    queryKey: ["services", workspaceId],
    queryFn: ({ signal }) => getServices(workspaceId!, signal),
    enabled: Boolean(workspaceId),
    retry: false,
  });
  const create = useMutation({
    mutationFn: (body: CreateToolRunRequest) => createToolRun(workspaceId!, body),
    onSuccess: () => void message.success("工具计划已冻结并进入执行队列"),
    onError: (error) => void message.error(errorMessage(error)),
  });
  return { tools, services, create };
}
