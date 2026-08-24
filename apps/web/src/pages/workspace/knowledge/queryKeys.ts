/** @description 知识生产与组织读模型的 TanStack Query Key 工厂。 */

/** 统一知识页面缓存范围，保证空间切换和组织写入不会留下局部陈旧数据。 */
export const knowledgeQueryKeys = {
  bases: (workspaceId: string | null) => ["knowledge-bases", workspaceId] as const,
  documents: (workspaceId: string | null, knowledgeBaseId?: string | null) =>
    knowledgeBaseId === undefined
      ? (["knowledge-documents", workspaceId] as const)
      : (["knowledge-documents", workspaceId, knowledgeBaseId] as const),
  jobs: (workspaceId: string | null, knowledgeBaseId?: string | null) =>
    knowledgeBaseId === undefined
      ? (["knowledge-ingestion-jobs", workspaceId] as const)
      : (["knowledge-ingestion-jobs", workspaceId, knowledgeBaseId] as const),
  folders: (workspaceId: string | null) => ["knowledge-folders", workspaceId] as const,
  tags: (workspaceId: string | null) => ["knowledge-tags", workspaceId] as const,
  favorites: (workspaceId: string | null) => ["knowledge-favorites", workspaceId] as const,
  trash: (workspaceId: string | null) => ["knowledge-trash", workspaceId] as const,
};
