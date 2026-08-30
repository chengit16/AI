/**
 * @description 知识生产 API Service
 * 只负责契约化请求与 multipart 组装，权限、状态机和上传安全由服务端执行。
 */
import type { components } from "@/api/generated/platform-api.v1";
import { apiDownloadRequest, apiRequest } from "@/api/client";

/** 知识库列表使用的服务端范围化摘要。 */
export type KnowledgeBaseSummary = components["schemas"]["KnowledgeBaseSummaryResponse"];
type KnowledgeDocumentSummaryResponse = components["schemas"]["KnowledgeDocumentSummaryResponse"];
/** 页面使用的文档摘要；Service 将兼容契约的可选组织字段收敛为确定结构。 */
export type KnowledgeDocumentSummary = Omit<
  KnowledgeDocumentSummaryResponse,
  "folder_id" | "tag_ids" | "is_favorite"
> & {
  /** 服务端当前读模型返回的唯一主目录；空字符串只兼容尚未升级的旧响应。 */
  folder_id: string;
  /** 当前仍活动的标签绑定。 */
  tag_ids: readonly string[];
  /** 当前账号的收藏投影。 */
  is_favorite: boolean;
};
/** 文档解析、OCR 和索引前处理任务的可见状态。 */
export type IngestionJob = components["schemas"]["IngestionJobResponse"];
/** 文档元数据、完整版本历史及每版处理状态的服务端聚合。 */
export type KnowledgeDocumentDetail = components["schemas"]["KnowledgeDocumentDetailResponse"];
/** 新建知识库时允许提交的契约字段。 */
export type CreateKnowledgeBaseRequest = components["schemas"]["CreateKnowledgeBaseRequest"];
/** 上传完成后返回的文档、版本和异步任务标识。 */
export type DocumentUploadResponse = components["schemas"]["DocumentUploadResponse"];
/** 个人知识工作台的授权统计、最近文档和收藏聚合。 */
export type PersonalKnowledgeWorkbench =
  components["schemas"]["PersonalKnowledgeWorkbenchResponse"];
/** 已发布文档搜索分页及索引覆盖状态。 */
export type KnowledgeSearchResponse = components["schemas"]["KnowledgeSearchResponse"];
/** 已发布文档的名称或正文命中摘要。 */
export type KnowledgeSearchItem = components["schemas"]["KnowledgeSearchItemResponse"];

/** 搜索筛选参数；正文是否可用仍由服务端字段策略决定。 */
export interface KnowledgeSearchParameters {
  query: string;
  knowledgeBaseId?: string;
  matchType: "all" | "title" | "content";
  favoriteOnly: boolean;
  limit: number;
  offset: number;
}

/** 查询当前空间可见的知识库摘要。 */
export async function getKnowledgeBases(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["KnowledgeBaseListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/knowledge-bases`,
    { signal },
  );
  return response.items;
}

/** 查询个人工作台聚合；最近会话由 Assistant 公开接口独立加载。 */
export function getPersonalKnowledgeWorkbench(workspaceId: string, signal?: AbortSignal) {
  return apiRequest<PersonalKnowledgeWorkbench>(
    `/api/v1/workspaces/${workspaceId}/personal-workbench`,
    { signal },
  );
}

/** 记录当前账号最后访问文档的服务端时间，重复调用只推进时间。 */
export function recordPersonalWorkbenchDocumentAccess(workspaceId: string, documentId: string) {
  return apiRequest<void>(`/api/v1/workspaces/${workspaceId}/personal-workbench/accesses`, {
    method: "POST",
    body: { document_id: documentId },
  });
}

/** 搜索获权且已发布文档；分页偏移由后端再次限制在 10000 内。 */
export function searchPublishedKnowledgeDocuments(
  workspaceId: string,
  parameters: KnowledgeSearchParameters,
  signal?: AbortSignal,
) {
  const query = new URLSearchParams({
    query: parameters.query.trim(),
    match_type: parameters.matchType,
    favorite_only: String(parameters.favoriteOnly),
    limit: String(parameters.limit),
    offset: String(parameters.offset),
  });
  if (parameters.knowledgeBaseId) query.set("knowledge_base_id", parameters.knowledgeBaseId);
  return apiRequest<KnowledgeSearchResponse>(
    `/api/v1/workspaces/${workspaceId}/knowledge-search?${query.toString()}`,
    { signal },
  );
}

/** 查询知识库内经过服务端资源范围和字段策略投影的文档摘要。 */
export async function getKnowledgeDocuments(
  workspaceId: string,
  knowledgeBaseId: string,
  signal?: AbortSignal,
) {
  const response = await apiRequest<components["schemas"]["KnowledgeDocumentListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/knowledge-bases/${knowledgeBaseId}/documents`,
    { signal },
  );
  // 新字段在 v1 契约中保持可选以兼容旧客户端，页面层只消费这里归一化后的确定结构。
  return response.items.map((document) => ({
    ...document,
    folder_id: document.folder_id ?? "",
    tag_ids: document.tag_ids ?? [],
    is_favorite: document.is_favorite ?? false,
  }));
}

/** 查询知识库入库任务，供页面轮询活动任务及展示稳定失败码。 */
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

/** 查询单篇文档详情；服务端按同一资源范围返回版本、解析与索引摘要。 */
export function getKnowledgeDocumentDetail(
  workspaceId: string,
  knowledgeBaseId: string,
  documentId: string,
  signal?: AbortSignal,
) {
  return apiRequest<KnowledgeDocumentDetail>(
    `/api/v1/workspaces/${workspaceId}/knowledge-bases/${knowledgeBaseId}/documents/${documentId}`,
    { signal },
  );
}

/** 下载指定不可变版本的原文件；对象存储定位信息不会到达浏览器。 */
export function downloadKnowledgeDocumentVersion(
  workspaceId: string,
  knowledgeBaseId: string,
  documentId: string,
  documentVersionId: string,
) {
  return apiDownloadRequest(
    `/api/v1/workspaces/${workspaceId}/knowledge-bases/${knowledgeBaseId}/documents/${documentId}/versions/${documentVersionId}/download`,
  );
}

/** 创建工作空间知识库；服务端负责名称、配额和写权限校验。 */
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

/** 上传文档首版并创建安全扫描与解析任务。 */
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

/** 为现有文档上传不可变新版本，不直接改变当前发布指针。 */
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

/** 将活动文档移入回收站；服务端负责资源授权、索引退出和审计。 */
export function deleteKnowledgeDocument(
  workspaceId: string,
  knowledgeBaseId: string,
  documentId: string,
) {
  return apiRequest<components["schemas"]["DocumentResponse"]>(
    `/api/v1/workspaces/${workspaceId}/knowledge-bases/${knowledgeBaseId}/documents/${documentId}`,
    { method: "DELETE" },
  );
}

/** 确认解析版本就绪；内容摘要和状态转换由服务端校验。 */
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

/** 发布已就绪版本并更新服务端当前版本指针。 */
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

/** 对后端允许重试的失败任务发起一次人工重新排队。 */
export function retryKnowledgeIngestionJob(workspaceId: string, ingestionJobId: string) {
  return apiRequest<IngestionJob>(
    `/api/v1/workspaces/${workspaceId}/ingestion-jobs/${ingestionJobId}/retry`,
    { method: "POST" },
  );
}
