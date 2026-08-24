/** @description 知识文档版本状态、可见性与发布动作表格。 */
import { Button, Dropdown, Popconfirm, Table, Tag } from "antd";
import type { MenuProps, TableColumnsType } from "antd";
import {
  CheckCircle2,
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

import { documentStatus, formatTimestamp, securityLevelLabels, visibilityLabels } from "../config";

interface DocumentTableProps {
  /** 服务端已按资源和字段策略投影的文档摘要。 */
  items: readonly KnowledgeDocumentSummary[];
  /** 用于关联最新版本解析结果和内容摘要的入库任务。 */
  jobs: readonly IngestionJob[];
  /** 当前工作空间活动目录，用于还原文档归属。 */
  folders: readonly KnowledgeFolder[];
  /** 当前工作空间活动标签，用于还原标签名称和颜色。 */
  tags: readonly KnowledgeTag[];
  /** 当前批量选中的文档 ID。 */
  selectedDocumentIds: readonly string[];
  /** 文档清单是否正在首次加载。 */
  isLoading: boolean;
  /** 当前视图无数据时的上下文化标题。 */
  emptyTitle?: string;
  /** 当前视图无数据时的上下文化说明。 */
  emptyDescription?: string;
  /** 只控制上传新版本入口，服务端仍独立授权。 */
  canUploadVersion: boolean;
  /** 只控制确认就绪入口，状态和摘要仍由服务端校验。 */
  canMarkReady: boolean;
  /** 只控制发布入口，服务端仍校验版本和资源范围。 */
  canPublish: boolean;
  /** 只控制目录和标签入口，服务端仍校验绑定资源。 */
  canOrganize: boolean;
  /** 只控制个人收藏入口，服务端仍校验文档读取权限。 */
  canFavorite: boolean;
  /** 只控制移入回收站入口，服务端仍校验文档状态和权限。 */
  canDelete: boolean;
  /** 任一文档写操作是否正在提交。 */
  isMutating: boolean;
  /** 打开指定文档的新版本上传流程。 */
  onUploadVersion: (document: KnowledgeDocumentSummary) => void;
  /** 以解析任务提供的内容摘要请求确认版本就绪。 */
  onMarkReady: (document: KnowledgeDocumentSummary, contentHash: string) => void;
  /** 请求发布指定文档的最新版本。 */
  onPublish: (document: KnowledgeDocumentSummary) => void;
  /** 更新批量选择。 */
  onSelectionChange: (documentIds: readonly string[]) => void;
  /** 打开指定文档移动流程。 */
  onMove: (document: KnowledgeDocumentSummary) => void;
  /** 打开指定文档标签流程。 */
  onSetTags: (document: KnowledgeDocumentSummary) => void;
  /** 切换指定文档收藏状态。 */
  onFavorite: (document: KnowledgeDocumentSummary) => void;
  /** 将指定文档移入回收站。 */
  onDelete: (document: KnowledgeDocumentSummary) => void;
}

/**
 * 展示文档最新版本及允许的发布动作。
 *
 * 操作按钮只依据页面权限和服务端状态快照裁剪，后端仍需校验版本状态、内容摘要和资源范围。
 */
export function DocumentTable({
  items,
  jobs,
  folders,
  tags,
  selectedDocumentIds,
  isLoading,
  emptyTitle = "还没有文档",
  emptyDescription = "上传首份文档后，可以在这里跟踪解析和发布状态。",
  canUploadVersion,
  canMarkReady,
  canPublish,
  canOrganize,
  canFavorite,
  canDelete,
  isMutating,
  onUploadVersion,
  onMarkReady,
  onPublish,
  onSelectionChange,
  onMove,
  onSetTags,
  onFavorite,
  onDelete,
}: DocumentTableProps) {
  const latestJobByVersion = new Map(jobs.map((job) => [job.document_version_id, job]));
  const folderById = new Map(folders.map((folder) => [folder.folder_id, folder]));
  const tagById = new Map(tags.map((tag) => [tag.tag_id, tag]));
  const columns: TableColumnsType<KnowledgeDocumentSummary> = [
    {
      title: "文档",
      key: "document",
      render: (_, record) => (
        <div className="grid min-w-0 gap-[3px]">
          <strong className="overflow-hidden text-ellipsis whitespace-nowrap">
            {record.title}
          </strong>
          <span className="overflow-hidden text-ellipsis whitespace-nowrap text-xs text-text-muted">
            {record.source_name}
          </span>
        </div>
      ),
    },
    {
      title: "版本",
      key: "version",
      width: 150,
      render: (_, record) => {
        const status = documentStatus[record.latest_version.status];
        return (
          <div className="flex items-center gap-2">
            <Tag color={status.color}>{status.label}</Tag>
            <span className="text-xs text-text-muted">V{record.latest_version.version_number}</span>
          </div>
        );
      },
    },
    {
      title: "文件夹",
      key: "folder",
      width: 130,
      render: (_, record) => folderById.get(record.folder_id)?.name ?? "全部文件",
    },
    {
      title: "标签",
      key: "tags",
      width: 190,
      render: (_, record) => {
        const documentTags = record.tag_ids
          .map((tagId) => tagById.get(tagId))
          .filter((tag): tag is KnowledgeTag => Boolean(tag));
        return documentTags.length > 0 ? (
          <div className="flex max-w-[180px] flex-wrap gap-1">
            {documentTags.slice(0, 2).map((tag) => (
              <Tag key={tag.tag_id} color={tag.color ?? undefined} className="m-0 max-w-[120px]">
                <span className="block overflow-hidden text-ellipsis whitespace-nowrap">
                  {tag.name}
                </span>
              </Tag>
            ))}
            {documentTags.length > 2 && <Tag className="m-0">+{documentTags.length - 2}</Tag>}
          </div>
        ) : (
          <span className="text-text-muted">-</span>
        );
      },
    },
    {
      title: "范围",
      key: "scope",
      width: 150,
      render: (_, record) => (
        <div className="grid min-w-0 gap-[3px]">
          <span>{visibilityLabels[record.visibility]}</span>
          <small className="text-xs text-text-muted">
            {securityLevelLabels[record.security_level]}
          </small>
        </div>
      ),
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      key: "updated_at",
      width: 140,
      render: formatTimestamp,
    },
    {
      title: "操作",
      key: "actions",
      width: 156,
      render: (_, record) => {
        const job = latestJobByVersion.get(record.latest_version.document_version_id);
        const readyHash = job?.status === "succeeded" ? job.parsed_content_hash : null;
        const actions: MenuProps["items"] = [
          ...(canUploadVersion
            ? [
                {
                  key: "upload-version",
                  label: "上传新版本",
                  icon: <Upload size={15} />,
                  onClick: () => onUploadVersion(record),
                },
              ]
            : []),
          ...(canOrganize
            ? [
                {
                  key: "move",
                  label: "移动到",
                  icon: <FolderInput size={15} />,
                  onClick: () => onMove(record),
                },
                {
                  key: "tags",
                  label: "设置标签",
                  icon: <Tags size={15} />,
                  onClick: () => onSetTags(record),
                },
              ]
            : []),
          ...(canFavorite
            ? [
                {
                  key: "favorite",
                  label: record.is_favorite ? "取消收藏" : "收藏",
                  icon: <Star size={15} fill={record.is_favorite ? "currentColor" : "none"} />,
                  onClick: () => onFavorite(record),
                },
              ]
            : []),
        ];
        return (
          <div className="flex items-center gap-2">
            {record.latest_version.status === "draft" && readyHash && canMarkReady && (
              <Button
                type="text"
                icon={<CheckCircle2 size={16} />}
                loading={isMutating}
                onClick={() => onMarkReady(record, readyHash)}
              >
                确认就绪
              </Button>
            )}
            {record.latest_version.status === "ready" && canPublish && (
              <Popconfirm
                title="发布此文档版本？"
                description="发布后将切换当前可检索版本。"
                okText="发布"
                cancelText="取消"
                onConfirm={() => onPublish(record)}
              >
                <Button type="text" icon={<Send size={16} />} loading={isMutating}>
                  发布
                </Button>
              </Popconfirm>
            )}
            {actions.length > 0 && (
              <Dropdown menu={{ items: actions }} trigger={["click"]}>
                <Button
                  aria-label={`打开 ${record.title} 操作菜单`}
                  type="text"
                  icon={<MoreHorizontal size={17} />}
                />
              </Dropdown>
            )}
            {canDelete && (
              <Popconfirm
                title="将此文档移入回收站？"
                okText="删除"
                cancelText="取消"
                onConfirm={() => onDelete(record)}
              >
                <Button
                  type="text"
                  danger
                  aria-label={`删除文档 ${record.title}`}
                  icon={<Trash2 size={16} />}
                  loading={isMutating}
                />
              </Popconfirm>
            )}
          </div>
        );
      },
    },
  ];

  return (
    <Table<KnowledgeDocumentSummary>
      rowKey="document_id"
      columns={columns}
      dataSource={[...items]}
      loading={isLoading}
      rowSelection={
        canOrganize || canDelete
          ? {
              selectedRowKeys: [...selectedDocumentIds],
              onChange: (keys) => onSelectionChange(keys.map(String)),
            }
          : undefined
      }
      pagination={false}
      scroll={{ x: 1120 }}
      locale={{
        emptyText: <StateView kind="empty" title={emptyTitle} description={emptyDescription} />,
      }}
    />
  );
}
