/**
 * @description 企业组织管理业务 Hook
 * 组合部门、岗位、成员 Query 与写操作，并维护层级状态变化后的缓存一致性。
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";

import { errorMessage } from "@/api/client";
import {
  assignMemberOrganization,
  createDepartment,
  createPosition,
  getDepartments,
  getPositions,
  setDepartmentStatus,
  setPositionStatus,
} from "@/api/services/organization";
import { getWorkspaceMembers } from "@/api/services/workspaces";

import type {
  AssignmentFormValues,
  DepartmentFormValues,
  PositionFormValues,
} from "./OrganizationForms";

interface Options {
  /** 当前企业空间 ID；未解析空间时为空。 */
  workspaceId: string | null;
  /** 页面确认当前空间为企业且允许发起查询后置为 `true`。 */
  enabled: boolean;
  /** 任一组织表单成功提交后的统一关闭回调。 */
  closeForm: () => void;
}

/**
 * 集中维护组织页 Query、Mutation 与缓存刷新边界。
 *
 * `enabled` 由页面在确认企业空间后开启；部门与岗位的新增或状态变化可能改变
 * 有效层级和可选岗位，因此两类缓存必须一起失效。成员归属提交不改变当前页面的
 * 部门、岗位或成员摘要，成功后只关闭表单并等待后续成员详情能力读取新事实。
 */
export function useOrganizationManagement({ workspaceId, enabled, closeForm }: Options) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  // 1. 三组 Query 共享企业空间启用条件，但分别保存组织、岗位和成员服务端事实。
  const departments = useQuery({
    queryKey: ["departments", workspaceId],
    queryFn: ({ signal }) => getDepartments(workspaceId!, signal),
    enabled,
    retry: false,
  });
  const positions = useQuery({
    queryKey: ["positions", workspaceId],
    queryFn: ({ signal }) => getPositions(workspaceId!, signal),
    enabled,
    retry: false,
  });
  const members = useQuery({
    queryKey: ["workspace-members", workspaceId],
    queryFn: ({ signal }) => getWorkspaceMembers(workspaceId!, signal),
    enabled,
    retry: false,
  });
  const refreshOrganization = async () => {
    // 上级部门状态会级联影响岗位有效性，两份服务端投影必须在同一动作后共同刷新。
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["departments", workspaceId] }),
      queryClient.invalidateQueries({ queryKey: ["positions", workspaceId] }),
    ]);
  };
  const handleError = (error: unknown) => void message.error(errorMessage(error));
  // 2. Mutation 按影响范围刷新组织投影；成员归属不改变当前列表，只关闭已提交表单。
  const createDepartmentMutation = useMutation({
    mutationFn: (values: DepartmentFormValues) =>
      createDepartment(workspaceId!, values.name, values.parentId ?? null),
    onSuccess: async () => {
      await refreshOrganization();
      closeForm();
      void message.success("部门已创建");
    },
    onError: handleError,
  });
  const createPositionMutation = useMutation({
    mutationFn: (values: PositionFormValues) =>
      createPosition(workspaceId!, values.departmentId, values.name),
    onSuccess: async () => {
      await refreshOrganization();
      closeForm();
      void message.success("岗位已创建");
    },
    onError: handleError,
  });
  const assignmentMutation = useMutation({
    mutationFn: (values: AssignmentFormValues) =>
      assignMemberOrganization(workspaceId!, values.accountId, {
        department_ids: values.departmentIds,
        primary_department_id: values.primaryDepartmentId,
        position_ids: values.positionIds ?? [],
      }),
    onSuccess: () => {
      closeForm();
      void message.success("成员归属已保存");
    },
    onError: handleError,
  });
  const departmentStatus = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      setDepartmentStatus(workspaceId!, id, active),
    onSuccess: refreshOrganization,
    onError: handleError,
  });
  const positionStatus = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      setPositionStatus(workspaceId!, id, active),
    onSuccess: refreshOrganization,
    onError: handleError,
  });

  return {
    departments,
    positions,
    members,
    createDepartmentMutation,
    createPositionMutation,
    assignmentMutation,
    departmentStatus,
    positionStatus,
  };
}
