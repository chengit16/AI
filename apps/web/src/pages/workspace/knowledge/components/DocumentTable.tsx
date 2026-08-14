import { Button, Dropdown, Popconfirm, Table, Tag } from "antd";
import type { MenuProps, TableColumnsType } from "antd";
import { CheckCircle2, MoreHorizontal, Send, Upload } from "lucide-react";

import type { IngestionJob, KnowledgeDocumentSummary } from "@/api/services/knowledge";
import { StateView } from "@/components/StateView/StateView";

import { documentStatus, formatTimestamp, securityLevelLabels, visibilityLabels } from "../config";

interface DocumentTableProps {
  items: readonly KnowledgeDocumentSummary[];
  jobs: readonly IngestionJob[];
  isLoading: boolean;
  canUploadVersion: boolean;
  canMarkReady: boolean;
  canPublish: boolean;
  isMutating: boolean;
  onUploadVersion: (document: KnowledgeDocumentSummary) => void;
  onMarkReady: (document: KnowledgeDocumentSummary, contentHash: string) => void;
  onPublish: (document: KnowledgeDocumentSummary) => void;
}

/**
 * 展示文档最新版本及允许的发布动作。
 *
 * 操作按钮只依据页面权限和服务端状态快照裁剪，后端仍需校验版本状态、内容摘要和资源范围。
 */
export function DocumentTable({
  items,
  jobs,
  isLoading,
  canUploadVersion,
  canMarkReady,
  canPublish,
  isMutating,
  onUploadVersion,
  onMarkReady,
  onPublish,
}: DocumentTableProps) {
  const latestJobByVersion = new Map(jobs.map((job) => [job.document_version_id, job]));
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
        const actions: MenuProps["items"] = canUploadVersion
          ? [
              {
                key: "upload-version",
                label: "上传新版本",
                icon: <Upload size={15} />,
                onClick: () => onUploadVersion(record),
              },
            ]
          : [];
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
      pagination={false}
      scroll={{ x: 820 }}
      locale={{
        emptyText: (
          <StateView
            kind="empty"
            title="还没有文档"
            description="上传首份文档后，可以在这里跟踪解析和发布状态。"
          />
        ),
      }}
    />
  );
}
