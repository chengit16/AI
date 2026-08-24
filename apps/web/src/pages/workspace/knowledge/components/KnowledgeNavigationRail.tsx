/** @description 知识库、目录、收藏和回收站统一导航栏。 */
import { Button, Dropdown, Select, Skeleton, Tree } from "antd";
import type { DataNode } from "antd/es/tree";
import {
  ArchiveRestore,
  FileStack,
  Folder,
  FolderOpen,
  MoreHorizontal,
  Plus,
  Star,
  Tags,
} from "lucide-react";
import type { ReactNode } from "react";

import type { KnowledgeBaseSummary, KnowledgeDocumentSummary } from "@/api/services/knowledge";
import type { KnowledgeFolder } from "@/api/services/knowledgeOrganization";
import { cn } from "@/utils/cn";

/** 当前文档区使用的组织范围。 */
export type KnowledgeViewScope = "documents" | "favorites" | "trash";

/** 目录管理动作，由页面弹窗收集最终字段后再提交。 */
export type FolderEditAction = "create" | "rename" | "move";

interface KnowledgeNavigationRailProps {
  /** 当前空间可见的知识库摘要。 */
  bases: readonly KnowledgeBaseSummary[];
  /** 当前选中的知识库 ID。 */
  selectedBaseId: string | null;
  /** 当前工作空间活动目录。 */
  folders: readonly KnowledgeFolder[];
  /** 当前知识库已授权文档，用于显示目录计数。 */
  documents: readonly KnowledgeDocumentSummary[];
  /** 当前选中的普通目录；为空表示全部文件。 */
  selectedFolderId: string | null;
  /** 当前文档、收藏或回收站视图。 */
  scope: KnowledgeViewScope;
  /** 知识库或目录是否正在首次加载。 */
  isLoading: boolean;
  /** 当前成员收藏数量。 */
  favoriteCount: number;
  /** 当前工作空间回收站文档与文件夹总数。 */
  trashCount: number;
  /** 只控制新建目录入口，服务端仍独立授权。 */
  canCreateFolder: boolean;
  /** 只控制普通目录编辑入口，服务端仍独立授权。 */
  canUpdateFolder: boolean;
  /** 只控制目录删除入口，服务端仍独立授权。 */
  canDeleteFolder: boolean;
  /** 是否展示标签管理入口。 */
  canReadTags: boolean;
  /** 切换当前知识库。 */
  onSelectBase: (knowledgeBaseId: string) => void;
  /** 切换全部文件或具体目录。 */
  onSelectFolder: (folderId: string | null) => void;
  /** 切换收藏或回收站范围。 */
  onSelectScope: (scope: KnowledgeViewScope) => void;
  /** 打开目录创建、重命名或移动弹窗。 */
  onEditFolder: (action: FolderEditAction, folder: KnowledgeFolder | null) => void;
  /** 请求软删除空目录。 */
  onDeleteFolder: (folder: KnowledgeFolder) => void;
  /** 打开标签管理界面。 */
  onManageTags: () => void;
}

function scopeButton(
  label: string,
  count: number,
  icon: ReactNode,
  isActive: boolean,
  onClick: () => void,
) {
  return (
    <button
      type="button"
      className={cn(
        "flex min-h-11 w-full cursor-pointer items-center gap-3 rounded-ui border border-solid px-3 text-left transition-colors",
        isActive
          ? "border-brand-border bg-brand-soft text-brand"
          : "border-transparent bg-transparent text-text hover:border-border-soft hover:bg-surface",
      )}
      onClick={onClick}
    >
      {icon}
      <span className="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap">
        {label}
      </span>
      <span className="text-xs text-text-muted">{count}</span>
    </button>
  );
}

/** 构建稳定目录树；孤儿目录回落到根层展示，避免关系异常导致入口完全消失。 */
function folderTreeData(
  folders: readonly KnowledgeFolder[],
  counts: ReadonlyMap<string, number>,
  actionTitle: (folder: KnowledgeFolder) => ReactNode,
): DataNode[] {
  const childrenByParent = new Map<string | null, KnowledgeFolder[]>();
  const knownIds = new Set(folders.map((folder) => folder.folder_id));
  folders.forEach((folder) => {
    const parentId =
      folder.parent_folder_id && knownIds.has(folder.parent_folder_id)
        ? folder.parent_folder_id
        : null;
    const children = childrenByParent.get(parentId) ?? [];
    children.push(folder);
    childrenByParent.set(parentId, children);
  });
  const build = (parentId: string | null): DataNode[] =>
    (childrenByParent.get(parentId) ?? []).map((folder) => ({
      key: folder.folder_id,
      icon: folder.is_default ? <FolderOpen size={16} /> : <Folder size={16} />,
      title: actionTitle(folder),
      children: build(folder.folder_id),
    }));
  return build(null);
}

