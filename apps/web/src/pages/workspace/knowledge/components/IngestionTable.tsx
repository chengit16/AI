import { Button, Table, Tag, Tooltip } from "antd";
import type { TableColumnsType } from "antd";
import { RotateCcw } from "lucide-react";

import type { IngestionJob } from "@/api/services/knowledge";
import { StateView } from "@/components/StateView/StateView";

import { formatTimestamp, ingestionStatus } from "../config";

interface IngestionTableProps {
  items: readonly IngestionJob[];
  isLoading: boolean;
  canRetry: boolean;
  isRetrying: boolean;
  onRetry: (ingestionJobId: string) => void;
}

/** 展示解析任务状态、脱敏失败定位和后端允许的人工重试入口。 */
export function IngestionTable({
  items,
  isLoading,
  canRetry,
  isRetrying,
  onRetry,
}: IngestionTableProps) {
  const columns: TableColumnsType<IngestionJob> = [
    {
      title: "来源",
      key: "source",
      render: (_, record) => (
        <div className="grid min-w-0 gap-[3px]">
          <strong className="overflow-hidden text-ellipsis whitespace-nowrap">
            {record.source_name}
          </strong>
          <span className="overflow-hidden text-ellipsis whitespace-nowrap text-xs text-text-muted">
            {record.source_media_type}
          </span>
        </div>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (value: IngestionJob["status"]) => {
        const status = ingestionStatus[value];
        return <Tag color={status.color}>{status.label}</Tag>;
      },
    },
    {
      title: "尝试",
      key: "attempts",
      width: 100,
      render: (_, record) => `${record.attempt_count}/${record.max_attempts}`,
    },
    {
      title: "结果 / 失败定位",
      key: "result",
      render: (_, record) =>
        record.status === "failed" ? (
          <div className="grid max-w-[360px] min-w-0 gap-[3px]">
            <strong className="text-xs text-danger-text">{record.error_code ?? "UNKNOWN"}</strong>
            <span className="overflow-hidden text-ellipsis whitespace-normal text-xs text-text-muted">
              {record.error_message ?? "未提供错误详情"}
            </span>
          </div>
        ) : (
          <span className="text-xs text-text-muted">
            {record.block_count === null ? "等待处理" : `${record.block_count} 个内容块`}
          </span>
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
      width: 96,
      render: (_, record) =>
        record.can_retry_manually && canRetry ? (
          <Button
            type="text"
            icon={<RotateCcw size={16} />}
            loading={isRetrying}
            onClick={() => onRetry(record.ingestion_job_id)}
          >
            重试
          </Button>
        ) : record.status === "failed" ? (
          <Tooltip title="内容或格式错误需要修复后上传新版本">
            <span className="text-xs text-text-muted">不可重试</span>
          </Tooltip>
        ) : null,
    },
  ];

  return (
    <Table<IngestionJob>
      rowKey="ingestion_job_id"
      columns={columns}
      dataSource={[...items]}
      loading={isLoading}
      pagination={false}
      scroll={{ x: 860 }}
      locale={{
        emptyText: (
          <StateView
            kind="empty"
            title="暂无入库任务"
            description="上传文档后会自动创建解析任务。"
          />
        ),
      }}
    />
  );
}
