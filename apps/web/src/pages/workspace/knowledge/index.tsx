/** @description 知识库页面编排，组合目录、标签、收藏、回收站、文档版本和入库任务入口。 */
import { Alert, App, Button, Tabs } from "antd";
import { Plus, Upload } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";

import { errorMessage } from "@/api/client";
import type { KnowledgeDocumentSummary } from "@/api/services/knowledge";
import type { KnowledgeFolder } from "@/api/services/knowledgeOrganization";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";

import { DocumentListToolbar } from "./components/DocumentListToolbar";
import { DocumentCardGrid } from "./components/DocumentCardGrid";
import { DocumentDetailDrawer } from "./components/DocumentDetailDrawer";
import {
  DocumentOrganizationDialog,
  type DocumentOrganizationMode,
} from "./components/DocumentOrganizationDialog";
import { DocumentTable } from "./components/DocumentTable";
import { FolderDialog } from "./components/FolderDialog";
import { IngestionTable } from "./components/IngestionTable";
import { KnowledgeDialogs } from "./components/KnowledgeDialogs";
import {
  KnowledgeNavigationRail,
  type FolderEditAction,
  type KnowledgeViewScope,
} from "./components/KnowledgeNavigationRail";
import { TagManagerDrawer } from "./components/TagManagerDrawer";
import { TrashPanel } from "./components/TrashPanel";
import { useKnowledgeOrganization } from "./useKnowledgeOrganization";
import { useKnowledgeOrganizationActions } from "./useKnowledgeOrganizationActions";
import { useKnowledgeProduction } from "./useKnowledgeProduction";

interface FolderDialogState {
  action: FolderEditAction;
  folder: KnowledgeFolder | null;
}

interface OrganizationDialogState {
  mode: DocumentOrganizationMode;
  documents: readonly KnowledgeDocumentSummary[];
}

/**
 * 展示当前空间的文件型知识管理闭环。
 *
 * 长函数保留原因：页面入口只组合 URL 状态、领域 Hook 与私有组件；拆散这些路由映射会让
 * 同一筛选状态出现多个写入点。权限只裁剪体验，所有资源关系仍由后端重新授权。
 */
