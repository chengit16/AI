/**
 * @description P6B-05 权限与审计独立产品页
 * 页面只裁剪体验入口，角色版本、字段权限、空间隔离和导出状态均由服务端执行。
 */
import { Button, Skeleton, Tabs } from "antd";
import { useState } from "react";

import { errorMessage, PlatformApiError } from "@/api/client";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";

import { AuditGovernancePanel } from "./AuditGovernancePanel";
import { RoleGovernancePanel } from "./RoleGovernancePanel";
import { useAuditFilters } from "./useAuditFilters";
import { usePermissionsAudit } from "./usePermissionsAudit";

/** 组合角色矩阵和审计台账；个人空间与策略故障都不会保留旧数据。 */
export default function PermissionsAuditPage() {
  // 1. 先恢复空间、菜单权限和 URL 状态，只为企业空间启动对应服务端查询。
  const workspace = useCurrentWorkspace();
  const menu = useWorkspaceMenuNavigation();
  const url = useAuditFilters();
  const [selectedAuditId, setSelectedAuditId] = useState<string | null>(null);
  const canReadRoles = menu.visiblePermissionCodes.has("authorization.role_permission.read");
  const canManageRoles = menu.visiblePermissionCodes.has("authorization.role_permission.manage");
  const canReadAudit = menu.visiblePermissionCodes.has("operations.records.read");
  const model = usePermissionsAudit({
    workspaceId: workspace.workspaceId,
    roleEnabled: Boolean(
      workspace.currentWorkspace?.workspace_type === "enterprise" && canReadRoles,
    ),
    auditEnabled: Boolean(
      workspace.currentWorkspace?.workspace_type === "enterprise" && canReadAudit,
    ),
    filters: url.filters,
    selectedAuditId,
  });
  // 2. 按空间、权限和查询结果逐层收敛页面状态，成功后再组合两个治理页签。
  if (workspace.workspaces.isLoading || menu.isLoading)
    return <Skeleton active paragraph={{ rows: 10 }} />;
  if (workspace.workspaces.isError || menu.error || !workspace.currentWorkspace)
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="权限事实暂时无法核验"
        description={errorMessage(workspace.workspaces.error ?? menu.error)}
      />
    );
  if (workspace.currentWorkspace.workspace_type === "personal")
    return (
      <StateView
        kind="empty"
        headingLevel={1}
        title="个人空间不启用企业权限治理"
        description="角色矩阵、影响成员和企业审计只属于企业空间。"
      />
    );
  if (!canReadRoles)
    return (
      <StateView
        kind="denied"
        headingLevel={1}
        title="当前账号没有权限治理访问权"
        description="页面入口和接口均由当前菜单发布与后端 PDP 独立控制。"
      />
    );
  const activeQuery = url.tab === "roles" ? model.governance : model.audits;
  if (activeQuery.isLoading) return <Skeleton active paragraph={{ rows: 10 }} />;
  if (activeQuery.error) {
    const denied =
      activeQuery.error instanceof PlatformApiError && activeQuery.error.code === "POLICY_DENIED";
    return (
      <StateView
        kind={denied ? "denied" : "error"}
        headingLevel={1}
        title={denied ? "当前账号没有所需治理权限" : "治理数据未能加载"}
        description={errorMessage(activeQuery.error)}
        action={
          !denied ? <Button onClick={() => void activeQuery.refetch()}>重新加载</Button> : undefined
        }
      />
    );
  }
  return (
    <>
      <PageHeader
        eyebrow="PERMISSIONS & AUDIT"
        title="权限与审计"
        description="维护企业角色的数据范围、字段遮罩与安全等级，并核查可追溯的操作事实和异步导出状态。"
      />
      <Tabs
        activeKey={url.tab}
        onChange={(key) => url.setTab(key === "audit" ? "audit" : "roles")}
        items={[
          {
            key: "roles",
            label: "角色权限",
            children: model.governance.data ? (
              <RoleGovernancePanel
                key={model.governance.data.role_version}
                snapshot={model.governance.data}
                canManage={canManageRoles}
                saving={model.saveRole.isPending}
                onSave={(roleId, version, items) =>
                  model.saveRole.mutate({ roleId, expectedRoleVersion: version, items })
                }
              />
            ) : null,
          },
          {
            key: "audit",
            label: "审计与导出",
            disabled: !canReadAudit,
            children: (
              <AuditGovernancePanel
                filters={url.filters}
                records={model.audits.data?.items ?? []}
                exports={model.exports.data ?? []}
                detail={model.auditDetail.data}
                detailLoading={model.auditDetail.isLoading}
                selectedAuditId={selectedAuditId}
                exporting={model.createExport.isPending}
                onFilter={url.setFilter}
                onReset={url.reset}
                onSelect={setSelectedAuditId}
                onExport={() => model.createExport.mutate()}
              />
            ),
          },
        ]}
      />
    </>
  );
}