/**
 * 组合知识库与组织导航。
 *
 * 权限属性只控制交互入口；直接 URL、跨空间资源和每个写操作仍由服务端策略拒绝。
 */
export function KnowledgeNavigationRail(props: KnowledgeNavigationRailProps) {
  const counts = new Map<string, number>();
  props.documents.forEach((document) => {
    counts.set(document.folder_id, (counts.get(document.folder_id) ?? 0) + 1);
  });
  const folderTitle = (folder: KnowledgeFolder) => (
    <span className="flex min-w-0 items-center gap-2">
      <span className="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap">
        {folder.name}
      </span>
      <span className="text-xs text-text-muted">
        {folder.is_default ? props.documents.length : (counts.get(folder.folder_id) ?? 0)}
      </span>
      {!folder.is_default &&
        (props.canUpdateFolder || props.canDeleteFolder || props.canCreateFolder) && (
          <Dropdown
            trigger={["click"]}
            menu={{
              items: [
                ...(props.canCreateFolder ? [{ key: "create", label: "新建子文件夹" }] : []),
                ...(props.canUpdateFolder
                  ? [
                      { key: "rename", label: "重命名" },
                      { key: "move", label: "移动" },
                    ]
                  : []),
                ...(props.canDeleteFolder
                  ? [{ key: "delete", label: "移入回收站", danger: true }]
                  : []),
              ],
              onClick: ({ key, domEvent }) => {
                domEvent.stopPropagation();
                if (key === "delete") props.onDeleteFolder(folder);
                else props.onEditFolder(key as FolderEditAction, folder);
              },
            }}
          >
            <Button
              type="text"
              size="small"
              aria-label={`管理文件夹 ${folder.name}`}
              icon={<MoreHorizontal size={15} />}
              onClick={(event) => event.stopPropagation()}
            />
          </Dropdown>
        )}
    </span>
  );
  const treeData = folderTreeData(props.folders, counts, folderTitle);

  return (
    <aside
      className="min-w-0 border-r border-r-solid border-border bg-surface-subtle p-4 tablet-down:border-b tablet-down:border-b-solid tablet-down:border-r-0"
      aria-label="知识组织导航"
    >
      <label className="mb-2 block text-xs font-semibold text-text-muted" htmlFor="knowledge-base">
        当前知识库
      </label>
      <Select
        id="knowledge-base"
        className="mb-4 w-full"
        value={props.selectedBaseId ?? undefined}
        loading={props.isLoading}
        placeholder="选择知识库"
        options={props.bases.map((base) => ({
          value: base.knowledge_base_id,
          label: base.name,
        }))}
        onChange={props.onSelectBase}
      />
      <div className="grid gap-1">
        {scopeButton(
          "全部文件",
          props.documents.length,
          <FileStack size={17} />,
          props.scope === "documents" && props.selectedFolderId === null,
          () => props.onSelectFolder(null),
        )}
        {scopeButton(
          "我的收藏",
          props.favoriteCount,
          <Star size={17} />,
          props.scope === "favorites",
          () => props.onSelectScope("favorites"),
        )}
        {scopeButton(
          "回收站",
          props.trashCount,
          <ArchiveRestore size={17} />,
          props.scope === "trash",
          () => props.onSelectScope("trash"),
        )}
      </div>
      <div className="mt-5 flex min-h-11 items-center justify-between gap-2">
        <strong className="text-sm">文件夹</strong>
        {props.canCreateFolder && (
          <Button
            type="text"
            aria-label="新建文件夹"
            icon={<Plus size={16} />}
            onClick={() => props.onEditFolder("create", null)}
          />
        )}
      </div>
      {props.isLoading ? (
        <Skeleton active title={false} paragraph={{ rows: 5 }} />
      ) : (
        <Tree
          blockNode
          showIcon
          defaultExpandAll
          selectedKeys={props.selectedFolderId ? [props.selectedFolderId] : []}
          treeData={treeData}
          onSelect={(keys) => props.onSelectFolder(String(keys[0] ?? "") || null)}
        />
      )}
      {props.canReadTags && (
        <Button
          className="mt-4 w-full justify-start"
          type="text"
          icon={<Tags size={16} />}
          onClick={props.onManageTags}
        >
          管理标签
        </Button>
      )}
    </aside>
  );
}
