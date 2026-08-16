/** @description Release 运营的结构化阈值告警列表。 */
import { Empty, Table, Tag, type TableColumnsType } from "antd";

import type { OperationsAlert } from "@/api/services/agentOperations";

import { alertCodeLabel, formatMetricValue, metricLabel, releaseRoleLabel } from "../config";

/** 告警面板只呈现后端稳定码和数值，不接收自由文本。 */
export interface OperationsAlertsProps {
  /** 当前 Route 中主版本与比较版本的全部结构化告警。 */
  alerts: readonly OperationsAlert[];
}

/** 展示告警严重性、Release 角色、观测值、阈值和晋级影响。 */
export function OperationsAlerts({ alerts }: OperationsAlertsProps) {
  const columns: TableColumnsType<OperationsAlert> = [
    {
      title: "级别",
      dataIndex: "severity",
      key: "severity",
      width: 90,
      render: (value: OperationsAlert["severity"]) => (
        <Tag color={value === "critical" ? "error" : "warning"}>
          {value === "critical" ? "严重" : "提醒"}
        </Tag>
      ),
    },
    {
      title: "Release",
      dataIndex: "release_role",
      key: "release_role",
      width: 110,
      render: (value: string) => releaseRoleLabel[value] ?? value,
    },
    {
      title: "告警",
      dataIndex: "code",
      key: "code",
      width: 190,
      render: (value: string) => alertCodeLabel[value] ?? value,
    },
    {
      title: "指标",
      dataIndex: "metric",
      key: "metric",
      width: 130,
      render: (value: string) => metricLabel[value] ?? value,
    },
    {
      title: "观测 / 阈值",
      key: "values",
      width: 220,
      render: (_, item) =>
        `${formatMetricValue(item.metric, item.observed_value)} / ${formatMetricValue(item.metric, item.threshold_value)}`,
    },
    {
      title: "晋级影响",
      dataIndex: "blocks_promotion",
      key: "blocks_promotion",
      width: 110,
      render: (value: boolean) =>
        value ? <Tag color="error">阻断</Tag> : <Tag color="default">仅观察</Tag>,
    },
  ];

  return (
    <section
      className="mt-6 border-0 border-t border-t-solid border-border-soft pt-5"
      aria-labelledby="operations-alerts-title"
    >
      <div className="mb-3">
        <h3 id="operations-alerts-title" className="m-0 text-lg text-text-strong">
          阈值告警
        </h3>
        <p className="mb-0 mt-1 text-sm text-text-muted">
          阻断项参与当前策略判定，观察项只用于主版本和历史版本运营跟踪。
        </p>
      </div>
      {alerts.length === 0 ? (
        <div className="rounded-ui border border-solid border-border-soft bg-surface-subtle py-4">
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前窗口没有阈值告警" />
        </div>
      ) : (
        <Table<OperationsAlert>
          rowKey={(item) => `${item.release_role}-${item.code}-${item.metric}`}
          size="small"
          pagination={false}
          columns={columns}
          dataSource={[...alerts]}
          scroll={{ x: 850 }}
        />
      )}
    </section>
  );
}
