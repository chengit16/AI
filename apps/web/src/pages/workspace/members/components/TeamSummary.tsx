/** @description 团队治理摘要与企业身份横幅。 */
import { Building2, Network, UserCheck, UserRoundX, UserRoundPlus } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { TeamManagement } from "@/api/services/teamManagement";

interface TeamSummaryProps {
  /** 当前团队管理统一读模型。 */
  snapshot: TeamManagement;
}

interface SummaryCard {
  label: string;
  value: number;
  detail: string;
  icon: LucideIcon;
  tone: string;
}

/** 用紧凑治理卡片展示团队规模、风险状态和组织覆盖。 */
export function TeamSummary({ snapshot }: TeamSummaryProps) {
  const { statistics } = snapshot;
  const cards: SummaryCard[] = [
    {
      label: "启用成员",
      value: statistics.active_members,
      detail: "当前可访问企业空间",
      icon: UserCheck,
      tone: "bg-brand-soft text-brand",
    },
    {
      label: "待接受邀请",
      value: statistics.pending_invitations,
      detail: "七日有效期内待处理",
      icon: UserRoundPlus,
      tone: "bg-[#fff5df] text-[#966411]",
    },
    {
      label: "已停用",
      value: statistics.disabled_members,
      detail: "配置保留，可受控恢复",
      icon: UserRoundX,
      tone: "bg-surface-subtle text-text-muted",
    },
    {
      label: "组织结构",
      value: statistics.departments,
      detail: `${statistics.positions.toLocaleString("zh-CN")} 个岗位`,
      icon: Network,
      tone: "bg-brand-soft text-brand",
    },
  ];
  return (
    <>
      <section
        className="relative overflow-hidden rounded-panel bg-nav-bg px-7 py-6 text-status-foreground phone-down:px-5"
        aria-labelledby="team-identity-title"
      >
        <div className="absolute -right-12 -top-20 h-52 w-52 rounded-full border border-solid border-nav-separator bg-nav-hover" />
        <div className="relative flex items-center gap-4">
          <span className="grid h-13 w-13 flex-none place-items-center rounded-panel bg-white/10 text-accent">
            <Building2 size={25} aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className="m-0 text-xs font-700 tracking-[0.16em] text-status-label">
              TEAM GOVERNANCE
            </p>
            <h2 id="team-identity-title" className="mb-0 mt-2 truncate text-xl text-white">
              {snapshot.workspace.name}
            </h2>
            <p className="mb-0 mt-2 text-sm leading-6 text-status-detail">
              成员身份、组织归属和角色来源使用同一事务事实。
            </p>
          </div>
        </div>
      </section>
      <section
        className="mt-4 grid grid-cols-[repeat(4,minmax(0,1fr))] gap-3 desktop-down:grid-cols-2 phone-down:grid-cols-1"
        aria-label="团队治理摘要"
      >
        {cards.map((card) => {
          const Icon = card.icon;
          return (
            <article className="ui-surface-panel min-w-0 p-5" key={card.label}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="m-0 text-xs font-650 text-text-muted">{card.label}</p>
                  <strong className="mt-2 block text-[27px] leading-none text-text-strong">
                    {card.value.toLocaleString("zh-CN")}
                  </strong>
                </div>
                <span className={`grid h-10 w-10 place-items-center rounded-panel ${card.tone}`}>
                  <Icon size={19} aria-hidden="true" />
                </span>
              </div>
              <p className="mb-0 mt-4 truncate text-xs text-text-muted">{card.detail}</p>
            </article>
          );
        })}
      </section>
    </>
  );
}
