import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

export type KnowledgeBaseSummary = components["schemas"]["KnowledgeBaseSummaryResponse"];
export type KnowledgeDocumentSummary = components["schemas"]["KnowledgeDocumentSummaryResponse"];
export type IngestionJob = components["schemas"]["IngestionJobResponse"];
export type CreateKnowledgeBaseRequest = components["schemas"]["CreateKnowledgeBaseRequest"];
export type DocumentUploadResponse = components["schemas"]["DocumentUploadResponse"];

export async function getKnowledgeBases(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["KnowledgeBaseListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/knowledge-bases`,
    { signal },
  );
  return response.items;
}

export async function getKnowledgeDocuments(
  workspaceId: string,
  knowledgeBaseId: string,
  signal?: AbortSignal,
) {
  const response = await apiRequest<components["schemas"]["KnowledgeDocumentListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/knowledge-bases/${knowledgeBaseId}/documents`,
    { signal },
  );
  return response.items;
}

export async function getKnowledgeIngestionJobs(
  workspaceId: string,
  knowledgeBaseId: string,
  signal?: AbortSignal,
) {
  const response = await apiRequest<components["schemas"]["IngestionJobListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/knowledge-bases/${knowledgeBaseId}/ingestion-jobs`,
    { signal },
  );
  return response.items;
}

export function createKnowledgeBase(workspaceId: string, body: CreateKnowledgeBaseRequest) {
  return apiRequest<components["schemas"]["KnowledgeBaseResponse"]>(
    `/api/v1/workspaces/${workspaceId}/knowledge-bases`,
    { method: "POST", body },
  );
}

interface UploadKnowledgeDocumentParams {
  title: string;
  file: File;
  visibility: "private" | "workspace";
  securityLevel: "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED";
}

export function uploadKnowledgeDocument(
  workspaceId: string,
  knowledgeBaseId: string,
  values: UploadKnowledgeDocumentParams,
) {
  const body = new FormData();
  body.append("title", values.title);
  body.append("file", values.file);
  body.append("visibility", values.visibility);
  body.append("security_level", values.securityLevel);
  return apiRequest<DocumentUploadResponse>(
    `/api/v1/workspaces/${workspaceId}/knowledge-bases/${knowledgeBaseId}/documents/upload`,
    { method: "POST", body },
  );
}

export function uploadKnowledgeDocumentVersion(
  workspaceId: string,
  knowledgeBaseId: string,
  documentId: string,
  file: File,
) {
  const body = new FormData();
  body.append("file", file);
  return apiRequest<components["schemas"]["DocumentVersionUploadResponse"]>(
    `/api/v1/workspaces/${workspaceId}/knowledge-bases/${knowledgeBaseId}/documents/${documentId}/versions/upload`,
    { method: "POST", body },
  );
}

export function markKnowledgeDocumentVersionReady(
  workspaceId: string,
  knowledgeBaseId: string,
  documentId: string,
  documentVersionId: string,
  contentHash: string,
) {
  return apiRequest<components["schemas"]["DocumentVersionResponse"]>(
    `/api/v1/workspaces/${workspaceId}/knowledge-bases/${knowledgeBaseId}/documents/${documentId}/versions/${documentVersionId}/ready`,
    { method: "POST", body: { content_hash: contentHash } },
  );
}

export function publishKnowledgeDocumentVersion(
  workspaceId: string,
  knowledgeBaseId: string,
  documentId: string,
  documentVersionId: string,
) {
  return apiRequest<components["schemas"]["DocumentVersionResponse"]>(
    `/api/v1/workspaces/${workspaceId}/knowledge-bases/${knowledgeBaseId}/documents/${documentId}/versions/${documentVersionId}/publish`,
    { method: "POST" },
  );
}

export function retryKnowledgeIngestionJob(workspaceId: string, ingestionJobId: string) {
  return apiRequest<IngestionJob>(
    `/api/v1/workspaces/${workspaceId}/ingestion-jobs/${ingestionJobId}/retry`,
    { method: "POST" },
  );
}
