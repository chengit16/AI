/**
 * @description 知识生产业务 Hook
 * 负责知识库、文档和入库任务查询、轮询与写操作编排，不负责服务端状态机。
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";
import { useEffect } from "react";

import { errorMessage } from "@/api/client";
import {
  createKnowledgeBase,
  getKnowledgeBases,
  getKnowledgeDocuments,
  getKnowledgeIngestionJobs,
  markKnowledgeDocumentVersionReady,
  publishKnowledgeDocumentVersion,
  retryKnowledgeIngestionJob,
  uploadKnowledgeDocument,
  uploadKnowledgeDocumentVersion,
  type CreateKnowledgeBaseRequest,
  type KnowledgeDocumentSummary,
} from "@/api/services/knowledge";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";

/** 文档上传表单在进入 API Service 前的完整值。 */
export interface UploadDocumentValues {
  /** 文档在知识库中的展示标题。 */
  title: string;
  /** 用户本次选择的原始浏览器文件。 */
  file: File;
  /** 首期允许的创建者私有或空间共享范围。 */
  visibility: "private" | "workspace";
  /** 服务端用于字段、检索和模型上下文策略的敏感级别。 */
  securityLevel: "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED";
}

/**
 * 返回知识生产页三类查询和全部写操作。
 *
 * 任务查询只在存在当前空间和知识库时启动，活动任务按 3 秒轮询；任何会改变文档
 * 或任务状态的命令成功后统一刷新知识库、文档和任务，避免跨列表状态不一致。
 */
export function useKnowledgeProduction(knowledgeBaseId: string | null) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { workspaceId } = useCurrentWorkspace();
  // 1. 三组 Query 分别缓存空间知识库、选中文档和异步任务事实。
  const bases = useQuery({
    queryKey: ["knowledge-bases", workspaceId],
    queryFn: ({ signal }) => getKnowledgeBases(workspaceId!, signal),
    enabled: Boolean(workspaceId),
    retry: false,
  });
  const documents = useQuery({
    queryKey: ["knowledge-documents", workspaceId, knowledgeBaseId],
    queryFn: ({ signal }) => getKnowledgeDocuments(workspaceId!, knowledgeBaseId!, signal),
    enabled: Boolean(workspaceId && knowledgeBaseId),
    retry: false,
  });
  const jobs = useQuery({
    queryKey: ["knowledge-ingestion-jobs", workspaceId, knowledgeBaseId],
    queryFn: ({ signal }) => getKnowledgeIngestionJobs(workspaceId!, knowledgeBaseId!, signal),
    enabled: Boolean(workspaceId && knowledgeBaseId),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.some((job) => ["queued", "running", "retry_wait"].includes(job.status))
        ? 3_000
        : false,
  });

  useEffect(() => {
    if (!jobs.dataUpdatedAt || !jobs.data?.length) {
      return;
    }
    // Worker 独立更新任务与文档事实；每个任务快照都刷新文档，避免展示可点击的过期状态。
    void queryClient.invalidateQueries({
      queryKey: ["knowledge-documents", workspaceId, knowledgeBaseId],
    });
  }, [jobs.data, jobs.dataUpdatedAt, knowledgeBaseId, queryClient, workspaceId]);

  // 2. 文档写操作会同时改变知识库统计、版本摘要和任务状态，必须整体刷新。
  const refreshBase = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["knowledge-bases", workspaceId] }),
      queryClient.invalidateQueries({
        queryKey: ["knowledge-documents", workspaceId, knowledgeBaseId],
      }),
      queryClient.invalidateQueries({
        queryKey: ["knowledge-ingestion-jobs", workspaceId, knowledgeBaseId],
      }),
    ]);
  };
  // 3. 所有 Mutation 复用稳定错误转换，成功后再刷新相关服务端事实并提示用户。
  const notifyError = (error: unknown) => void message.error(errorMessage(error));
  const createBase = useMutation({
    mutationFn: (body: CreateKnowledgeBaseRequest) => createKnowledgeBase(workspaceId!, body),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["knowledge-bases", workspaceId] });
      void message.success("知识库已创建");
    },
    onError: notifyError,
  });
  const uploadDocument = useMutation({
    mutationFn: (values: UploadDocumentValues) =>
      uploadKnowledgeDocument(workspaceId!, knowledgeBaseId!, values),
    onSuccess: async () => {
      await refreshBase();
      void message.success("文档已上传并进入解析队列");
    },
    onError: notifyError,
  });
  const uploadVersion = useMutation({
    mutationFn: ({ documentId, file }: { documentId: string; file: File }) =>
      uploadKnowledgeDocumentVersion(workspaceId!, knowledgeBaseId!, documentId, file),
    onSuccess: async () => {
      await refreshBase();
      void message.success("新版本已进入解析队列");
    },
    onError: notifyError,
  });
  const markReady = useMutation({
    mutationFn: ({
      document,
      contentHash,
    }: {
      document: KnowledgeDocumentSummary;
      contentHash: string;
    }) =>
      markKnowledgeDocumentVersionReady(
        workspaceId!,
        knowledgeBaseId!,
        document.document_id,
        document.latest_version.document_version_id,
        contentHash,
      ),
    onSuccess: async () => {
      await refreshBase();
      void message.success("文档版本已确认就绪");
    },
    onError: notifyError,
  });
  const publish = useMutation({
    mutationFn: (document: KnowledgeDocumentSummary) =>
      publishKnowledgeDocumentVersion(
        workspaceId!,
        knowledgeBaseId!,
        document.document_id,
        document.latest_version.document_version_id,
      ),
    onSuccess: async () => {
      await refreshBase();
      void message.success("文档版本已发布");
    },
    onError: notifyError,
  });
  const retryJob = useMutation({
    mutationFn: (ingestionJobId: string) =>
      retryKnowledgeIngestionJob(workspaceId!, ingestionJobId),
    onSuccess: async () => {
      await refreshBase();
      void message.success("入库任务已重新排队");
    },
    onError: notifyError,
  });

  return {
    bases,
    documents,
    jobs,
    createBase,
    uploadDocument,
    uploadVersion,
    markReady,
    publish,
    retryJob,
  };
}
