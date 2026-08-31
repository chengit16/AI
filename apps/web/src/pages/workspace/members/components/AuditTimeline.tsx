/** @description 团队治理近期审计时间线。 */
import { Timeline, Tag } from "antd";
import { ShieldCheck } from "lucide-react";

import type { TeamManagement } from "@/api/services/teamManagement";

import { auditActionLabel, formatTeamTime } from "../teamUtils";

interface AuditTimelineProps {
  /** 服务端返回的近期低敏团队治理审计。 */
  audits: TeamManagement["recent_audits"];
}

/** 展示真实审计事实，不从成员更新时间反向推断治理事件。 */
export function AuditTimeline({ audits }: AuditTimelineProps) {
  return (
    <section className="ui-surface-panel p-6 phone-down:p-5" aria-labelledby="team-audit-title">
      <div className="mb-5 flex items-center gap-3">
        <span className="ui-icon-badge h-10 w-10">
          <ShieldCheck size={18} />
        </span>
        <div>
          <h2 id="team-audit-title" className="m-0 text-[17px] text-text-strong">
            治理时间线
          </h2>
          <p className="mb-0 mt-1 text-xs text-text-muted">最近 {audits.length} 条团队治理审计</p>
        </div>
      </div>
      {audits.length === 0 ? (
        <p className="m-0 text-sm text-text-muted">暂无团队治理审计记录</p>
      ) : (
        <Timeline
          items={audits.map((audit) => ({
            color: audit.outcome === "succeeded" ? "green" : "red",
            children: (
              <div className="pb-2">
                <div className="flex flex-wrap items-center gap-2">
                  <strong className="text-sm text-text-strong">
                    {auditActionLabel(audit.action)}
                  </strong>
                  <Tag color={audit.outcome === "succeeded" ? "success" : "error"}>
                    {audit.outcome === "succeeded" ? "成功" : "失败"}
                  </Tag>
                </div>
                <p className="mb-0 mt-1 text-xs leading-5 text-text-muted">
                  {audit.actor_display_name ?? "受字段权限保护的操作者"} ·{" "}
                  {formatTeamTime(audit.occurred_at)}
                </p>
              </div>
            ),
          }))}
        />
      )}
    </section>
  );
}
