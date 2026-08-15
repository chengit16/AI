/** @description 跨知识库入库任务运营表格。 */
import { Button, Popconfirm, Table, Tag } from "antd";
import type { TableColumnsType } from "antd";
import { Ban, RotateCcw } from "lucide-react";

import type { OperationsIngestionJob } from "@/api/services/operations";
import { StateView } from "@/components/StateView/StateView";

import { formatOperationsTime, presentStatus } from "../config";

interface IngestionOperationsPanelProps {
  /** 当前工作空间跨知识库的脱敏入库任务。 */
  items: readonly OperationsIngestionJob[];
  /** 任务列表首次加载时的表格状态。 */
  isLoading: boolean;
  /** 是否显示取消入口；服务端仍按任务现态复核。 */
  canCancel: boolean;
  /** 是否显示人工恢复入口；服务端仍限制恢复次数。 */
  canRetry: boolean;
  /** 任一任务命令执行期间阻止重复点击。 */
  isMutating: boolean;
  /** 请求把非终态任务转换为稳定取消终态。 */
  onCancel: (jobId: string) => void;
  /** 请求为失败或超时任务开启新执行代次。 */
  onRetry: (jobId: string) => void;
}

/** 展示任务恢复边界，取消和重试入口仍以服务端当前状态复核为准。 */
export function IngestionOperationsPanel(props: IngestionOperationsPanelProps) {
  const columns: TableColumnsType<OperationsIngestionJob> = [
    {
      title: "任务",
      key: "task",
      render: (_, item) => (
        <span className="grid min-w-0 gap-1">
          <strong className="truncate">{item.source_name}</strong>
          <span className="text-xs text-text-muted">{item.processing_lane.toUpperCase()}</span>
        </span>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (value: string) => {
        const status = presentStatus(value);
        return <Tag color={status.color}>{status.label}</Tag>;
      },
    },
    {
      title: "尝试",
      width: 110,
      render: (_, item) => `${item.attempt_count}/${item.max_attempts}`,
    },
    {
      title: "错误码",
      dataIndex: "error_code",
      width: 180,
      render: (value: string | null) => value ?? "--",
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      width: 150,
      render: formatOperationsTime,
    },
    {
      title: "操作",
      key: "actions",
      width: 170,
      render: (_, item) => (
        <span className="flex items-center gap-1">
          {props.canCancel && ["queued", "running", "retry_wait"].includes(item.status) && (
            <Popconfirm
              title="取消这个入库任务？"
              description="活动 Attempt 会失去租约，迟到结果不会覆盖取消终态。"
              okText="确认取消"
              cancelText="返回"
              onConfirm={() => props.onCancel(item.ingestion_job_id)}
            >
              <Button type="text" danger icon={<Ban size={15} />} loading={props.isMutating}>
                取消
              </Button>
            </Popconfirm>
          )}
          {props.canRetry && ["failed", "timed_out"].includes(item.status) && (
            <Button
              type="text"
              icon={<RotateCcw size={15} />}
              loading={props.isMutating}
              onClick={() => props.onRetry(item.ingestion_job_id)}
            >
              重试
            </Button>
          )}
        </span>
      ),
    },
  ];
  return (
    <Table<OperationsIngestionJob>
      rowKey="ingestion_job_id"
      columns={columns}
      dataSource={[...props.items]}
      loading={props.isLoading}
      pagination={false}
      scroll={{ x: 860 }}
      locale={{
        emptyText: (
          <StateView
            kind="empty"
            title="暂无入库任务"
            description="上传文档后会在这里显示任务状态。"
          />
        ),
      }}
    />
  );
}
