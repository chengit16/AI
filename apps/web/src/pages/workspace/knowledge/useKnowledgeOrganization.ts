/**
 * @description 知识组织只读 Hook
 * 按当前工作空间查询目录、标签、个人收藏和回收站，权限参数只控制请求启用时机。
 */
import { useQuery } from "@tanstack/react-query";

import {
  getKnowledgeFavorites,
  getKnowledgeFolders,
  getKnowledgeTags,
  getKnowledgeTrash,
} from "@/api/services/knowledgeOrganization";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";

import { knowledgeQueryKeys } from "./queryKeys";

/** 知识组织查询的体验权限；服务端仍会对每个接口独立授权。 */
export interface KnowledgeOrganizationQueryOptions {
  /** 是否允许发起目录读取请求。 */
  canReadFolders: boolean;
  /** 是否允许发起标签读取请求。 */
  canReadTags: boolean;
  /** 是否允许发起当前成员收藏读取请求。 */
  canReadFavorites: boolean;
  /** 是否允许发起回收站读取请求。 */
  canReadTrash: boolean;
}

/** 返回当前工作空间完整组织读模型，删除项只在管理视图中派生展示。 */
export function useKnowledgeOrganization(options: KnowledgeOrganizationQueryOptions) {
  const { workspaceId } = useCurrentWorkspace();
  const folders = useQuery({
    queryKey: knowledgeQueryKeys.folders(workspaceId),
    queryFn: ({ signal }) => getKnowledgeFolders(workspaceId!, true, signal),
    enabled: Boolean(workspaceId && options.canReadFolders),
    retry: false,
  });
  const tags = useQuery({
    queryKey: knowledgeQueryKeys.tags(workspaceId),
    queryFn: ({ signal }) => getKnowledgeTags(workspaceId!, true, signal),
    enabled: Boolean(workspaceId && options.canReadTags),
    retry: false,
  });
  const favorites = useQuery({
    queryKey: knowledgeQueryKeys.favorites(workspaceId),
    queryFn: ({ signal }) => getKnowledgeFavorites(workspaceId!, signal),
    enabled: Boolean(workspaceId && options.canReadFavorites),
    retry: false,
  });
  const trash = useQuery({
    queryKey: knowledgeQueryKeys.trash(workspaceId),
    queryFn: ({ signal }) => getKnowledgeTrash(workspaceId!, signal),
    enabled: Boolean(workspaceId && options.canReadTrash),
    retry: false,
  });

  return { folders, tags, favorites, trash };
}
