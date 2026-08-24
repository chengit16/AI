/**
 * @description 知识组织写操作 Hook
 * 编排目录、标签、收藏和回收站命令，并刷新受影响的服务端事实缓存。
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";

import { errorMessage } from "@/api/client";
import { deleteKnowledgeDocument, type KnowledgeDocumentSummary } from "@/api/services/knowledge";
import {
  bindKnowledgeDocumentFolder,
  bindKnowledgeDocumentTags,
  createKnowledgeFolder,
  createKnowledgeTag,
  deleteKnowledgeFolder,
  deleteKnowledgeTag,
  moveKnowledgeFolder,
  purgeKnowledgeDocument,
  purgeKnowledgeFolder,
  renameKnowledgeFolder,
  restoreKnowledgeDocument,
  restoreKnowledgeFolder,
  restoreKnowledgeTag,
  setKnowledgeDocumentFavorite,
  unbindKnowledgeDocumentTag,
  updateKnowledgeTag,
  type CreateKnowledgeFolderRequest,
  type CreateKnowledgeTagRequest,
  type UpdateKnowledgeTagRequest,
} from "@/api/services/knowledgeOrganization";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";

import { knowledgeQueryKeys } from "./queryKeys";

type FolderCommand =
  | { type: "create"; body: CreateKnowledgeFolderRequest }
  | { type: "rename"; folderId: string; name: string }
  | { type: "move"; folderId: string; parentFolderId: string | null }
  | { type: "delete"; folderId: string }
  | { type: "restore"; folderId: string }
  | { type: "purge"; folderId: string };

type TagCommand =
  | { type: "create"; body: CreateKnowledgeTagRequest }
  | { type: "update"; tagId: string; body: UpdateKnowledgeTagRequest }
  | { type: "delete"; tagId: string }
  | { type: "restore"; tagId: string };

interface MoveDocumentsCommand {
  /** 待移动且已由列表授权投影的文档 ID。 */
  documentIds: readonly string[];
  /** 目标活动目录 ID。 */
  folderId: string;
}

interface ReplaceDocumentTagsCommand {
  /** 待更新且携带当前标签快照的文档。 */
  documents: readonly KnowledgeDocumentSummary[];
  /** 所有选中文档最终应拥有的标签 ID。 */
  tagIds: readonly string[];
  /** 批量追加时保留每篇文档已有标签，单篇编辑时使用替换语义。 */
  preserveExisting: boolean;
}

interface FavoriteCommand {
  /** 待切换收藏状态的活动文档 ID。 */
  documentId: string;
  /** 目标收藏状态。 */
  favorite: boolean;
}

interface DeleteDocumentCommand {
  /** 文档当前所属知识库 ID。 */
  knowledgeBaseId: string;
  /** 待移入回收站的活动文档 ID。 */
  documentIds: readonly string[];
}

type TrashCommand = { type: "restore"; documentId: string } | { type: "purge"; documentId: string };

const folderSuccessMessages: Record<FolderCommand["type"], string> = {
  create: "文件夹已创建",
  rename: "文件夹已重命名",
  move: "文件夹已移动",
  delete: "文件夹已移入回收站",
  restore: "文件夹已恢复",
  purge: "文件夹已永久删除",
};

const tagSuccessMessages: Record<TagCommand["type"], string> = {
  create: "标签已创建",
  update: "标签已更新",
  delete: "标签已移入回收站",
  restore: "标签已恢复",
};

/**
 * 返回知识组织命令和统一提交状态。
 *
 * 组织关系变化会刷新目录/标签及全部知识库文档摘要；删除和恢复还会刷新回收站与
 * 知识库统计，确保 URL 筛选、收藏计数和列表操作读取同一服务端事实。
 */
