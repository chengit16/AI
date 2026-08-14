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
  workspaceId: string | null;
  enabled: boolean;
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
