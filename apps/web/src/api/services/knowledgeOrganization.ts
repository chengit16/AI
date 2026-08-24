/**
 * @description 知识目录、标签、收藏和回收站 API Service
 * 只映射冻结契约；工作空间、资源授权、状态冲突和审计均由服务端执行。
 */
import { apiRequest } from "@/api/client";
import type { components } from "@/api/generated/platform-api.v1";

/** 工作空间内可恢复的目录事实。 */
export type KnowledgeFolder = components["schemas"]["KnowledgeFolderResponse"];
/** 工作空间内不参与授权的标签事实。 */
export type KnowledgeTag = components["schemas"]["KnowledgeTagResponse"];
/** 回收站中的文档事实，不包含正文和对象存储定位信息。 */
export type KnowledgeTrashDocument = components["schemas"]["DocumentResponse"];
/** 创建目录时允许提交的名称和父目录。 */
export type CreateKnowledgeFolderRequest = components["schemas"]["CreateKnowledgeFolderRequest"];
/** 创建标签时允许提交的名称和展示颜色。 */
export type CreateKnowledgeTagRequest = components["schemas"]["CreateKnowledgeTagRequest"];
/** 更新标签时允许提交的名称和展示颜色。 */
export type UpdateKnowledgeTagRequest = components["schemas"]["UpdateKnowledgeTagRequest"];

/** 查询目录；显式包含删除项时仅用于有权限的回收站视图。 */
export async function getKnowledgeFolders(
  workspaceId: string,
  includeDeleted: boolean,
  signal?: AbortSignal,
) {
  const response = await apiRequest<components["schemas"]["KnowledgeFolderListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/knowledge-folders?include_deleted=${includeDeleted}`,
    { signal },
  );
  return response.items;
}

/** 创建普通目录；空父级由服务端解析到固定默认根目录。 */
export function createKnowledgeFolder(workspaceId: string, body: CreateKnowledgeFolderRequest) {
  return apiRequest<KnowledgeFolder>(`/api/v1/workspaces/${workspaceId}/knowledge-folders`, {
    method: "POST",
    body,
  });
}

/** 重命名普通目录；固定默认根目录不可修改。 */
export function renameKnowledgeFolder(workspaceId: string, folderId: string, name: string) {
  return apiRequest<KnowledgeFolder>(
    `/api/v1/workspaces/${workspaceId}/knowledge-folders/${folderId}`,
    { method: "PATCH", body: { name } },
  );
}

/** 移动普通目录；服务端拒绝循环、跨空间和同级重名关系。 */
export function moveKnowledgeFolder(
  workspaceId: string,
  folderId: string,
  parentFolderId: string | null,
) {
  return apiRequest<KnowledgeFolder>(
    `/api/v1/workspaces/${workspaceId}/knowledge-folders/${folderId}/move`,
    { method: "POST", body: { parent_folder_id: parentFolderId } },
  );
}

/** 将空普通目录软删除到回收站。 */
export function deleteKnowledgeFolder(workspaceId: string, folderId: string) {
  return apiRequest<KnowledgeFolder>(
    `/api/v1/workspaces/${workspaceId}/knowledge-folders/${folderId}`,
    { method: "DELETE" },
  );
}

/** 恢复父目录仍活动且名称不冲突的目录。 */
export function restoreKnowledgeFolder(workspaceId: string, folderId: string) {
  return apiRequest<KnowledgeFolder>(
    `/api/v1/workspaces/${workspaceId}/knowledge-folders/${folderId}/restore`,
    { method: "POST" },
  );
}

/** 永久删除回收站中的空目录；成功响应没有正文。 */
export function purgeKnowledgeFolder(workspaceId: string, folderId: string) {
  return apiRequest<void>(
    `/api/v1/workspaces/${workspaceId}/knowledge-folders/${folderId}/permanent`,
    { method: "DELETE" },
  );
}

/** 查询标签；显式包含删除项时用于标签恢复管理。 */
export async function getKnowledgeTags(
  workspaceId: string,
  includeDeleted: boolean,
  signal?: AbortSignal,
) {
  const response = await apiRequest<components["schemas"]["KnowledgeTagListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/knowledge-tags?include_deleted=${includeDeleted}`,
    { signal },
  );
  return response.items;
}

