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

export interface UploadDocumentValues {
  title: string;
  file: File;
  visibility: "private" | "workspace";
  securityLevel: "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED";
}

export function useKnowledgeProduction(knowledgeBaseId: string | null) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { workspaceId } = useCurrentWorkspace();
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
