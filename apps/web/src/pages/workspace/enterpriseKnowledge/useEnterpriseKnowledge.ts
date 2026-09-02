/** @description 企业知识门户统一查询与分类、知识域治理 Mutation 编排。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";

import { errorMessage, PlatformApiError } from "@/api/client";
import {
  archiveEnterpriseCategory,
  archiveTeamKnowledgeDomain,
  createEnterpriseCategory,
  createTeamKnowledgeDomain,
  getEnterpriseKnowledgePortal,
  getEnterpriseDocumentDetail,
  listEnterpriseDocumentPublishRequests,
  requestEnterpriseDocumentPublish,
  replaceEnterpriseCategoryDocuments,
  replaceTeamKnowledgeDomainScope,
  resolveTeamKnowledgeDomainScope,
  updateEnterpriseCategory,
  updateTeamKnowledgeDomain,
  type CreateEnterpriseCategory,
  type CreateTeamKnowledgeDomain,
  type EnterpriseCategory,
  type ReplaceKnowledgeDomainScope,
  type TeamKnowledgeDomain,
  type UpdateEnterpriseCategory,
  type UpdateTeamKnowledgeDomain,
} from "@/api/services/enterpriseKnowledge";
import { actOnApproval, transferApproval } from "@/api/services/workflows";

interface UseEnterpriseKnowledgeOptions {
  /** 来自可信空间切换状态的工作空间标识。 */
  workspaceId: string | null | undefined;
  /** 仅企业空间且菜单允许读取时启用查询。 */
  enabled: boolean;
  /** 只有菜单允许读取发布台账时启用参与者查询。 */
  publishRequestsEnabled: boolean;
}

/** 策略传播暂时不可用时有限重试，其他错误立即交给页面处理。 */
export function shouldRetryEnterpriseKnowledge(failureCount: number, error: unknown): boolean {
  return (
    failureCount < 4 &&
    error instanceof PlatformApiError &&
    error.code === "POLICY_UNAVAILABLE" &&
    error.retryable
  );
}