export default function KnowledgeProductionPage() {
  const { modal } = App.useApp();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedBaseId = searchParams.get("base");
  const activeTab = searchParams.get("view") === "jobs" ? "jobs" : "documents";
  const scopeValue = searchParams.get("scope");
  const scope: KnowledgeViewScope =
    scopeValue === "favorites" || scopeValue === "trash" ? scopeValue : "documents";
  const selectedFolderId = searchParams.get("folder");
  const selectedTagId = searchParams.get("tag");
  const searchText = searchParams.get("q") ?? "";
  const requestedDetailId = searchParams.get("document");
  const requestedAction = searchParams.get("action");
  const documentViewMode = searchParams.get("layout") === "cards" ? "cards" : "list";
  const [createOpen, setCreateOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [versionDocument, setVersionDocument] = useState<KnowledgeDocumentSummary | null>(null);
  const [folderDialog, setFolderDialog] = useState<FolderDialogState | null>(null);
  const [tagManagerOpen, setTagManagerOpen] = useState(false);
  const [organizationDialog, setOrganizationDialog] = useState<OrganizationDialogState | null>(
    null,
  );
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<readonly string[]>([]);
  const { visiblePermissionCodes } = useWorkspaceMenuNavigation();
  const has = (permissionCode: string) => visiblePermissionCodes.has(permissionCode);
  const canCreateDocument = has("knowledge.document.create");
  const canCreateFolder = has("knowledge.folder.create");
  const detailDocumentId = has("knowledge.document.read") ? requestedDetailId : null;

  // 1. 页面 Query 只在对应菜单权限存在时启动，避免用失败请求猜测授权结果。
  const model = useKnowledgeProduction(selectedBaseId, detailDocumentId);
  const organization = useKnowledgeOrganization({
    canReadFolders: has("knowledge.folder.read"),
    canReadTags: has("knowledge.tag.read"),
    canReadFavorites: has("knowledge.document.favorite"),
    canReadTrash: has("knowledge.trash.read"),
  });
  const actions = useKnowledgeOrganizationActions();
  const activeFolders = (organization.folders.data ?? []).filter(
    (folder) => folder.status === "active",
  );
  const deletedFolders = (organization.folders.data ?? []).filter(
    (folder) => folder.status === "deleted",
  );
  const activeTags = (organization.tags.data ?? []).filter((tag) => tag.status === "active");
  const favoriteIds = new Set(organization.favorites.data ?? []);
  const trashCount = (organization.trash.data?.length ?? 0) + deletedFolders.length;
  const documentEmptyState =
    scope === "favorites"
      ? { title: "还没有收藏文档", description: "收藏的文档会出现在这里。" }
      : selectedFolderId || selectedTagId || searchText
        ? { title: "没有匹配的文档", description: "调整搜索、标签或文件夹筛选后重试。" }
        : {
            title: "还没有文档",
            description: "上传首份文档后，可以在这里跟踪解析和发布状态。",
          };

  const updateSearch = (updates: Record<string, string | null>) => {
    const next = new URLSearchParams(searchParams);
    Object.entries(updates).forEach(([key, value]) => {
      if (value) next.set(key, value);
      else next.delete(key);
    });
    setSearchParams(next);
  };

  useEffect(() => {
    if (!selectedBaseId && model.bases.data?.[0]) {
      const next = new URLSearchParams(searchParams);
      next.set("base", model.bases.data[0].knowledge_base_id);
      setSearchParams(next, { replace: true });
    }
  }, [model.bases.data, searchParams, selectedBaseId, setSearchParams]);

  useEffect(() => {
    // 工作台快捷入口只打开既有流程；URL 动作消费后立即移除，刷新不会重复弹窗。
    const openUpload = requestedAction === "upload" && selectedBaseId && canCreateDocument;
    const openFolder = requestedAction === "folder" && canCreateFolder;
    if (!openUpload && !openFolder) return;
    // URL 是外部路由状态，延迟到当前提交完成后再同步本地弹窗，避免 Effect 内级联渲染。
    const timer = window.setTimeout(() => {
      if (openUpload) setUploadOpen(true);
      if (openFolder) setFolderDialog({ action: "create", folder: null });
      const next = new URLSearchParams(searchParams);
      next.delete("action");
      setSearchParams(next, { replace: true });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [
    canCreateDocument,
    canCreateFolder,
    requestedAction,
    searchParams,
    selectedBaseId,
    setSearchParams,
  ]);

  // 2. URL 中的目录、收藏、标签和名称条件只过滤已授权摘要，不参与扩大服务端查询范围。
  const filteredDocuments = (model.documents.data ?? []).filter((document) => {
    const folderMatches = !selectedFolderId || document.folder_id === selectedFolderId;
    const favoriteMatches = scope !== "favorites" || favoriteIds.has(document.document_id);
    const tagMatches = !selectedTagId || document.tag_ids.includes(selectedTagId);
    const textMatches = document.title.toLocaleLowerCase().includes(searchText.toLocaleLowerCase());
    return folderMatches && favoriteMatches && tagMatches && textMatches;
  });
  const selectedDocuments = filteredDocuments.filter((document) =>
    selectedDocumentIds.includes(document.document_id),
  );
  const isOrganizationPending =
    actions.moveDocuments.isPending || actions.replaceDocumentTags.isPending;
  const isDocumentMutating =
    model.markReady.isPending ||
    model.publish.isPending ||
    actions.favorite.isPending ||
    actions.deleteDocuments.isPending;
  const organizationError = organization.folders.error ?? organization.tags.error;

  if (model.bases.isError) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="知识库未能加载"
        description={errorMessage(model.bases.error)}
        action={<Button onClick={() => void model.bases.refetch()}>重新加载</Button>}
      />
    );
  }

  // 3. 主视图保持知识生产状态机，并在同一页面叠加组织与回收站操作。
  return (
    <>
      <PageHeader
        eyebrow="KNOWLEDGE"
        title="知识库"
        description="上传、组织和发布文件型知识，并跟踪解析与索引状态。"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {has("knowledge.base.create") && (
              <Button icon={<Plus size={16} />} onClick={() => setCreateOpen(true)}>
                新建知识库
              </Button>
            )}
            {selectedBaseId && scope !== "trash" && canCreateDocument && (
              <Button
                type="primary"
                icon={<Upload size={17} />}
                onClick={() => setUploadOpen(true)}
              >
                上传文档
              </Button>
            )}
          </div>
        }
      />
      <div className="ui-surface-panel grid min-h-[620px] grid-cols-[260px_minmax(0,1fr)] overflow-hidden tablet-down:grid-cols-1">
        <KnowledgeNavigationRail
          bases={model.bases.data ?? []}
          selectedBaseId={selectedBaseId}
          folders={activeFolders}
          documents={model.documents.data ?? []}
          selectedFolderId={scope === "documents" ? selectedFolderId : null}
          scope={scope}
          isLoading={model.bases.isLoading || organization.folders.isLoading}
          favoriteCount={
            (model.documents.data ?? []).filter((document) => document.is_favorite).length
          }
          trashCount={trashCount}
          canCreateFolder={has("knowledge.folder.create")}
          canUpdateFolder={has("knowledge.folder.update")}
          canDeleteFolder={has("knowledge.folder.delete")}
          canReadTags={has("knowledge.tag.read")}
          onSelectBase={(base) => {
            setSelectedDocumentIds([]);
            updateSearch({
              base,
              scope: "documents",
              folder: null,
              view: "documents",
              document: null,
            });
          }}
          onSelectFolder={(folderId) => {
            setSelectedDocumentIds([]);
            const isDefault = activeFolders.some(
              (folder) => folder.folder_id === folderId && folder.is_default,
            );
            updateSearch({
              scope: "documents",
              folder: isDefault ? null : folderId,
              view: "documents",
            });
          }}
          onSelectScope={(nextScope) => {
            setSelectedDocumentIds([]);
            updateSearch({ scope: nextScope, folder: null, view: "documents" });
          }}
          onEditFolder={(action, folder) => setFolderDialog({ action, folder })}
          onDeleteFolder={(folder) =>
            modal.confirm({
              title: `删除文件夹“${folder.name}”？`,
              content: "只有空文件夹可以移入回收站。",
              okText: "删除",
              cancelText: "取消",
              okButtonProps: { danger: true },
              onOk: () =>
                actions.folderCommand.mutateAsync({
                  type: "delete",
                  folderId: folder.folder_id,
                }),
            })
          }
          onManageTags={() => setTagManagerOpen(true)}
        />
        <section
          className="min-w-0 px-5 pb-5 phone-down:px-3 phone-down:pb-3"
          aria-label="知识内容"
        >
          {organizationError && (
            <Alert
              className="mt-4"
              type="warning"
              showIcon
              message="部分组织信息未能加载"
              description={errorMessage(organizationError)}
            />
          )}
          {scope === "trash" ? (
            <TrashPanel
              documents={organization.trash.data ?? []}
              folders={deletedFolders}
              isLoading={organization.trash.isLoading || organization.folders.isLoading}
              errorDescription={
                organization.trash.isError ? errorMessage(organization.trash.error) : null
              }
              canRestoreDocument={has("knowledge.trash.restore")}
              canPurgeDocument={has("knowledge.trash.purge")}
              canRestoreFolder={has("knowledge.folder.restore")}
              canPurgeFolder={has("knowledge.folder.purge")}
              isPending={actions.trashCommand.isPending || actions.folderCommand.isPending}
              onRetry={() => void organization.trash.refetch()}
              onRestoreDocument={(documentId) =>
                actions.trashCommand.mutate({ type: "restore", documentId })
              }
              onPurgeDocument={(documentId) =>
                actions.trashCommand.mutate({ type: "purge", documentId })
              }
              onRestoreFolder={(folderId) =>
                actions.folderCommand.mutate({ type: "restore", folderId })
              }
              onPurgeFolder={(folderId) =>
                actions.folderCommand.mutate({ type: "purge", folderId })
              }
            />
          ) : !selectedBaseId ? (
            <StateView kind="empty" title="选择一个知识库" description="先选择或创建知识库。" />
          ) : model.documents.isError ? (
            <StateView
              kind="error"
              title="文档未能加载"
              description={errorMessage(model.documents.error)}
              action={<Button onClick={() => void model.documents.refetch()}>重新加载</Button>}
            />
          ) : (
            <Tabs
              activeKey={activeTab}
              onChange={(view) => updateSearch({ view })}
              items={[
                {
                  key: "documents",
                  label: `文档 ${filteredDocuments.length}`,
                  children: (
                    <>
                      <DocumentListToolbar
                        searchText={searchText}
                        selectedTagId={selectedTagId}
                        tags={activeTags}
                        selectedCount={selectedDocuments.length}
                        viewMode={documentViewMode}
                        canOrganize={
                          has("knowledge.document.folder.bind") ||
                          has("knowledge.document.tag.bind")
                        }
                        canDelete={has("knowledge.document.delete")}
                        onSearchChange={(q) => updateSearch({ q: q || null })}
                        onViewModeChange={(layout) =>
                          updateSearch({ layout: layout === "cards" ? "cards" : null })
                        }
                        onTagChange={(tag) => updateSearch({ tag })}
                        onClearSelection={() => setSelectedDocumentIds([])}
                        onMoveSelected={() =>
                          setOrganizationDialog({ mode: "move", documents: selectedDocuments })
                        }
                        onTagSelected={() =>
                          setOrganizationDialog({ mode: "tags", documents: selectedDocuments })
                        }
                        onDeleteSelected={() =>
                          actions.deleteDocuments.mutate({
                            knowledgeBaseId: selectedBaseId,
                            documentIds: selectedDocumentIds,
                          })
                        }
                      />
                      {documentViewMode === "cards" ? (
                        <DocumentCardGrid
                          items={filteredDocuments}
                          jobs={model.jobs.data ?? []}
                          folders={activeFolders}
                          tags={activeTags}
                          selectedDocumentIds={selectedDocumentIds}
                          isLoading={model.documents.isLoading}
                          emptyTitle={documentEmptyState.title}
                          emptyDescription={documentEmptyState.description}
                          permissions={{
                            readDetails: has("knowledge.document.read"),
                            uploadVersion: has("knowledge.document.version.create"),
                            markReady: has("knowledge.document.version.ready"),
                            publish: has("knowledge.document.version.publish"),
                            organize:
                              has("knowledge.document.folder.bind") ||
                              has("knowledge.document.tag.bind"),
                            favorite: has("knowledge.document.favorite"),
                            delete: has("knowledge.document.delete"),
                          }}
                          isMutating={isDocumentMutating}
                          onSelectionChange={setSelectedDocumentIds}
                          onOpenDetails={(document) =>
                            updateSearch({ document: document.document_id })
                          }
                          onUploadVersion={setVersionDocument}
                          onMarkReady={(document, contentHash) =>
                            model.markReady.mutate({ document, contentHash })
                          }
                          onPublish={(document) => model.publish.mutate(document)}
                          onMove={(document) =>
                            setOrganizationDialog({ mode: "move", documents: [document] })
                          }
                          onSetTags={(document) =>
                            setOrganizationDialog({ mode: "tags", documents: [document] })
                          }
                          onFavorite={(document) =>
                            actions.favorite.mutate({
                              documentId: document.document_id,
                              favorite: !document.is_favorite,
                            })
                          }
                          onDelete={(document) =>
                            actions.deleteDocuments.mutate({
                              knowledgeBaseId: selectedBaseId,
                              documentIds: [document.document_id],
                            })
                          }
                        />
                      ) : (
                        <DocumentTable
                          items={filteredDocuments}
                          jobs={model.jobs.data ?? []}
                          folders={activeFolders}
                          tags={activeTags}
                          selectedDocumentIds={selectedDocumentIds}
                          isLoading={model.documents.isLoading}
                          emptyTitle={documentEmptyState.title}
                          emptyDescription={documentEmptyState.description}
                          canReadDetails={has("knowledge.document.read")}
                          canUploadVersion={has("knowledge.document.version.create")}
                          canMarkReady={has("knowledge.document.version.ready")}
                          canPublish={has("knowledge.document.version.publish")}
                          canOrganize={
                            has("knowledge.document.folder.bind") ||
                            has("knowledge.document.tag.bind")
                          }
                          canFavorite={has("knowledge.document.favorite")}
                          canDelete={has("knowledge.document.delete")}
                          isMutating={isDocumentMutating}
                          onSelectionChange={setSelectedDocumentIds}
                          onOpenDetails={(document) =>
                            updateSearch({ document: document.document_id })
                          }
                          onUploadVersion={setVersionDocument}
                          onMarkReady={(document, contentHash) =>
                            model.markReady.mutate({ document, contentHash })
                          }
                          onPublish={(document) => model.publish.mutate(document)}
                          onMove={(document) =>
                            setOrganizationDialog({ mode: "move", documents: [document] })
                          }
                          onSetTags={(document) =>
                            setOrganizationDialog({ mode: "tags", documents: [document] })
                          }
                          onFavorite={(document) =>
                            actions.favorite.mutate({
                              documentId: document.document_id,
                              favorite: !document.is_favorite,
                            })
                          }
                          onDelete={(document) =>
                            actions.deleteDocuments.mutate({
                              knowledgeBaseId: selectedBaseId,
                              documentIds: [document.document_id],
                            })
                          }
                        />
                      )}
                    </>
                  ),
                },
                {
                  key: "jobs",
                  label: `入库任务 ${model.jobs.data?.length ?? 0}`,
                  children: (
                    <IngestionTable
                      items={model.jobs.data ?? []}
                      isLoading={model.jobs.isLoading}
                      canRetry={has("knowledge.ingestion.retry")}
                      isRetrying={model.retryJob.isPending}
                      onRetry={(jobId) => model.retryJob.mutate(jobId)}
                    />
                  ),
                },
              ]}
            />
          )}
        </section>
      </div>
      <KnowledgeDialogs
        createOpen={createOpen}
        uploadOpen={uploadOpen}
        versionDocument={versionDocument}
        isCreating={model.createBase.isPending}
        isUploading={model.uploadDocument.isPending || model.uploadVersion.isPending}
        onCloseCreate={() => setCreateOpen(false)}
        onCloseUpload={() => setUploadOpen(false)}
        onCloseVersion={() => setVersionDocument(null)}
        onCreate={model.createBase.mutateAsync}
        onUpload={model.uploadDocument.mutateAsync}
        onUploadVersion={(documentId, file) =>
          model.uploadVersion.mutateAsync({ documentId, file })
        }
      />
      <FolderDialog
        action={folderDialog?.action ?? null}
        folder={folderDialog?.folder ?? null}
        folders={activeFolders}
        isPending={actions.folderCommand.isPending}
        onClose={() => setFolderDialog(null)}
        onCreate={(body) => actions.folderCommand.mutateAsync({ type: "create", body })}
        onRename={(folderId, name) =>
          actions.folderCommand.mutateAsync({ type: "rename", folderId, name })
        }
        onMove={(folderId, parentFolderId) =>
          actions.folderCommand.mutateAsync({ type: "move", folderId, parentFolderId })
        }
      />
      <DocumentOrganizationDialog
        mode={organizationDialog?.mode ?? null}
        documents={organizationDialog?.documents ?? []}
        folders={activeFolders}
        tags={activeTags}
        isPending={isOrganizationPending}
        onClose={() => setOrganizationDialog(null)}
        onMove={(folderId) =>
          actions.moveDocuments.mutateAsync({
            documentIds:
              organizationDialog?.documents.map((document) => document.document_id) ?? [],
            folderId,
          })
        }
        onSetTags={(tagIds, preserveExisting) =>
          actions.replaceDocumentTags.mutateAsync({
            documents: organizationDialog?.documents ?? [],
            tagIds,
            preserveExisting,
          })
        }
      />
      <TagManagerDrawer
        open={tagManagerOpen}
        items={organization.tags.data ?? []}
        isLoading={organization.tags.isLoading}
        canCreate={has("knowledge.tag.create")}
        canUpdate={has("knowledge.tag.update")}
        canDelete={has("knowledge.tag.delete")}
        canRestore={has("knowledge.tag.restore")}
        isPending={actions.tagCommand.isPending}
        onClose={() => setTagManagerOpen(false)}
        onCreate={(body) => actions.tagCommand.mutateAsync({ type: "create", body })}
        onUpdate={(tagId, body) => actions.tagCommand.mutateAsync({ type: "update", tagId, body })}
        onDelete={(tagId) => actions.tagCommand.mutateAsync({ type: "delete", tagId })}
        onRestore={(tagId) => actions.tagCommand.mutateAsync({ type: "restore", tagId })}
      />
      <DocumentDetailDrawer
        open={Boolean(detailDocumentId)}
        detail={model.detail.data}
        isLoading={model.detail.isLoading}
        errorDescription={model.detail.isError ? errorMessage(model.detail.error) : null}
        canDownload={has("knowledge.document.download")}
        isDownloading={model.downloadVersion.isPending}
        onClose={() => updateSearch({ document: null })}
        onRetry={() => void model.detail.refetch()}
        onDownload={(documentId, documentVersionId) =>
          model.downloadVersion.mutate({ documentId, documentVersionId })
        }
      />
    </>
  );
}
