/** @description 权限矩阵、审计查询和异步导出的服务端状态编排。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";

import { errorMessage, PlatformApiError } from "@/api/client";
import {
  createAuditExport,
  getAuditRecord,
  getRoleGovernance,
  listAuditExports,
  listAuditRecords,
  replaceRolePermissions,
  type AuditFilters,
  type RoleGrant,
} from "@/api/services/permissionsAudit";

interface Options {
  /** 当前可信工作空间标识；空间未恢复时不启动请求。 */
  workspaceId: string | null;
  /** 是否允许读取角色治理聚合。 */
  roleEnabled: boolean;
  /** 是否允许读取审计和导出事实。 */
  auditEnabled: boolean;
  /** 已由 URL 规范化的审计筛选条件。 */
  filters: AuditFilters;
  /** 当前需要读取详情的审计标识。 */
  selectedAuditId: string | null;
}

/** 策略传播故障只做有限重试；其余错误立即进入可解释页面状态。 */
function retryPolicy(failureCount: number, error: unknown): boolean {
  return (
    failureCount < 3 &&
    error instanceof PlatformApiError &&
    error.code === "POLICY_UNAVAILABLE" &&
    error.retryable
  );
}

/** 集中维护权限与审计页 Query、Mutation、轮询和并发冲突反馈。 */
export function usePermissionsAudit(options: Options) {
  // 1. 建立当前空间的角色、审计详情和导出查询，权限撤销后立即停用对应读取。
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const roleKey = ["role-governance", options.workspaceId] as const;
  const exportKey = ["audit-exports", options.workspaceId] as const;
  const governance = useQuery({
    queryKey: roleKey,
    queryFn: ({ signal }) => getRoleGovernance(options.workspaceId!, signal),
    enabled: Boolean(options.workspaceId && options.roleEnabled),
    retry: retryPolicy,
  });
  const audits = useQuery({
    queryKey: ["audit-records", options.workspaceId, options.filters],
    queryFn: ({ signal }) => listAuditRecords(options.workspaceId!, options.filters, signal),
    enabled: Boolean(options.workspaceId && options.auditEnabled),
    retry: retryPolicy,
  });
  const auditDetail = useQuery({
    queryKey: ["audit-record", options.workspaceId, options.selectedAuditId],
    queryFn: ({ signal }) => getAuditRecord(options.workspaceId!, options.selectedAuditId!, signal),
    enabled: Boolean(options.workspaceId && options.auditEnabled && options.selectedAuditId),
    retry: retryPolicy,
  });
  const exports = useQuery({
    queryKey: exportKey,
    queryFn: ({ signal }) => listAuditExports(options.workspaceId!, signal),
    enabled: Boolean(options.workspaceId && options.auditEnabled),
    retry: retryPolicy,
    refetchInterval: (query) =>
      query.state.data?.some((item) => ["pending", "running", "retry_wait"].includes(item.status))
        ? 3_000
        : false,
  });
  // 2. 两类写操作只刷新各自事实；角色并发冲突强制重新读取可信版本。
  const saveRole = useMutation({
    mutationFn: (input: {
      roleId: string;
      expectedRoleVersion: number;
      items: readonly RoleGrant[];
    }) =>
      replaceRolePermissions(
        options.workspaceId!,
        input.roleId,
        input.expectedRoleVersion,
        input.items,
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: roleKey });
      void message.success("角色权限已保存");
    },
    onError: async (error) => {
      if (error instanceof PlatformApiError && error.code === "ROLE_CONFLICT") {
        await queryClient.invalidateQueries({ queryKey: roleKey });
        void message.warning("角色已被其他管理员更新，已载入最新版本，请重新确认");
        return;
      }
      void message.error(errorMessage(error));
    },
  });
  const createExport = useMutation({
    mutationFn: () => createAuditExport(options.workspaceId!, options.filters),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: exportKey });
      void message.success("审计导出已进入处理队列");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });
  return { governance, audits, auditDetail, exports, saveRole, createExport };
}
