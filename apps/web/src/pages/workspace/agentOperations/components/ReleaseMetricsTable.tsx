/** @description 正式、灰度或上一 Release 的同口径运营指标对比表。 */
import { Table, Tag, type TableColumnsType } from "antd";

import type { AgentOperationsReport } from "@/api/services/agentOperations";

import { formatCost, formatLatency, formatRate, releaseRoleLabel } from "../config";

interface MetricRow {
  key: string;
  metric: string;
  primary: string;
  comparison: string;
}

/** Release 指标表只接受同一个后端报告，避免跨请求拼接不同 Route 版本。 */
export interface ReleaseMetricsTableProps {
  /** 当前 Route 的完整运营报告。 */
  report: AgentOperationsReport;
}

/** 构建同口径对比行；空样本保持“尚未测量”，不以 0 伪装。 */
function metricRows(report: AgentOperationsReport): MetricRow[] {
  const comparison = report.comparison;
  const compare = (value: string) => (comparison ? value : "无比较版本");
  return [
    {
      key: "runs",
      metric: "运行 / 终态 / 失败",
      primary: `${report.primary.run_count} / ${report.primary.terminal_count} / ${report.primary.failed_count}`,
      comparison: compare(
        `${comparison?.run_count ?? 0} / ${comparison?.terminal_count ?? 0} / ${comparison?.failed_count ?? 0}`,
      ),
    },
    {
      key: "success",
      metric: "成功率",
      primary: formatRate(report.primary.success_rate_bps),
      comparison: compare(formatRate(comparison?.success_rate_bps ?? null)),
    },
    {
      key: "error",
      metric: "错误率",
      primary: formatRate(report.primary.error_rate_bps),
      comparison: compare(formatRate(comparison?.error_rate_bps ?? null)),
    },
    {
      key: "degradation",
      metric: "降级率",
      primary: formatRate(report.primary.degradation_rate_bps),
      comparison: compare(formatRate(comparison?.degradation_rate_bps ?? null)),
    },
    {
      key: "latency",
      metric: "P95 延迟",
      primary: formatLatency(report.primary.latency_p95_ms),
      comparison: compare(formatLatency(comparison?.latency_p95_ms ?? null)),
    },
    {
      key: "cost",
      metric: "平均 / 最高单次成本",
      primary: `${formatCost(report.primary.average_cost_microunits)} / ${formatCost(report.primary.max_run_cost_microunits)}`,
      comparison: compare(
        `${formatCost(comparison?.average_cost_microunits ?? null)} / ${formatCost(comparison?.max_run_cost_microunits ?? null)}`,
      ),
    },
    {
      key: "feedback",
      metric: "反馈样本 / 有帮助率",
      primary: `${report.primary.feedback_count} / ${formatRate(report.primary.helpful_rate_bps)}`,
      comparison: compare(
        `${comparison?.feedback_count ?? 0} / ${formatRate(comparison?.helpful_rate_bps ?? null)}`,
      ),
    },
    {
      key: "offline",
      metric: "离线评估",
      primary: `${report.primary.offline_evaluation_status} / ${formatRate(report.primary.offline_evaluation_score_bps)}`,
      comparison: compare(
        `${comparison?.offline_evaluation_status ?? "not_run"} / ${formatRate(comparison?.offline_evaluation_score_bps ?? null)}`,
      ),
    },
  ];
}

/** 展示 Release 级运行、质量、时延和成本指标。 */
export function ReleaseMetricsTable({ report }: ReleaseMetricsTableProps) {
  const comparisonTitle = report.comparison
    ? `${releaseRoleLabel[report.comparison.role]} v${report.comparison.release_version}`
    : "比较版本";
  const columns: TableColumnsType<MetricRow> = [
    {
      title: "指标",
      dataIndex: "metric",
      key: "metric",
      width: 190,
      render: (value: string) => <strong className="text-sm text-text-strong">{value}</strong>,
    },
    {
      title: (
        <span className="flex items-center gap-2">
          正式版本 v{report.primary.release_version}
          <Tag color="success">主版本</Tag>
        </span>
      ),
      dataIndex: "primary",
      key: "primary",
      width: 280,
    },
    {
      title: comparisonTitle,
      dataIndex: "comparison",
      key: "comparison",
      width: 280,
    },
  ];

  return (
    <section className="mt-6" aria-labelledby="release-metrics-title">
      <div className="mb-3 flex items-end justify-between gap-3 nav-mobile:items-start">
        <div>
          <h3 id="release-metrics-title" className="m-0 text-lg text-text-strong">
            Release 指标对比
          </h3>
          <p className="mb-0 mt-1 text-sm text-text-muted">
            所有比例均来自当前窗口内的终态样本，成本以契约微单位显示。
          </p>
        </div>
      </div>
      <Table<MetricRow>
        rowKey="key"
        size="small"
        pagination={false}
        columns={columns}
        dataSource={metricRows(report)}
        scroll={{ x: 750 }}
      />
    </section>
  );
}
