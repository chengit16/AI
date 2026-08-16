/** @description 当前 Route 的晋级结论、样本状态和评估边界摘要。 */
import { Tag, Typography } from "antd";
import { Ban, CheckCircle2, CircleDashed, ShieldCheck } from "lucide-react";

import type { AgentOperationsReport } from "@/api/services/agentOperations";

import { alertCodeLabel, formatOperationsTime, promotionStatus, releaseRoleLabel } from "../config";

/** 晋级结论面板只消费服务端已签名证据摘要。 */
export interface PromotionSummaryProps {
  /** 当前服务运营报告。 */
  report: AgentOperationsReport;
}

const statusIcon = {
  passed: CheckCircle2,
  blocked: Ban,
  insufficient_data: CircleDashed,
  not_applicable: ShieldCheck,
};

/** 展示晋级是否可行，并明确样本和 AI 质量评估是否已运行。 */
export function PromotionSummary({ report }: PromotionSummaryProps) {
  const display = promotionStatus[report.promotion.status];
  const StatusIcon = statusIcon[report.promotion.status];
  const comparisonLabel = report.comparison
    ? `${releaseRoleLabel[report.comparison.role]} v${report.comparison.release_version}`
    : "无比较版本";

  return (
    <section aria-labelledby="promotion-summary-title">
      <div className="flex items-start justify-between gap-4 border-0 border-b border-b-solid border-border-soft pb-5 nav-mobile:flex-col">
        <div className="flex min-w-0 items-start gap-3">
          <span className="ui-icon-badge h-10 w-10 flex-none">
            <StatusIcon size={20} aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 id="promotion-summary-title" className="m-0 text-xl text-text-strong">
                {report.service_name}
              </h2>
              <Tag color={display.color}>{display.label}</Tag>
              <Tag>
                {report.route_mode === "canary" ? `${report.canary_percent}% 灰度` : "正式路由"}
              </Tag>
            </div>
            <p className="mb-0 mt-2 text-sm text-text-muted">
              Route v{report.route_version} · {formatOperationsTime(report.window_started_at)} 至{" "}
              {formatOperationsTime(report.window_ended_at)}
            </p>
          </div>
        </div>
        <div className="text-right text-xs text-text-muted nav-mobile:text-left">
          <div>策略 {report.promotion.policy_version}</div>
          <Typography.Text
            className="mt-1 block"
            copyable={{ text: report.promotion.evidence_hash }}
          >
            证据 {report.promotion.evidence_hash.slice(0, 12)}…
          </Typography.Text>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-3 py-5 compact-down:grid-cols-2 phone-down:grid-cols-1">
        <div className="rounded-ui border border-solid border-border-soft bg-surface-subtle p-3">
          <p className="m-0 text-xs text-text-muted">正式版本</p>
          <strong className="mt-2 block text-sm text-text-strong">
            v{report.primary.release_version} · {report.primary.terminal_count} 终态样本
          </strong>
        </div>
        <div className="rounded-ui border border-solid border-border-soft bg-surface-subtle p-3">
          <p className="m-0 text-xs text-text-muted">比较版本</p>
          <strong className="mt-2 block text-sm text-text-strong">{comparisonLabel}</strong>
        </div>
        <div className="rounded-ui border border-solid border-border-soft bg-surface-subtle p-3">
          <p className="m-0 text-xs text-text-muted">最小样本门槛</p>
          <strong className="mt-2 block text-sm text-text-strong">
            {report.minimum_terminal_samples} 终态 / {report.minimum_feedback_samples} 反馈
          </strong>
        </div>
        <div className="rounded-ui border border-solid border-border-soft bg-surface-subtle p-3">
          <p className="m-0 text-xs text-text-muted">AI 质量评估</p>
          <strong className="mt-2 block text-sm text-text-strong">
            {report.ai_quality_status === "not_configured" ? "未配置" : report.ai_quality_status}
          </strong>
        </div>
      </div>

      {report.promotion.reason_codes.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-0 border-t border-t-solid border-border-soft pt-4">
          <span className="text-xs font-700 text-text-muted">判定原因</span>
          {report.promotion.reason_codes.map((code) => (
            <Tag key={code} color={report.promotion.allowed ? "default" : "warning"}>
              {alertCodeLabel[code] ?? code}
            </Tag>
          ))}
        </div>
      )}
    </section>
  );
}