/** 创建工作空间标签；颜色只参与展示，不影响数据权限。 */
export function createKnowledgeTag(workspaceId: string, body: CreateKnowledgeTagRequest) {
  return apiRequest<KnowledgeTag>(`/api/v1/workspaces/${workspaceId}/knowledge-tags`, {
    method: "POST",
    body,
  });
}

/** 更新标签名称和展示颜色。 */
export function updateKnowledgeTag(
  workspaceId: string,
  tagId: string,
  body: UpdateKnowledgeTagRequest,
) {
  return apiRequest<KnowledgeTag>(`/api/v1/workspaces/${workspaceId}/knowledge-tags/${tagId}`, {
    method: "PATCH",
    body,
  });
}

/** 软删除标签；文档及其内容事实保持不变。 */
export function deleteKnowledgeTag(workspaceId: string, tagId: string) {
  return apiRequest<KnowledgeTag>(`/api/v1/workspaces/${workspaceId}/knowledge-tags/${tagId}`, {
    method: "DELETE",
  });
}

/** 恢复名称仍可用的标签。 */
export function restoreKnowledgeTag(workspaceId: string, tagId: string) {
  return apiRequest<KnowledgeTag>(
    `/api/v1/workspaces/${workspaceId}/knowledge-tags/${tagId}/restore`,
    { method: "POST" },
  );
}

/** 以替换语义设置文档唯一主目录。 */
export function bindKnowledgeDocumentFolder(
  workspaceId: string,
  documentId: string,
  folderId: string,
) {
  return apiRequest<components["schemas"]["KnowledgeDocumentFolderBindingResponse"]>(
    `/api/v1/workspaces/${workspaceId}/documents/${documentId}/folders`,
    { method: "POST", body: { ids: [folderId] } },
  );
}

/** 幂等增加一组文档标签绑定。 */
export function bindKnowledgeDocumentTags(
  workspaceId: string,
  documentId: string,
  tagIds: readonly string[],
) {
  return apiRequest<components["schemas"]["KnowledgeDocumentTagBindingResponse"]>(
    `/api/v1/workspaces/${workspaceId}/documents/${documentId}/tags`,
    { method: "POST", body: { ids: tagIds } },
  );
}

/** 解除一个文档标签绑定，服务端显式验证标签空间归属。 */
export function unbindKnowledgeDocumentTag(workspaceId: string, documentId: string, tagId: string) {
  return apiRequest<components["schemas"]["KnowledgeDocumentTagBindingResponse"]>(
    `/api/v1/workspaces/${workspaceId}/documents/${documentId}/tags/${tagId}`,
    { method: "DELETE" },
  );
}

/** 设置当前成员的文档收藏状态。 */
export function setKnowledgeDocumentFavorite(
  workspaceId: string,
  documentId: string,
  favorite: boolean,
) {
  return apiRequest<components["schemas"]["KnowledgeFavoriteResponse"]>(
    `/api/v1/workspaces/${workspaceId}/documents/${documentId}/favorite`,
    { method: "PUT", body: { favorite } },
  );
}

/** 查询当前成员的活动文档收藏 ID，结果不会扩大文档读取权限。 */
export async function getKnowledgeFavorites(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["KnowledgeFavoriteListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/favorites`,
    { signal },
  );
  return response.document_ids;
}

/** 查询工作空间回收站文档，服务端继续执行资源级投影。 */
export async function getKnowledgeTrash(workspaceId: string, signal?: AbortSignal) {
  const response = await apiRequest<components["schemas"]["KnowledgeTrashListResponse"]>(
    `/api/v1/workspaces/${workspaceId}/trash`,
    { signal },
  );
  return response.items;
}

/** 恢复回收站文档；原目录不可用时由服务端回落到默认根目录。 */
export function restoreKnowledgeDocument(workspaceId: string, documentId: string) {
  return apiRequest<KnowledgeTrashDocument>(
    `/api/v1/workspaces/${workspaceId}/trash/documents/${documentId}/restore`,
    { method: "POST" },
  );
}

/** 永久删除回收站文档并触发外部对象清理意图。 */
export function purgeKnowledgeDocument(workspaceId: string, documentId: string) {
  return apiRequest<void>(
    `/api/v1/workspaces/${workspaceId}/trash/documents/${documentId}/permanent`,
    { method: "DELETE" },
  );
}
