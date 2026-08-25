/** @description 知识文档卡片视图，保留与列表一致的状态和操作能力。 */
import { Button, Checkbox, Dropdown, Popconfirm, Skeleton, Tag } from "antd";
import type { MenuProps } from "antd";
import {
  CheckCircle2,
  Eye,
  FileText,
  FolderInput,
  MoreHorizontal,
  Send,
  Star,
  Tags,
  Trash2,
  Upload,
} from "lucide-react";

import type { IngestionJob, KnowledgeDocumentSummary } from "@/api/services/knowledge";
import type { KnowledgeFolder, KnowledgeTag } from "@/api/services/knowledgeOrganization";
import { StateView } from "@/components/StateView/StateView";

import { documentStatus, formatTimestamp } from "../config";

interface DocumentCardGridProps {
  /** 服务端已授权的文档摘要。 */
  items: readonly KnowledgeDocumentSummary[];
  /** 用于判断最新版本是否可确认就绪的任务快照。 */
  jobs: readonly IngestionJob[];
  /** 当前空间活动目录。 */
  folders: readonly KnowledgeFolder[];
  /** 当前空间活动标签。 */
  tags: readonly KnowledgeTag[];
  /** 当前批量选中的文档 ID。 */
  selectedDocumentIds: readonly string[];
  /** 首次加载状态。 */
  isLoading: boolean;
  /** 空状态标题。 */
  emptyTitle: string;
  /** 空状态说明。 */
  emptyDescription: string;
  /** 页面动作权限仅裁剪入口，服务端仍逐项重新授权。 */
  permissions: {
    readDetails: boolean;
    uploadVersion: boolean;
    markReady: boolean;
    publish: boolean;
    organize: boolean;
    favorite: boolean;
    delete: boolean;
  };
  /** 任一文档命令是否正在提交。 */
  isMutating: boolean;
  /** 更新批量选择。 */
  onSelectionChange: (documentIds: readonly string[]) => void;
  /** 打开详情。 */
  onOpenDetails: (document: KnowledgeDocumentSummary) => void;
  /** 上传新版本。 */
  onUploadVersion: (document: KnowledgeDocumentSummary) => void;
  /** 确认最新版本就绪。 */
  onMarkReady: (document: KnowledgeDocumentSummary, contentHash: string) => void;
  /** 发布最新版本。 */
  onPublish: (document: KnowledgeDocumentSummary) => void;
  /** 移动文档。 */
  onMove: (document: KnowledgeDocumentSummary) => void;
  /** 设置文档标签。 */
  onSetTags: (document: KnowledgeDocumentSummary) => void;
  /** 切换收藏。 */
  onFavorite: (document: KnowledgeDocumentSummary) => void;
  /** 移入回收站。 */
  onDelete: (document: KnowledgeDocumentSummary) => void;
}

/** 以稳定密度展示文件卡片；每张卡片保留列表视图的全部状态命令。 */
export function DocumentCardGrid(props: DocumentCardGridProps) {
  if (props.isLoading) {
    return (
      <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-3">
        {[0, 1, 2].map((key) => (
          <Skeleton.Node key={key} active className="h-[220px]! w-full!" />
        ))}
      </div>
    );
  }
  if (props.items.length === 0) {
    return <StateView kind="empty" title={props.emptyTitle} description={props.emptyDescription} />;
  }
  const jobByVersion = new Map(props.jobs.map((job) => [job.document_version_id, job]));
  const folderById = new Map(props.folders.map((folder) => [folder.folder_id, folder]));
  const tagById = new Map(props.tags.map((tag) => [tag.tag_id, tag]));

  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-3">
      {props.items.map((document) => {
        const status = documentStatus[document.latest_version.status];
        const latestJob = jobByVersion.get(document.latest_version.document_version_id);
        const readyHash = latestJob?.status === "succeeded" ? latestJob.parsed_content_hash : null;
        const selected = props.selectedDocumentIds.includes(document.document_id);
        const documentTags = document.tag_ids
          .map((tagId) => tagById.get(tagId))
          .filter((tag): tag is KnowledgeTag => Boolean(tag));
        return (
          <article
            key={document.document_id}
            className="grid min-h-[220px] grid-rows-[auto_1fr_auto] rounded-md border border-solid border-border bg-surface p-4 transition-shadow hover:shadow-sm"
          >
            <div className="flex items-start justify-between gap-2">
              <Checkbox
                aria-label={`选择文档 ${document.title}`}
                checked={selected}
                onChange={(event) =>
                  props.onSelectionChange(
                    event.target.checked
                      ? [...props.selectedDocumentIds, document.document_id]
                      : props.selectedDocumentIds.filter((id) => id !== document.document_id),
                  )
                }
              />
              <Tag color={status.color}>{status.label}</Tag>
            </div>
            <div className="min-w-0 py-4">
              <FileText size={22} className="mb-3 text-text-muted" />
              <button
                type="button"
                className="block w-full cursor-pointer overflow-hidden border-0 bg-transparent p-0 text-left text-[15px] font-semibold text-text"
                disabled={!props.permissions.readDetails}
                onClick={() => props.onOpenDetails(document)}
              >
                <span className="block overflow-hidden text-ellipsis whitespace-nowrap">
                  {document.title}
                </span>
              </button>
              <span className="mt-1 block overflow-hidden text-ellipsis whitespace-nowrap text-xs text-text-muted">
                {document.source_name}
              </span>
              <div className="mt-3 flex min-h-6 flex-wrap gap-1">
                {documentTags.slice(0, 2).map((tag) => (
                  <Tag key={tag.tag_id} color={tag.color ?? undefined} className="m-0">
                    {tag.name}
                  </Tag>
                ))}
              </div>
            </div>
            <div className="flex items-center justify-between border-t border-t-solid border-border pt-3 text-xs text-text-muted">
              <span>{folderById.get(document.folder_id)?.name ?? "全部文件"}</span>
              <span>{formatTimestamp(document.updated_at)}</span>
              <DocumentCardActions
                document={document}
                readyHash={readyHash}
                permissions={props.permissions}
                isMutating={props.isMutating}
                onOpenDetails={props.onOpenDetails}
                onUploadVersion={props.onUploadVersion}
                onMarkReady={props.onMarkReady}
                onPublish={props.onPublish}
                onMove={props.onMove}
                onSetTags={props.onSetTags}
                onFavorite={props.onFavorite}
                onDelete={props.onDelete}
              />
            </div>
          </article>
        );
      })}
    </div>
  );
}

