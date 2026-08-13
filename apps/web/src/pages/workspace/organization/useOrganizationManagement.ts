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

/** 集中维护组织页缓存边界，部门或岗位变化后只刷新关联事实。 */
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
