/** @description 企业分类、团队知识域与可解释范围的类型化 API Service。 */
import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

/** 企业知识门户统一快照。 */
export type EnterpriseKnowledgePortal = components["schemas"]["EnterpriseKnowledgePortalResponse"];
/** 企业分类及显式文档绑定。 */
export type EnterpriseCategory = components["schemas"]["EnterpriseCategoryResponse"];
/** 团队知识域、当前 RAG 策略和声明范围。 */
export type TeamKnowledgeDomain = components["schemas"]["TeamKnowledgeDomainResponse"];
/** 知识域声明范围与当前 PDP 授权的交集解释。 */
export type ResolvedKnowledgeDomainScope =
  components["schemas"]["ResolvedKnowledgeDomainScopeResponse"];
/** 创建企业分类输入。 */
export type CreateEnterpriseCategory = components["schemas"]["CreateEnterpriseCategoryRequest"];
/** 更新企业分类输入。 */
export type UpdateEnterpriseCategory = components["schemas"]["UpdateEnterpriseCategoryRequest"];
/** 创建团队知识域输入。 */
export type CreateTeamKnowledgeDomain = components["schemas"]["CreateTeamKnowledgeDomainRequest"];
/** 更新团队知识域输入。 */
export type UpdateTeamKnowledgeDomain = components["schemas"]["UpdateTeamKnowledgeDomainRequest"];
/** 原子替换团队知识域范围输入。 */
export type ReplaceKnowledgeDomainScope =
  components["schemas"]["ReplaceKnowledgeDomainScopeRequest"];

/** 读取服务端已执行密级与字段裁剪的企业知识统一快照。 */
export function getEnterpriseKnowledgePortal(
  workspaceId: string,
  signal?: AbortSignal,
): Promise<EnterpriseKnowledgePortal> {
  return apiRequest<EnterpriseKnowledgePortal>(
    "/api/v1/workspaces/" + workspaceId + "/enterprise-knowledge",
    { signal },
  );
}

/** 创建分类及初始文档绑定。 */
export function createEnterpriseCategory(workspaceId: string, body: CreateEnterpriseCategory) {
  return apiRequest<EnterpriseCategory>(
    "/api/v1/workspaces/" + workspaceId + "/enterprise-categories",
    { method: "POST", body },
  );
}

/** 按乐观版本更新分类元数据与可见策略。 */
export function updateEnterpriseCategory(
  workspaceId: string,
  categoryId: string,
  body: UpdateEnterpriseCategory,
) {
  return apiRequest<EnterpriseCategory>(
    "/api/v1/workspaces/" + workspaceId + "/enterprise-categories/" + categoryId,
    { method: "PUT", body },
  );
}

/** 归档无活动子分类的企业分类。 */
export function archiveEnterpriseCategory(
  workspaceId: string,
  categoryId: string,
  expectedVersion: number,
) {
  return apiRequest<EnterpriseCategory>(
    "/api/v1/workspaces/" + workspaceId + "/enterprise-categories/" + categoryId + "/archive",
    { method: "POST", body: { expected_version: expectedVersion } },
  );
}

/** 整体替换分类文档关系，不改变文档自身权限或内容事实。 */
export function replaceEnterpriseCategoryDocuments(
  workspaceId: string,
  categoryId: string,
  expectedVersion: number,
  documentIds: readonly string[],
) {
  return apiRequest<EnterpriseCategory>(
    "/api/v1/workspaces/" + workspaceId + "/enterprise-categories/" + categoryId + "/documents",
    { method: "PUT", body: { expected_version: expectedVersion, document_ids: documentIds } },
  );
}

/** 创建团队知识域、初始范围和首个不可变 RAG 策略版本。 */
export function createTeamKnowledgeDomain(workspaceId: string, body: CreateTeamKnowledgeDomain) {
  return apiRequest<TeamKnowledgeDomain>(
    "/api/v1/workspaces/" + workspaceId + "/team-knowledge-domains",
    { method: "POST", body },
  );
}

/** 更新知识域元数据；RAG 参数变化时由服务端追加策略版本。 */
export function updateTeamKnowledgeDomain(
  workspaceId: string,
  domainId: string,
  body: UpdateTeamKnowledgeDomain,
) {
  return apiRequest<TeamKnowledgeDomain>(
    "/api/v1/workspaces/" + workspaceId + "/team-knowledge-domains/" + domainId,
    { method: "PUT", body },
  );
}

/** 归档知识域，使其运行范围立即失败关闭。 */
export function archiveTeamKnowledgeDomain(
  workspaceId: string,
  domainId: string,
  expectedVersion: number,
) {
  return apiRequest<TeamKnowledgeDomain>(
    "/api/v1/workspaces/" + workspaceId + "/team-knowledge-domains/" + domainId + "/archive",
    { method: "POST", body: { expected_version: expectedVersion } },
  );
}

/** 原子替换成员、部门和知识库范围，浏览器不拆分为多次写入。 */
export function replaceTeamKnowledgeDomainScope(
  workspaceId: string,
  domainId: string,
  body: ReplaceKnowledgeDomainScope,
) {
  return apiRequest<TeamKnowledgeDomain>(
    "/api/v1/workspaces/" + workspaceId + "/team-knowledge-domains/" + domainId + "/scope",
    { method: "PUT", body },
  );
}

/** 读取知识域声明范围与当前 PDP 授权交集的可解释结果。 */
export function resolveTeamKnowledgeDomainScope(
  workspaceId: string,
  domainId: string,
  signal?: AbortSignal,
) {
  return apiRequest<ResolvedKnowledgeDomainScope>(
    "/api/v1/workspaces/" + workspaceId + "/team-knowledge-domains/" + domainId + "/resolved-scope",
    { signal },
  );
}
