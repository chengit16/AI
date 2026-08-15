/**
 * @description 工作流设计、发布和运行查询 Hook
 *
 * 负责 TanStack Query 缓存与页面写操作反馈，不实现服务端图校验或执行状态机。
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";

import { errorMessage } from "@/api/client";
import {
  createWorkflow,
  createWorkflowRun,
  getWorkflow,
  getWorkflowRuns,
  getWorkflows,
  publishWorkflow,
  updateWorkflowDraft,
  validateWorkflowDraft,
  type CreateWorkflowRequest,
  type WorkflowGraph,
} from "@/api/services/workflows";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";

/** 工作流 Hook 的当前选择和创建后回调。 */
export interface WorkflowOperationOptions {
  /** 当前正在设计或监控的工作流 ID。 */
  selectedWorkflowId: string | null;
  /** 创建成功后由页面把 URL 选择切换到新工作流。 */
  onWorkflowCreated: (workflowId: string) => void;
}

/**
 * 返回工作流列表、草稿详情、最近运行及全部设计动作。
 *
 * 运行查询仅在工作流已选中时启动，存在活动运行时每两秒刷新；所有写操作成功后只刷新
 * 受影响的缓存键，避免审批待办等其他工作区发生无意义重载。
 */
export function useWorkflowOperations(options: WorkflowOperationOptions) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { workspaceId } = useCurrentWorkspace();
  const selectedId = options.selectedWorkflowId;

  // 1. 定义、详情和运行分别缓存，草稿编辑不会让整个工作台进入全屏加载。
  const workflows = useQuery({
    queryKey: ["workflows", workspaceId],
    queryFn: ({ signal }) => getWorkflows(workspaceId!, signal),
    enabled: Boolean(workspaceId),
    retry: false,
  });
  const detail = useQuery({
    queryKey: ["workflow-detail", workspaceId, selectedId],
    queryFn: ({ signal }) => getWorkflow(workspaceId!, selectedId!, signal),
    enabled: Boolean(workspaceId && selectedId),
    retry: false,
  });
  const runs = useQuery({
    queryKey: ["workflow-runs", workspaceId, selectedId],
    queryFn: ({ signal }) => getWorkflowRuns(workspaceId!, selectedId!, signal),
    enabled: Boolean(workspaceId && selectedId),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.some((run) => ["queued", "running"].includes(run.status)) ? 2_000 : false,
  });

  // 2. 草稿和发布会同时改变列表摘要与详情，统一刷新两处事实。
  const refreshDefinition = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["workflows", workspaceId] }),
      queryClient.invalidateQueries({
        queryKey: ["workflow-detail", workspaceId, selectedId],
      }),
    ]);
  };
  const notifyError = (error: unknown) => void message.error(errorMessage(error));

  // 3. Mutation 只提交用户命令，乐观锁、图合法性和版本冻结始终由服务端裁决。
  const create = useMutation({
    mutationFn: (body: CreateWorkflowRequest) => createWorkflow(workspaceId!, body),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["workflows", workspaceId] });
      options.onWorkflowCreated(result.workflow.workflow_id);
      void message.success("工作流已创建");
    },
    onError: notifyError,
  });
  const saveDraft = useMutation({
    mutationFn: ({ graph, revision }: { graph: WorkflowGraph; revision: number }) =>
      updateWorkflowDraft(workspaceId!, selectedId!, revision, graph),
    onSuccess: async () => {
      await refreshDefinition();
      void message.success("草稿已保存");
    },
    onError: notifyError,
  });
  const validateDraft = useMutation({
    mutationFn: () => validateWorkflowDraft(workspaceId!, selectedId!),
    onSuccess: async () => {
      await refreshDefinition();
      void message.success("图校验已完成");
    },
    onError: notifyError,
  });
  const publish = useMutation({
    mutationFn: (revision: number) => publishWorkflow(workspaceId!, selectedId!, revision),
    onSuccess: async () => {
      await refreshDefinition();
      void message.success("工作流版本已发布");
    },
    onError: notifyError,
  });
  const run = useMutation({
    mutationFn: (inputPayload: Record<string, unknown>) => {
      const versionId = detail.data?.publication?.workflow_version_id;
      if (!versionId) throw new Error("请先发布工作流再运行");
      return createWorkflowRun(
        workspaceId!,
        selectedId!,
        { workflow_version_id: versionId, input_payload: inputPayload },
        ["workflow-ui", crypto.randomUUID()].join("-"),
      );
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["workflow-runs", workspaceId, selectedId],
      });
      void message.success("运行已启动");
    },
    onError: notifyError,
  });

  return { workflows, detail, runs, create, saveDraft, validateDraft, publish, run };
}
