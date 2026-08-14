import { Button, Popconfirm, Table, Tag } from "antd";
import type { TableColumnsType } from "antd";
import { Rocket } from "lucide-react";

import type { AiRuntimeConfig } from "@/api/services/platformModels";
import { StateView } from "@/components/StateView/StateView";

import { formatTimestamp } from "../config";

interface RuntimeTableProps {
  items: readonly AiRuntimeConfig[];
  currentId: string | null;
  isLoading: boolean;
  isActivating: boolean;
  onActivate: (runtimeConfigVersionId: string) => void;
}

/** 展示不可变运行配置版本，并只对非当前版本提供发布入口。 */
export function RuntimeTable({
  items,
  currentId,
  isLoading,
  isActivating,
  onActivate,
}: RuntimeTableProps) {
  const columns: TableColumnsType<AiRuntimeConfig> = [
    {
      title: "运行配置",
      key: "runtime",
      render: (_, record) => (
        <div className="grid min-w-0 gap-[3px]">
          <strong className="overflow-hidden text-ellipsis whitespace-nowrap">
            {record.display_name}
          </strong>
          <span className="max-w-[420px] overflow-hidden text-ellipsis whitespace-nowrap text-xs text-text-muted">
            {record.runtime_config_version_id}
          </span>
        </div>
      ),
    },
    {
      title: "版本",
      dataIndex: "version_number",
      key: "version_number",
      width: 90,
      render: (value, record) => (
        <div className="flex flex-wrap items-center gap-1">
          <span>V{value}</span>
          {record.runtime_config_version_id === currentId && <Tag color="success">当前</Tag>}
        </div>
      ),
    },
    {
      title: "路由",
      key: "routes",
      width: 180,
      render: (_, record) => `${record.routes.length} 条 / ${record.policy.total_timeout_ms} ms`,
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 170,
      render: formatTimestamp,
    },
    {
      title: "操作",
      key: "actions",
      width: 120,
      render: (_, record) =>
        record.runtime_config_version_id === currentId ? null : (
          <Popconfirm
            title="发布此运行配置？"
            description="后续模型调用将使用该不可变版本。"
            okText="发布"
            cancelText="取消"
            onConfirm={() => onActivate(record.runtime_config_version_id)}
          >
            <Button type="text" icon={<Rocket size={16} />} loading={isActivating}>
              发布
            </Button>
          </Popconfirm>
        ),
    },
  ];

  return (
    <Table<AiRuntimeConfig>
      rowKey="runtime_config_version_id"
      columns={columns}
      dataSource={[...items]}
      loading={isLoading}
      pagination={false}
      scroll={{ x: 760 }}
      locale={{
        emptyText: (
          <StateView
            kind="empty"
            title="尚未创建运行配置"
            description="至少启用一个供应商后，创建并发布不可变运行配置。"
          />
        ),
      }}
    />
  );
}
