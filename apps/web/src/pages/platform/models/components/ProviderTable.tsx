/** @description 模型供应商治理状态与允许操作的表格组件。 */
import { Button, Dropdown, Table, Tag } from "antd";
import type { MenuProps, TableColumnsType } from "antd";
import { MoreHorizontal } from "lucide-react";

import type { ModelProvider } from "@/api/services/platformModels";
import { StateView } from "@/components/StateView/StateView";

import {
  capabilityLabels,
  policyStatus,
  probeStatus,
  providerStatus,
  wireApiLabels,
} from "../config";

interface ProviderTableProps {
  /** 服务端返回的脱敏供应商治理视图。 */
  items: readonly ModelProvider[];
  /** 首次查询是否仍在进行。 */
  isLoading: boolean;
  /** 任一供应商写操作是否正在提交。 */
  isMutating: boolean;
  /** 打开指定供应商的凭证轮换流程。 */
  onRotateCredential: (provider: ModelProvider) => void;
  /** 打开指定供应商的数据政策复核流程。 */
  onReviewPolicy: (provider: ModelProvider) => void;
  /** 执行服务端探测、激活或停用命令。 */
  onAction: (providerId: string, action: "probe" | "activate" | "disable") => void;
}

/** 展示供应商接入、政策、探测与启停事实，并分发管理员治理动作。 */
export function ProviderTable({
  items,
  isLoading,
  isMutating,
  onRotateCredential,
  onReviewPolicy,
  onAction,
}: ProviderTableProps) {
  const columns: TableColumnsType<ModelProvider> = [
    {
      title: "供应商",
      key: "provider",
      render: (_, record) => (
        <div className="grid min-w-0 gap-[3px]">
          <strong className="overflow-hidden text-ellipsis whitespace-nowrap">
            {record.display_name}
          </strong>
          <span className="max-w-[420px] overflow-hidden text-ellipsis whitespace-nowrap text-xs text-text-muted">
            {record.provider_key}
          </span>
        </div>
      ),
    },
    {
      title: "接入",
      key: "endpoint",
      render: (_, record) => (
        <div className="grid min-w-0 gap-[3px]">
          <span className="max-w-[420px] overflow-hidden text-ellipsis whitespace-nowrap text-xs text-text-muted">
            {record.base_url}
          </span>
          <small className="max-w-[420px] overflow-hidden text-ellipsis whitespace-nowrap text-xs text-text-muted">
            {wireApiLabels[record.wire_api ?? "chat_completions"]} · {record.probe_model_id}
          </small>
        </div>
      ),
    },
    {
      title: "能力",
      dataIndex: "declared_capabilities",
      key: "capabilities",
      width: 190,
      render: (values: ModelProvider["declared_capabilities"]) => (
        <div className="flex flex-wrap items-center gap-1">
          {values.map((value) => (
            <Tag key={value}>{capabilityLabels[value]}</Tag>
          ))}
        </div>
      ),
    },
    {
      title: "治理状态",
      key: "governance",
      width: 210,
      render: (_, record) => (
        <div className="flex flex-wrap items-center gap-1">
          <Tag color={providerStatus[record.status].color}>
            {providerStatus[record.status].label}
          </Tag>
          <Tag color={policyStatus[record.policy_review_status].color}>
            {policyStatus[record.policy_review_status].label}
          </Tag>
          <Tag color={probeStatus[record.probe_status].color}>
            {probeStatus[record.probe_status].label}
          </Tag>
        </div>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 76,
      render: (_, record) => {
        const actions: MenuProps["items"] = [
          { key: "credential", label: "轮换凭证", onClick: () => onRotateCredential(record) },
          { key: "policy", label: "审核数据政策", onClick: () => onReviewPolicy(record) },
          { type: "divider" },
          {
            key: "probe",
            label: "探测能力",
            disabled: record.policy_review_status !== "approved",
            onClick: () => onAction(record.provider_id, "probe"),
          },
          {
            key: "activate",
            label: "启用供应商",
            disabled:
              record.status === "active" ||
              record.policy_review_status !== "approved" ||
              record.probe_status !== "passed",
            onClick: () => onAction(record.provider_id, "activate"),
          },
          {
            key: "disable",
            danger: true,
            label: "停用供应商",
            disabled: record.status !== "active",
            onClick: () => onAction(record.provider_id, "disable"),
          },
        ];
        return (
          <Dropdown menu={{ items: actions }} trigger={["click"]} disabled={isMutating}>
            <Button
              aria-label={`打开 ${record.display_name} 操作菜单`}
              type="text"
              icon={<MoreHorizontal size={17} />}
            />
          </Dropdown>
        );
      },
    },
  ];

  return (
    <Table<ModelProvider>
      rowKey="provider_id"
      columns={columns}
      dataSource={[...items]}
      loading={isLoading}
      pagination={false}
      scroll={{ x: 960 }}
      locale={{
        emptyText: (
          <StateView
            kind="empty"
            title="尚未配置模型供应商"
            description="创建 OpenAI-compatible 供应商后，再完成政策审核和能力探测。"
          />
        ),
      }}
    />
  );
}
