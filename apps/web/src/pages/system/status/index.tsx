/** @description 本地运行健康与受控运营工作台页面。 */
import { useQuery } from "@tanstack/react-query";
import { Button, Tooltip } from "antd";
import { RefreshCw } from "lucide-react";

import { getPlatformHealth } from "@/api/health";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";

import { OperationsWorkbench } from "./components/OperationsWorkbench";
import { RuntimeHealthPanel } from "./components/RuntimeHealthPanel";

/** 展示本地依赖状态，并按动态菜单权限装载当前工作空间运营控制面。 */
export default function StatusPage() {
  const health = useQuery({
    queryKey: ["platform-health"],
    queryFn: getPlatformHealth,
    refetchInterval: 30_000,
  });
  const workspace = useCurrentWorkspace();
  const menu = useWorkspaceMenuNavigation();
  return (
    <>
      <PageHeader
        eyebrow="LOCAL OPERATIONS"
        title="运行状态"
        description="查看本地依赖健康，管理当前工作空间的任务恢复、索引、事件投递和数据生命周期。"
        actions={
          <Tooltip title="刷新健康状态">
            <Button
              aria-label="刷新健康状态"
              icon={<RefreshCw size={17} />}
              loading={health.isFetching}
              onClick={() => void health.refetch()}
            />
          </Tooltip>
        }
      />
      <RuntimeHealthPanel
        health={health.data}
        isLoading={health.isLoading}
        isError={health.isError}
      />
      <OperationsWorkbench
        workspaceId={workspace.workspaceId}
        workspaceName={workspace.currentWorkspace?.name ?? ""}
        permissions={menu.visiblePermissionCodes}
      />
    </>
  );
}
