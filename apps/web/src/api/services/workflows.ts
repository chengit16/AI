/**
 * @description 工作流、审批策略和审批实例 API Service
 *
 * 只负责稳定 HTTP 契约和幂等请求头，不维护页面状态、按钮权限或服务端状态机。
 */
import { apiRequest } from "@/api/client";
import type { components } from "@/api/generated/platform-api.v1";

/** 可编辑的六类工作流节点和有向边文档。 */
export type WorkflowGraph = components["schemas"]["WorkflowGraphDocument"];
/** 工作流定义列表中的稳定身份与当前发布指针。 */
export type WorkflowDefinition = components["schemas"]["WorkflowDefinitionResponse"];
/** 工作流定义、草稿和发布指针聚合。 */
export type WorkflowDetail = components["schemas"]["WorkflowDetailResponse"];
/** 冻结版本的一次运行事实。 */
export type WorkflowRun = components["schemas"]["WorkflowRunResponse"];
/** 审批策略身份与当前版本聚合。 */
export type ApprovalPolicyDetail = components["schemas"]["ApprovalPolicyDetailResponse"];
/** 审批策略列表中的稳定身份。 */
export type ApprovalPolicy = components["schemas"]["ApprovalPolicyResponse"];
/** 审批策略定义，包括匹配条件和最多五级审批链。 */
export type ApprovalPolicyDefinition = components["schemas"]["ApprovalPolicyDefinitionDocument"];
/** 申请人或历史审批人可见的审批实例聚合。 */
export type ApprovalInstance = components["schemas"]["ApprovalInstanceResponse"];
/** 创建工作流所需的名称、说明和初始图。 */
export type CreateWorkflowRequest = components["schemas"]["CreateWorkflowRequest"];
/** 启动工作流时传入的当前发布版本和受限 JSON 输入。 */
export type CreateWorkflowRunRequest = components["schemas"]["CreateWorkflowRunRequest"];
/** 创建审批策略所需的名称和首个不可变定义。 */
export type CreateApprovalPolicyRequest = components["schemas"]["CreateApprovalPolicyRequest"];
/** 独立发起审批时使用的可信主题字段。 */
export type StartApprovalRequest = components["schemas"]["StartApprovalInstanceRequest"];
/** 预计算审批链但不创建实例的主题字段。 */
export type PreviewApprovalRequest = components["schemas"]["PreviewApprovalChainRequest"];

/** 查询当前授权数据范围内的工作流定义。 */
export async function getWorkflows(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["WorkflowListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/workflows`,
    { signal },
  );
  return response.items;
}

/** 查询工作流当前草稿和发布指针。 */
export function getWorkflow(workspaceId: string, workflowId: string, signal?: AbortSignal) {
  return apiRequest<WorkflowDetail>(`/api/v1/workspaces/${workspaceId}/workflows/${workflowId}`, {
    signal,
  });
}

/** 创建工作流及其首个可编辑草稿。 */
export function createWorkflow(workspaceId: string, body: CreateWorkflowRequest) {
  return apiRequest<WorkflowDetail>(`/api/v1/workspaces/${workspaceId}/workflows`, {
    method: "POST",
    body,
  });
}

/** 按修订号整体替换草稿，避免旧页面覆盖并发修改。 */
export function updateWorkflowDraft(
  workspaceId: string,
  workflowId: string,
  expectedRevision: number,
  graph: WorkflowGraph,
) {
  return apiRequest<components["schemas"]["WorkflowDraftResponse"]>(
    `/api/v1/workspaces/${workspaceId}/workflows/${workflowId}/draft`,
    { method: "PUT", body: { expected_revision: expectedRevision, graph } },
  );
}

/** 重新执行服务端确定性图校验，不改变草稿修订号。 */
export function validateWorkflowDraft(workspaceId: string, workflowId: string) {
  return apiRequest<components["schemas"]["WorkflowDraftResponse"]>(
    `/api/v1/workspaces/${workspaceId}/workflows/${workflowId}/draft/validate`,
    { method: "POST" },
  );
}

/** 冻结当前有效草稿并切换工作流发布指针。 */
export function publishWorkflow(workspaceId: string, workflowId: string, expectedRevision: number) {
  return apiRequest<components["schemas"]["WorkflowPublishResponse"]>(
    `/api/v1/workspaces/${workspaceId}/workflows/${workflowId}/publish`,
    { method: "POST", body: { expected_revision: expectedRevision } },
  );
}

