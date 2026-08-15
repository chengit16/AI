/** @description 授权审计与用量对账运营面板。 */
import { Table, Tabs, Tag } from "antd";
import type { TableColumnsType } from "antd";

import type { AuditRecord, UsageReconciliation } from "@/api/services/operations";
import { StateView } from "@/components/StateView/StateView";

import { formatOperationsTime } from "../config";

interface AuditUsagePanelProps {
  /** 已按当前工作空间和读取权限投影的审计事实。 */
  audit: readonly AuditRecord[];
  /** 计数器、累计明细和最后结果的逐项对账结果。 */
  usage: readonly UsageReconciliation[];
  /** 审计或用量首次加载期间统一保持表格骨架。 */
  isLoading: boolean;
}

/** 审计只展示登记字段，用量以计数器、明细累计和最后结果三方一致为健康。 */
export function AuditUsagePanel(props: AuditUsagePanelProps) {
  return (
    <Tabs
      items={[
        {
          key: "audit",
          label: `审计记录 ${props.audit.length}`,
          children: (
            <Table<AuditRecord>
              rowKey="audit_id"
              columns={auditColumns}
              dataSource={[...props.audit]}
              loading={props.isLoading}
              pagination={false}
              scroll={{ x: 980 }}
              locale={{
                emptyText: (
                  <StateView
                    kind="empty"
                    title="暂无审计记录"
                    description="受控操作会记录主体、资源和策略版本。"
                  />
                ),
              }}
            />
          ),
        },
        {
          key: "usage",
          label: `用量对账 ${props.usage.length}`,
          children: (
            <Table<UsageReconciliation>
              rowKey={(item) => `${item.metric}:${item.period_key}`}
              columns={usageColumns}
              dataSource={[...props.usage]}
              loading={props.isLoading}
              pagination={false}
              scroll={{ x: 760 }}
              locale={{
                emptyText: (
                  <StateView
                    kind="empty"
                    title="暂无用量事实"
                    description="产生计量记录后会显示三方对账结果。"
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

const auditColumns: TableColumnsType<AuditRecord> = [
  { title: "操作", dataIndex: "action", ellipsis: true },
  { title: "资源", dataIndex: "resource_type", width: 160 },
  {
    title: "结果",
    dataIndex: "outcome",
    width: 100,
    render: (value: string) => (
      <Tag color={value === "succeeded" ? "success" : value === "denied" ? "warning" : "error"}>
        {value === "succeeded" ? "成功" : value === "denied" ? "拒绝" : "失败"}
      </Tag>
    ),
  },
  { title: "权限码", dataIndex: "permission_code", width: 220, render: (value) => value ?? "--" },
  { title: "策略版本", dataIndex: "policy_version", width: 100, render: (value) => value ?? "--" },
  { title: "发生时间", dataIndex: "occurred_at", width: 150, render: formatOperationsTime },
];

const usageColumns: TableColumnsType<UsageReconciliation> = [
  { title: "计量项", dataIndex: "metric" },
  { title: "周期", dataIndex: "period_key", width: 120 },
  { title: "计数器", dataIndex: "counter_value", width: 100, render: (value) => value ?? "--" },
  { title: "明细累计", dataIndex: "record_delta_total", width: 110 },
  { title: "记录数", dataIndex: "record_count", width: 90 },
  {
    title: "一致性",
    dataIndex: "consistent",
    width: 100,
    render: (value: boolean) => (
      <Tag color={value ? "success" : "error"}>{value ? "一致" : "异常"}</Tag>
    ),
  },
];
