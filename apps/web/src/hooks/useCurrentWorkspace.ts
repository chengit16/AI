import { useQuery } from "@tanstack/react-query";

import { getWorkspaces } from "@/api/services/workspaces";
import { useSessionStore } from "@/store/session";

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