/** 幂等创建运行事实；服务端只接受当前发布版本。 */
export function createWorkflowRun(
  workspaceId: string,
  workflowId: string,
  body: CreateWorkflowRunRequest,
  idempotencyKey: string,
) {
  return apiRequest<WorkflowRun>(`/api/v1/workspaces/${workspaceId}/workflows/${workflowId}/runs`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body,
  });
}

/** 查询工作流最近运行，刷新页面后仍可恢复监控。 */
export async function getWorkflowRuns(
  workspaceId: string,
  workflowId: string,
  signal?: AbortSignal,
) {
  const response = await apiRequest<components["schemas"]["WorkflowRunListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/workflows/${workflowId}/runs`,
    { signal },
  );
  return response.items;
}

/** 查询当前授权范围内的审批策略身份。 */
export async function getApprovalPolicies(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["ApprovalPolicyListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/approval-policies`,
    { signal },
  );
  return response.items;
}

/** 查询审批策略当前不可变定义版本。 */
export function getApprovalPolicy(
  workspaceId: string,
  approvalPolicyId: string,
  signal?: AbortSignal,
) {
  return apiRequest<ApprovalPolicyDetail>(
    `/api/v1/workspaces/${workspaceId}/approval-policies/${approvalPolicyId}`,
    { signal },
  );
}

/** 创建审批策略及首个不可变版本。 */
export function createApprovalPolicy(workspaceId: string, body: CreateApprovalPolicyRequest) {
  return apiRequest<ApprovalPolicyDetail>(`/api/v1/workspaces/${workspaceId}/approval-policies`, {
    method: "POST",
    body,
  });
}

/** 以乐观锁新增审批策略版本，历史版本保持不可变。 */
export function reviseApprovalPolicy(
  workspaceId: string,
  approvalPolicyId: string,
  expectedVersion: number,
  definition: ApprovalPolicyDefinition,
) {
  return apiRequest<ApprovalPolicyDetail>(
    `/api/v1/workspaces/${workspaceId}/approval-policies/${approvalPolicyId}/versions`,
    { method: "POST", body: { expected_version: expectedVersion, definition } },
  );
}

/** 预计算审批链，不创建审批实例或产生业务副作用。 */
export function previewApprovalChain(workspaceId: string, body: PreviewApprovalRequest) {
  return apiRequest<components["schemas"]["ApprovalChainResponse"]>(
    `/api/v1/workspaces/${workspaceId}/approval-policies/preview-chain`,
    { method: "POST", body },
  );
}

/** 查询当前账号作为申请人或历史审批人可见的审批实例。 */
export async function getApprovalInstances(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["ApprovalInstanceListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/approval-instances`,
    { signal },
  );
  return response.items;
}

/** 独立发起审批；工作流内审批由执行器原子创建。 */
export function startApprovalInstance(workspaceId: string, body: StartApprovalRequest) {
  return apiRequest<ApprovalInstance>(`/api/v1/workspaces/${workspaceId}/approval-instances`, {
    method: "POST",
    body,
  });
}

/** 提交审批人工动作，动作幂等性由请求体中的键保证。 */
export function actOnApproval(
  workspaceId: string,
  approvalInstanceId: string,
  action: "approve" | "reject" | "withdraw",
  idempotencyKey: string,
  reasonCode?: string,
) {
  return apiRequest<components["schemas"]["ApprovalCommandResponse"]>(
    `/api/v1/workspaces/${workspaceId}/approval-instances/${approvalInstanceId}/${action}`,
    { method: "POST", body: { idempotency_key: idempotencyKey, reason_code: reasonCode || null } },
  );
}

/** 将当前账号的活动审批责任转交给另一个活动成员。 */
export function transferApproval(
  workspaceId: string,
  approvalInstanceId: string,
  targetAccountId: string,
  idempotencyKey: string,
) {
  return apiRequest<components["schemas"]["ApprovalCommandResponse"]>(
    `/api/v1/workspaces/${workspaceId}/approval-instances/${approvalInstanceId}/transfer`,
    {
      method: "POST",
      body: { idempotency_key: idempotencyKey, target_account_id: targetAccountId },
    },
  );
}
