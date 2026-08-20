/**
 * @description Agent 控制台服务端状态编排
 *
 * 负责 Agent、候选、评估、审批和 Release 的查询与命令刷新；
 * 不解释后端状态机，也不保留查询失败前的敏感候选或发布事实。
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";

import { errorMessage } from "@/api/client";
import {
  archiveAgent,
  createAgent,
  getAgentCandidates,
  getAgentReleases,
  getAgents,
  publishAgentRelease,
  requestAgentApproval,
  requestAgentRelease,
  runAgentEvaluation,
  updateAgentDraft,
  type AgentKnowledgeScopeSelection,
  type CreateAgentRequest,
} from "@/api/services/agents";
import { getKnowledgeBases } from "@/api/services/knowledge";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";

/** 更新草稿 Mutation 的乐观锁输入。 */
export interface UpdateAgentDraftInput {
  /** 服务端当前草稿 revision。 */
  expectedRevision: number;
  /** 已通过前端对象校验的完整配置。 */
  configuration: Record<string, unknown>;
}

/** 冻结知识范围并保存草稿的乐观锁输入。 */
export interface BindAgentKnowledgeScopeInput extends UpdateAgentDraftInput {
  /** 服务端用于审计和版本识别的范围名称。 */
  name: string;
  /** 当前用户明确选择的知识库集合；空集合表示禁用知识检索。 */
  knowledgeBaseIds: string[];
}

/**
 * 返回 Agent 控制台三组查询和完整发布流水命令。
 *
 * 候选审批处于活动状态时每 3 秒刷新一次，用于恢复通用审批页面产生的终态；
 * 所有写命令成功后只失效受影响的 Agent 查询，避免刷新无关空间缓存。
 */
export function useAgentControl(agentId: string | null, canReadKnowledgeBases: boolean) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { workspaceId } = useCurrentWorkspace();

  // 1. 列表是选择入口，候选和 Release 只在 URL 已选择 Agent 后加载。
  const agents = useQuery({
    queryKey: ["agents", workspaceId],
    queryFn: ({ signal }) => getAgents(workspaceId!, signal),
    enabled: Boolean(workspaceId),
    retry: false,
  });
  const candidates = useQuery({
    queryKey: ["agent-candidates", workspaceId, agentId],
    queryFn: ({ signal }) => getAgentCandidates(workspaceId!, agentId!, signal),
    enabled: Boolean(workspaceId && agentId),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.some((item) =>
        ["testing", "approval_pending"].includes(item.candidate.status),
      )
        ? 3_000
        : false,
  });
  const releases = useQuery({
    queryKey: ["agent-releases", workspaceId, agentId],
    queryFn: ({ signal }) => getAgentReleases(workspaceId!, agentId!, signal),
    enabled: Boolean(workspaceId && agentId),
    retry: false,
  });
  const knowledgeBases = useQuery({
    queryKey: ["knowledge-bases", workspaceId],
    queryFn: ({ signal }) => getKnowledgeBases(workspaceId!, signal),
    enabled: Boolean(workspaceId && canReadKnowledgeBases),
    retry: false,
  });

  // 2. 草稿与 Agent 定义共用列表聚合；发布流水会同时改变候选和 Release。
  const refreshAgent = () => queryClient.invalidateQueries({ queryKey: ["agents", workspaceId] });
  const refreshPipeline = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["agents", workspaceId] }),
      queryClient.invalidateQueries({ queryKey: ["agent-candidates", workspaceId, agentId] }),
      queryClient.invalidateQueries({ queryKey: ["agent-releases", workspaceId, agentId] }),
    ]);
  };
  const notifyError = (error: unknown) => void message.error(errorMessage(error));

  // 3. Mutation 只提交服务端要求的版本事实，不在浏览器预判新的 revision 或 generation。
  const create = useMutation({
    mutationFn: (body: CreateAgentRequest) => createAgent(workspaceId!, body),
    onSuccess: async () => {
      await refreshAgent();
      void message.success("Agent 已创建");
    },
    onError: notifyError,
  });
  const saveDraft = useMutation({
    mutationFn: (input: UpdateAgentDraftInput) =>
      updateAgentDraft(workspaceId!, agentId!, input.expectedRevision, input.configuration),
    onSuccess: async () => {
      await refreshPipeline();
      void message.success("草稿已保存，旧候选资格已按服务端规则重新计算");
    },
    onError: notifyError,
  });
  const bindKnowledgeScope = useMutation({
    mutationFn: (input: BindAgentKnowledgeScopeInput) => {
      const knowledgeScope: AgentKnowledgeScopeSelection = {
        name: input.name,
        knowledge_base_ids: input.knowledgeBaseIds,
      };
      return updateAgentDraft(
        workspaceId!,
        agentId!,
        input.expectedRevision,
        input.configuration,
        knowledgeScope,
      );
    },
    onSuccess: async () => {
      await refreshPipeline();
      void message.success("知识范围已冻结并保存到新草稿");
    },
    onError: notifyError,
  });
  const archive = useMutation({
    mutationFn: (expectedVersion: number) => archiveAgent(workspaceId!, agentId!, expectedVersion),
    onSuccess: async () => {
      await refreshAgent();
      void message.success("Agent 已归档，历史 Release 保持可追溯");
    },
    onError: notifyError,
  });
  const requestRelease = useMutation({
    mutationFn: (expectedRevision: number) =>
      requestAgentRelease(workspaceId!, agentId!, expectedRevision),
    onSuccess: async () => {
      await refreshPipeline();
      void message.success("当前草稿已冻结为发布候选");
    },
    onError: notifyError,
  });
  const evaluate = useMutation({
    mutationFn: (candidateId: string) => runAgentEvaluation(workspaceId!, agentId!, candidateId),
    onSuccess: async () => {
      await refreshPipeline();
      void message.success("五类确定性测试已完成");
    },
    onError: notifyError,
  });
  const requestApproval = useMutation({
    mutationFn: (candidateId: string) => requestAgentApproval(workspaceId!, agentId!, candidateId),
    onSuccess: async () => {
      await refreshPipeline();
      void message.success("发布审批已发起");
    },
    onError: notifyError,
  });
  const publish = useMutation({
    mutationFn: (candidateId: string) => publishAgentRelease(workspaceId!, agentId!, candidateId),
    onSuccess: async () => {
      await refreshPipeline();
      void message.success("不可变 Agent Release 已发布");
    },
    onError: notifyError,
  });

  return {
    workspaceId,
    agents,
    candidates,
    releases,
    knowledgeBases,
    create,
    saveDraft,
    bindKnowledgeScope,
    archive,
    requestRelease,
    evaluate,
    requestApproval,
    publish,
    refreshPipeline,
  };
}