interface DocumentCardActionsProps {
  /** 当前卡片文档。 */
  document: KnowledgeDocumentSummary;
  /** 解析成功后可用于确认就绪的内容摘要。 */
  readyHash: string | null;
  /** 当前页面动作权限快照。 */
  permissions: DocumentCardGridProps["permissions"];
  /** 任一文档命令是否正在提交。 */
  isMutating: boolean;
  /** 打开详情。 */
  onOpenDetails: DocumentCardGridProps["onOpenDetails"];
  /** 上传新版本。 */
  onUploadVersion: DocumentCardGridProps["onUploadVersion"];
  /** 确认版本就绪。 */
  onMarkReady: DocumentCardGridProps["onMarkReady"];
  /** 发布版本。 */
  onPublish: DocumentCardGridProps["onPublish"];
  /** 移动文档。 */
  onMove: DocumentCardGridProps["onMove"];
  /** 设置标签。 */
  onSetTags: DocumentCardGridProps["onSetTags"];
  /** 切换收藏。 */
  onFavorite: DocumentCardGridProps["onFavorite"];
  /** 移入回收站。 */
  onDelete: DocumentCardGridProps["onDelete"];
}

/** 生成与表格视图一致的版本和组织命令，避免卡片模式成为只读降级入口。 */
function DocumentCardActions(props: DocumentCardActionsProps) {
  const items: MenuProps["items"] = [
    ...(props.permissions.readDetails
      ? [
          {
            key: "details",
            label: "查看详情",
            icon: <Eye size={15} />,
            onClick: () => props.onOpenDetails(props.document),
          },
        ]
      : []),
    ...(props.permissions.uploadVersion
      ? [
          {
            key: "upload",
            label: "上传新版本",
            icon: <Upload size={15} />,
            onClick: () => props.onUploadVersion(props.document),
          },
        ]
      : []),
    ...(props.permissions.organize
      ? [
          {
            key: "move",
            label: "移动到",
            icon: <FolderInput size={15} />,
            onClick: () => props.onMove(props.document),
          },
          {
            key: "tags",
            label: "设置标签",
            icon: <Tags size={15} />,
            onClick: () => props.onSetTags(props.document),
          },
        ]
      : []),
    ...(props.permissions.favorite
      ? [
          {
            key: "favorite",
            label: props.document.is_favorite ? "取消收藏" : "收藏",
            icon: <Star size={15} />,
            onClick: () => props.onFavorite(props.document),
          },
        ]
      : []),
  ];
  return (
    <div className="flex items-center gap-1">
      {props.document.latest_version.status === "draft" &&
        props.readyHash &&
        props.permissions.markReady && (
          <Button
            type="text"
            aria-label="确认就绪"
            icon={<CheckCircle2 size={16} />}
            loading={props.isMutating}
            onClick={() => props.onMarkReady(props.document, props.readyHash!)}
          />
        )}
      {props.document.latest_version.status === "ready" && props.permissions.publish && (
        <Popconfirm
          title="发布此文档版本？"
          okText="发布"
          cancelText="取消"
          onConfirm={() => props.onPublish(props.document)}
        >
          <Button
            type="text"
            aria-label="发布"
            icon={<Send size={16} />}
            loading={props.isMutating}
          />
        </Popconfirm>
      )}
      {items.length > 0 && (
        <Dropdown menu={{ items }} trigger={["click"]}>
          <Button
            type="text"
            aria-label={`打开 ${props.document.title} 操作菜单`}
            icon={<MoreHorizontal size={17} />}
          />
        </Dropdown>
      )}
      {props.permissions.delete && (
        <Popconfirm
          title="将此文档移入回收站？"
          okText="删除"
          cancelText="取消"
          onConfirm={() => props.onDelete(props.document)}
        >
          <Button
            type="text"
            danger
            aria-label={`删除文档 ${props.document.title}`}
            icon={<Trash2 size={16} />}
            loading={props.isMutating}
          />
        </Popconfirm>
      )}
    </div>
  );
}
