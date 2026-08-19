/** @description AI 运行配置版本、激活状态和低敏详情入口表格。 */
import { Button, Popconfirm, Space, Table, Tag, Tooltip } from "antd";
import type { TableColumnsType } from "antd";
import { Eye, Rocket } from "lucide-react";
import { useState } from "react";

import type { AiRuntimeConfig, ModelProvider } from "@/api/services/platformModels";
import { StateView } from "@/components/StateView/StateView";

import { formatTimestamp } from "../config";
import { RuntimeDetailsDrawer } from "./RuntimeDetailsDrawer";

interface RuntimeTableProps {
  /** 全部不可变运行配置版本。 */
  items: readonly AiRuntimeConfig[];
  /** 用于在详情中显示路由对应的供应商名称和稳定标识。 */
  providers: readonly ModelProvider[];
  /** 当前发布指针指向的版本 ID；未发布时为空。 */
  currentId: string | null;
  /** 版本清单是否正在加载。 */
  isLoading: boolean;
  /** 发布命令是否正在提交。 */
  isActivating: boolean;
  /** 请求原子发布指定运行配置版本。 */
  onActivate: (runtimeConfigVersionId: string) => void;
}

/** 展示不可变运行配置版本，并只对非当前版本提供发布入口。 */
export function RuntimeTable({
  items,
  providers,
  currentId,
  isLoading,
  isActivating,
  onActivate,
}: RuntimeTableProps) {
  const [selectedRuntime, setSelectedRuntime] = useState<AiRuntimeConfig | null>(null);
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
      width: 150,
      render: (_, record) => (
        <Space size={4}>
          <Tooltip title="查看冻结参数">
            <Button
              type="text"
              icon={<Eye size={16} />}
              aria-label={`查看 ${record.display_name} 详情`}
              onClick={() => setSelectedRuntime(record)}
            />
          </Tooltip>
          {record.runtime_config_version_id !== currentId && (
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
          )}
        </Space>
      ),
    },
  ];

  return (
    <>
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
      <RuntimeDetailsDrawer
        runtime={selectedRuntime}
        providers={providers}
        onClose={() => setSelectedRuntime(null)}
      />
    </>
  );
}
