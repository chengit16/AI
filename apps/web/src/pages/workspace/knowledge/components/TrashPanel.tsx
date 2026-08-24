/** @description 知识回收站文档与目录恢复、永久删除界面。 */
import { Button, Popconfirm, Table, Tabs, Tag } from "antd";
import type { TableColumnsType } from "antd";
import { RotateCcw, Trash2 } from "lucide-react";

import type { KnowledgeFolder, KnowledgeTrashDocument } from "@/api/services/knowledgeOrganization";
import { StateView } from "@/components/StateView/StateView";

import { formatTimestamp, securityLevelLabels } from "../config";

interface TrashPanelProps {
  /** 回收站文档事实。 */
  documents: readonly KnowledgeTrashDocument[];
  /** 已软删除目录事实。 */
  folders: readonly KnowledgeFolder[];
  /** 回收站查询是否正在首次加载。 */
  isLoading: boolean;
  /** 回收站查询失败时的可展示说明。 */
  errorDescription: string | null;
  /** 只控制文档恢复入口，服务端仍独立授权。 */
  canRestoreDocument: boolean;
  /** 只控制文档永久删除入口，服务端仍独立授权。 */
  canPurgeDocument: boolean;
  /** 只控制目录恢复入口，服务端仍独立授权。 */
  canRestoreFolder: boolean;
  /** 只控制目录永久删除入口，服务端仍独立授权。 */
  canPurgeFolder: boolean;
  /** 任一回收站命令是否正在提交。 */
  isPending: boolean;
  /** 重新加载回收站。 */
  onRetry: () => void;
  /** 恢复文档。 */
  onRestoreDocument: (documentId: string) => void;
  /** 永久删除文档。 */
  onPurgeDocument: (documentId: string) => void;
  /** 恢复目录。 */
  onRestoreFolder: (folderId: string) => void;
  /** 永久删除目录。 */
  onPurgeFolder: (folderId: string) => void;
}

/** 展示可恢复事实；永久删除均要求独立二次确认。 */
export function TrashPanel(props: TrashPanelProps) {
  if (props.errorDescription) {
    return (
      <StateView
        kind="error"
        title="回收站未能加载"
        description={props.errorDescription}
        action={<Button onClick={props.onRetry}>重新加载</Button>}
      />
    );
  }

  const documentColumns: TableColumnsType<KnowledgeTrashDocument> = [
    {
      title: "文档",
      key: "document",
      render: (_, document) => (
        <div className="grid min-w-0 gap-1">
          <strong className="overflow-hidden text-ellipsis whitespace-nowrap">
            {document.title}
          </strong>
          <span className="text-xs text-text-muted">
            {securityLevelLabels[document.security_level]}
          </span>
        </div>
      ),
    },
    {
      title: "删除时间",
      dataIndex: "deleted_at",
      width: 150,
      render: formatTimestamp,
    },
    {
      title: "状态",
      width: 100,
      render: () => <Tag>可恢复</Tag>,
    },
    {
      title: "操作",
      key: "actions",
      width: 190,
      render: (_, document) => (
        <div className="flex items-center gap-1">
          {props.canRestoreDocument && (
            <Button
              type="text"
              icon={<RotateCcw size={15} />}
              loading={props.isPending}
              onClick={() => props.onRestoreDocument(document.document_id)}
            >
              恢复
            </Button>
          )}
          {props.canPurgeDocument && (
            <Popconfirm
              title="永久删除此文档？"
              description="原文件、版本和索引清理后无法恢复。"
              okText="永久删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={() => props.onPurgeDocument(document.document_id)}
            >
              <Button type="text" danger icon={<Trash2 size={15} />} loading={props.isPending}>
                永久删除
              </Button>
            </Popconfirm>
          )}
        </div>
      ),
    },
  ];
  const folderColumns: TableColumnsType<KnowledgeFolder> = [
    { title: "文件夹", dataIndex: "name", key: "name" },
    {
      title: "删除时间",
      dataIndex: "deleted_at",
      width: 150,
      render: formatTimestamp,
    },
    {
      title: "操作",
      key: "actions",
      width: 190,
      render: (_, folder) => (
        <div className="flex items-center gap-1">
          {props.canRestoreFolder && (
            <Button
              type="text"
              icon={<RotateCcw size={15} />}
              loading={props.isPending}
              onClick={() => props.onRestoreFolder(folder.folder_id)}
            >
              恢复
            </Button>
          )}
          {props.canPurgeFolder && (
            <Popconfirm
              title="永久删除此文件夹？"
              description="此操作无法恢复。"
              okText="永久删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={() => props.onPurgeFolder(folder.folder_id)}
            >
              <Button type="text" danger icon={<Trash2 size={15} />} loading={props.isPending}>
                永久删除
              </Button>
            </Popconfirm>
          )}
        </div>
      ),
    },
  ];

  return (
    <Tabs
      items={[
        {
          key: "documents",
          label: `文档 ${props.documents.length}`,
          children: (
            <Table<KnowledgeTrashDocument>
              rowKey="document_id"
              columns={documentColumns}
              dataSource={[...props.documents]}
              loading={props.isLoading}
              pagination={false}
              scroll={{ x: 700 }}
              locale={{
                emptyText: (
                  <StateView
                    kind="empty"
                    title="回收站为空"
                    description="删除的文档会出现在这里。"
                  />
                ),
              }}
            />
          ),
        },
        {
          key: "folders",
          label: `文件夹 ${props.folders.length}`,
          children: (
            <Table<KnowledgeFolder>
              rowKey="folder_id"
              columns={folderColumns}
              dataSource={[...props.folders]}
              loading={props.isLoading}
              pagination={false}
              scroll={{ x: 540 }}
              locale={{
                emptyText: (
                  <StateView
                    kind="empty"
                    title="没有已删除文件夹"
                    description="空文件夹移入回收站后会出现在这里。"
                  />
                ),
              }}
            />
          ),
        },
      ]}
    />
  );
}
