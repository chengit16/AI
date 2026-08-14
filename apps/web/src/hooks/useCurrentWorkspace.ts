/** @description 当前工作空间查询 Hook，复用空间清单缓存解析选中空间。 */
import { useQuery } from "@tanstack/react-query";

import { getWorkspaces } from "@/api/services/workspaces";
import { useSessionStore } from "@/store/session";

/**
 * 返回会话选中的空间 ID、空间清单 Query 和匹配的空间摘要。
 *
 * 当前空间只是服务端空间清单的派生值，不另建客户端副本；切换空间后统一失效
 * `workspaces` Query，所有页面都会基于同一份成员关系事实重新计算。
 */
export function useCurrentWorkspace() {
  const workspaceId = useSessionStore((state) => state.workspaceId);
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: ({ signal }) => getWorkspaces(signal),
    enabled: Boolean(workspaceId),
  });
  return {
    workspaceId,
    workspaces,
    currentWorkspace: workspaces.data?.find((item) => item.workspace_id === workspaceId),
  };
}
