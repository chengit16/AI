/**
 * @description 审批策略和待办操作 Hook
 *
 * 负责审批查询、轮询、策略版本写入与人工动作反馈，不在浏览器预测审批状态转换。
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";

import { errorMessage } from "@/api/client";
import {
  actOnApproval,
  createApprovalPolicy,
  getApprovalInstances,
  getApprovalPolicies,
  getApprovalPolicy,
  previewApprovalChain,
  reviseApprovalPolicy,
  startApprovalInstance,
  transferApproval,
  type ApprovalPolicyDefinition,
  type CreateApprovalPolicyRequest,
  type PreviewApprovalRequest,
  type StartApprovalRequest,
} from "@/api/services/workflows";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";

/** 审批策略 Hook 的当前选择和创建后回调。 */
export interface ApprovalOperationOptions {
  /** 当前正在查看或修订的审批策略 ID。 */
  selectedPolicyId: string | null;
  /** 创建成功后由页面切换到新策略。 */
  onPolicyCreated: (approvalPolicyId: string) => void;
}

/**
 * 返回审批策略、策略详情、可见待办和全部审批命令。
 *
 * 待办存在 pending 状态时每三秒刷新；人工动作后统一刷新待办，工作流恢复结果由运行查询
 * 自己的轮询观察，两个业务聚合不共享前端乐观状态。
 */
export function useApprovalOperations(options: ApprovalOperationOptions) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { workspaceId } = useCurrentWorkspace();
  const selectedId = options.selectedPolicyId;

  // 1. 策略身份、当前版本和参与者待办保持独立查询边界。
  const policies = useQuery({
    queryKey: ["approval-policies", workspaceId],
    queryFn: ({ signal }) => getApprovalPolicies(workspaceId!, signal),
    enabled: Boolean(workspaceId),
    retry: false,
  });
  const policyDetail = useQuery({
    queryKey: ["approval-policy-detail", workspaceId, selectedId],
    queryFn: ({ signal }) => getApprovalPolicy(workspaceId!, selectedId!, signal),
    enabled: Boolean(workspaceId && selectedId),
    retry: false,
  });
  const instances = useQuery({
    queryKey: ["approval-instances", workspaceId],
    queryFn: ({ signal }) => getApprovalInstances(workspaceId!, signal),
    enabled: Boolean(workspaceId),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.some((instance) => instance.status === "pending") ? 3_000 : false,
  });

  // 2. 策略版本变更同时刷新列表身份和当前版本，避免修订号错位。
  const refreshPolicies = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["approval-policies", workspaceId] }),
      queryClient.invalidateQueries({
        queryKey: ["approval-policy-detail", workspaceId, selectedId],
      }),
    ]);
  };
  const refreshInstances = () =>
    queryClient.invalidateQueries({ queryKey: ["approval-instances", workspaceId] });
  const notifyError = (error: unknown) => void message.error(errorMessage(error));

  // 3. 策略写入和实例动作都等待服务端返回聚合事实，不在页面提前假设终态。
  const createPolicy = useMutation({
    mutationFn: (body: CreateApprovalPolicyRequest) => createApprovalPolicy(workspaceId!, body),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["approval-policies", workspaceId] });
      options.onPolicyCreated(result.policy.approval_policy_id);
      void message.success("审批策略已创建");
    },
    onError: notifyError,
  });
  const revisePolicy = useMutation({
    mutationFn: ({
      definition,
      version,
    }: {
      definition: ApprovalPolicyDefinition;
      version: number;
    }) => reviseApprovalPolicy(workspaceId!, selectedId!, version, definition),
    onSuccess: async () => {
      await refreshPolicies();
      void message.success("审批策略新版本已生效");
    },
    onError: notifyError,
  });
  const previewChain = useMutation({
    mutationFn: (body: PreviewApprovalRequest) => previewApprovalChain(workspaceId!, body),
    onError: notifyError,
  });
  const startInstance = useMutation({
    mutationFn: (body: StartApprovalRequest) => startApprovalInstance(workspaceId!, body),
    onSuccess: async () => {
      await refreshInstances();
      void message.success("审批已发起");
    },
    onError: notifyError,
  });
  const act = useMutation({
    mutationFn: ({
      instanceId,
      action,
      reasonCode,
    }: {
      instanceId: string;
      action: "approve" | "reject" | "withdraw";
      reasonCode?: string;
    }) =>
      actOnApproval(
        workspaceId!,
        instanceId,
        action,
        ["approval-ui", crypto.randomUUID()].join("-"),
        reasonCode,
      ),
    onSuccess: async () => {
      await refreshInstances();
      void message.success("审批状态已更新");
    },
    onError: notifyError,
  });
  const transfer = useMutation({
    mutationFn: ({
      instanceId,
      targetAccountId,
    }: {
      instanceId: string;
      targetAccountId: string;
    }) =>
      transferApproval(
        workspaceId!,
        instanceId,
        targetAccountId,
        ["approval-ui", crypto.randomUUID()].join("-"),
      ),
    onSuccess: async () => {
      await refreshInstances();
      void message.success("审批责任已转交");
    },
    onError: notifyError,
  });

  return {
    policies,
    policyDetail,
    instances,
    createPolicy,
    revisePolicy,
    previewChain,
    startInstance,
    act,
    transfer,
  };
}