/** 集中维护企业知识快照刷新、稳定反馈和乐观冲突提示。 */
export function useEnterpriseKnowledge({
  workspaceId,
  enabled,
  publishRequestsEnabled,
}: UseEnterpriseKnowledgeOptions) {
  // 1. 建立当前企业空间唯一查询键，读取与所有写操作共享同一份事务快照。
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const queryKey = ["enterprise-knowledge", workspaceId] as const;
  const portal = useQuery({
    queryKey,
    queryFn: ({ signal }) => getEnterpriseKnowledgePortal(workspaceId!, signal),
    enabled: Boolean(workspaceId && enabled),
    retry: shouldRetryEnterpriseKnowledge,
    retryDelay: (failureCount) => Math.min(500 * 2 ** failureCount, 4_000),
  });
  const publishRequestQueryKey = ["enterprise-document-publish-requests", workspaceId] as const;
  const publishRequests = useQuery({
    queryKey: publishRequestQueryKey,
    queryFn: ({ signal }) => listEnterpriseDocumentPublishRequests(workspaceId!, signal),
    enabled: Boolean(workspaceId && enabled && publishRequestsEnabled),
    retry: shouldRetryEnterpriseKnowledge,
    refetchInterval: (query) =>
      query.state.data?.some((request) => request.status === "pending") ? 3_000 : false,
  });

  const refresh = async () => queryClient.invalidateQueries({ queryKey });
  const mutationOptions = (successMessage: string) => ({
    onSuccess: async () => {
      await refresh();
      void message.success(successMessage);
    },
    onError: (error: unknown) => {
      const content =
        error instanceof PlatformApiError && error.code === "KNOWLEDGE_CONFLICT"
          ? "数据已被其他操作更新，请刷新后重试"
          : errorMessage(error);
      void message.error(content);
    },
  });

  // 2. 所有写操作只提交完整领域命令，成功后统一刷新单一快照，避免局部事实漂移。
  const createCategory = useMutation({
    mutationFn: (body: CreateEnterpriseCategory) => createEnterpriseCategory(workspaceId!, body),
    ...mutationOptions("企业分类已创建"),
  });
  const updateCategory = useMutation({
    mutationFn: ({
      category,
      body,
    }: {
      category: EnterpriseCategory;
      body: UpdateEnterpriseCategory;
    }) => updateEnterpriseCategory(workspaceId!, category.category_id, body),
    ...mutationOptions("企业分类已更新"),
  });
  const archiveCategory = useMutation({
    mutationFn: (category: EnterpriseCategory) =>
      archiveEnterpriseCategory(workspaceId!, category.category_id, category.version),
    ...mutationOptions("企业分类已归档"),
  });
  const bindCategoryDocuments = useMutation({
    mutationFn: ({
      category,
      documentIds,
    }: {
      category: EnterpriseCategory;
      documentIds: readonly string[];
    }) =>
      replaceEnterpriseCategoryDocuments(
        workspaceId!,
        category.category_id,
        category.version,
        documentIds,
      ),
    ...mutationOptions("分类文档绑定已更新"),
  });
  const createDomain = useMutation({
    mutationFn: (body: CreateTeamKnowledgeDomain) => createTeamKnowledgeDomain(workspaceId!, body),
    ...mutationOptions("团队知识域已创建"),
  });
  const updateDomain = useMutation({
    mutationFn: ({
      domain,
      body,
    }: {
      domain: TeamKnowledgeDomain;
      body: UpdateTeamKnowledgeDomain;
    }) => updateTeamKnowledgeDomain(workspaceId!, domain.domain_id, body),
    ...mutationOptions("知识域与 RAG 策略已更新"),
  });
  const archiveDomain = useMutation({
    mutationFn: (domain: TeamKnowledgeDomain) =>
      archiveTeamKnowledgeDomain(workspaceId!, domain.domain_id, domain.version),
    ...mutationOptions("团队知识域已归档"),
  });
  const replaceDomainScope = useMutation({
    mutationFn: ({
      domain,
      body,
    }: {
      domain: TeamKnowledgeDomain;
      body: ReplaceKnowledgeDomainScope;
    }) => replaceTeamKnowledgeDomainScope(workspaceId!, domain.domain_id, body),
    ...mutationOptions("知识域范围已原子更新"),
  });
  const resolveDomainScope = useMutation({
    mutationFn: (domainId: string) => resolveTeamKnowledgeDomainScope(workspaceId!, domainId),
    onError: (error) => void message.error(errorMessage(error)),
  });
  const loadDocumentVersions = useMutation({
    mutationFn: ({
      knowledgeBaseId,
      documentId,
    }: {
      knowledgeBaseId: string;
      documentId: string;
    }) => getEnterpriseDocumentDetail(workspaceId!, knowledgeBaseId, documentId),
    onError: (error) => void message.error(errorMessage(error)),
  });

  // 3. 发布审批动作复用通用审批运行时，并同时刷新审批台账与企业知识快照。
  const refreshPublishRequests = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: publishRequestQueryKey }),
      queryClient.invalidateQueries({ queryKey }),
    ]);
  };
  const actOnPublishRequest = useMutation({
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
        ["document-publish", action, crypto.randomUUID()].join("-"),
        reasonCode,
      ),
    onSuccess: async () => {
      await refreshPublishRequests();
      void message.success("发布审批状态已更新");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });
  const transferPublishRequest = useMutation({
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
        ["document-publish-transfer", crypto.randomUUID()].join("-"),
      ),
    onSuccess: async () => {
      await refreshPublishRequests();
      void message.success("发布审批责任已转交");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });
  const requestPublish = useMutation({
    mutationFn: ({
      documentId,
      documentVersionId,
      idempotencyKey,
    }: {
      documentId: string;
      documentVersionId: string;
      idempotencyKey: string;
    }) =>
      requestEnterpriseDocumentPublish(workspaceId!, documentId, documentVersionId, idempotencyKey),
    onSuccess: async () => {
      await refreshPublishRequests();
      void message.success("发布申请已提交");
    },
    onError: (error) => {
      const content =
        error instanceof PlatformApiError && error.code === "DOCUMENT_PUBLISH_NOT_READY"
          ? "当前版本尚未满足发布申请条件，请刷新后重试"
          : errorMessage(error);
      void message.error(content);
    },
  });

  return {
    portal,
    publishRequests,
    createCategory,
    updateCategory,
    archiveCategory,
    bindCategoryDocuments,
    createDomain,
    updateDomain,
    archiveDomain,
    replaceDomainScope,
    resolveDomainScope,
    actOnPublishRequest,
    transferPublishRequest,
    loadDocumentVersions,
    requestPublish,
  };
}