export function useKnowledgeOrganizationActions() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { workspaceId } = useCurrentWorkspace();
  const notifyError = (error: unknown) => void message.error(errorMessage(error));

  // 1. 先固定跨知识库缓存刷新集合，组织写入后不保留旧目录、收藏或回收站摘要。
  const refreshDocuments = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: knowledgeQueryKeys.bases(workspaceId) }),
      queryClient.invalidateQueries({ queryKey: knowledgeQueryKeys.documents(workspaceId) }),
      queryClient.invalidateQueries({ queryKey: knowledgeQueryKeys.favorites(workspaceId) }),
      queryClient.invalidateQueries({ queryKey: knowledgeQueryKeys.trash(workspaceId) }),
    ]);
  // 2. 再按领域命令拆分 Mutation；每个接口仍由服务端执行空间和资源级授权。
  const folderCommand = useMutation({
    mutationFn: async (command: FolderCommand) => {
      switch (command.type) {
        case "create":
          await createKnowledgeFolder(workspaceId!, command.body);
          break;
        case "rename":
          await renameKnowledgeFolder(workspaceId!, command.folderId, command.name);
          break;
        case "move":
          await moveKnowledgeFolder(workspaceId!, command.folderId, command.parentFolderId);
          break;
        case "delete":
          await deleteKnowledgeFolder(workspaceId!, command.folderId);
          break;
        case "restore":
          await restoreKnowledgeFolder(workspaceId!, command.folderId);
          break;
        case "purge":
          await purgeKnowledgeFolder(workspaceId!, command.folderId);
          break;
      }
    },
    onSuccess: async (_, command) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: knowledgeQueryKeys.folders(workspaceId) }),
        refreshDocuments(),
      ]);
      void message.success(folderSuccessMessages[command.type]);
    },
    onError: notifyError,
  });
  const tagCommand = useMutation({
    mutationFn: (command: TagCommand) => {
      switch (command.type) {
        case "create":
          return createKnowledgeTag(workspaceId!, command.body);
        case "update":
          return updateKnowledgeTag(workspaceId!, command.tagId, command.body);
        case "delete":
          return deleteKnowledgeTag(workspaceId!, command.tagId);
        case "restore":
          return restoreKnowledgeTag(workspaceId!, command.tagId);
      }
    },
    onSuccess: async (_, command) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: knowledgeQueryKeys.tags(workspaceId) }),
        queryClient.invalidateQueries({ queryKey: knowledgeQueryKeys.documents(workspaceId) }),
      ]);
      void message.success(tagSuccessMessages[command.type]);
    },
    onError: notifyError,
  });
  const moveDocuments = useMutation({
    mutationFn: (command: MoveDocumentsCommand) =>
      Promise.all(
        command.documentIds.map((documentId) =>
          bindKnowledgeDocumentFolder(workspaceId!, documentId, command.folderId),
        ),
      ),
    onSuccess: async () => {
      await refreshDocuments();
      void message.success("文档已移动");
    },
    onError: notifyError,
  });
  const replaceDocumentTags = useMutation({
    mutationFn: async (command: ReplaceDocumentTagsCommand) => {
      const targetIds = new Set(command.tagIds);
      // 每个文档的现有标签可能不同；先增加缺失关系，再移除多余关系，失败时不伪装整体成功。
      await Promise.all(
        command.documents.map(async (document) => {
          const currentIds = new Set(document.tag_ids);
          const additions = command.tagIds.filter((tagId) => !currentIds.has(tagId));
          const removals = command.preserveExisting
            ? []
            : document.tag_ids.filter((tagId) => !targetIds.has(tagId));
          await Promise.all([
            additions.length
              ? bindKnowledgeDocumentTags(workspaceId!, document.document_id, additions)
              : Promise.resolve(),
            ...removals.map((tagId) =>
              unbindKnowledgeDocumentTag(workspaceId!, document.document_id, tagId),
            ),
          ]);
        }),
      );
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: knowledgeQueryKeys.documents(workspaceId) });
      void message.success("文档标签已更新");
    },
    onError: notifyError,
  });
  const favorite = useMutation({
    mutationFn: (command: FavoriteCommand) =>
      setKnowledgeDocumentFavorite(workspaceId!, command.documentId, command.favorite),
    onSuccess: async (_, command) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: knowledgeQueryKeys.documents(workspaceId) }),
        queryClient.invalidateQueries({ queryKey: knowledgeQueryKeys.favorites(workspaceId) }),
      ]);
      void message.success(command.favorite ? "已收藏文档" : "已取消收藏");
    },
    onError: notifyError,
  });
  const deleteDocuments = useMutation({
    mutationFn: (command: DeleteDocumentCommand) =>
      Promise.all(
        command.documentIds.map((documentId) =>
          deleteKnowledgeDocument(workspaceId!, command.knowledgeBaseId, documentId),
        ),
      ),
    onSuccess: async () => {
      await refreshDocuments();
      void message.success("文档已移入回收站");
    },
    onError: notifyError,
  });
  const trashCommand = useMutation({
    mutationFn: async (command: TrashCommand) => {
      if (command.type === "restore") {
        await restoreKnowledgeDocument(workspaceId!, command.documentId);
      } else {
        await purgeKnowledgeDocument(workspaceId!, command.documentId);
      }
    },
    onSuccess: async (_, command) => {
      await refreshDocuments();
      void message.success(command.type === "restore" ? "文档已恢复" : "文档已永久删除");
    },
    onError: notifyError,
  });

  return {
    folderCommand,
    tagCommand,
    moveDocuments,
    replaceDocumentTags,
    favorite,
    deleteDocuments,
    trashCommand,
  };
}
