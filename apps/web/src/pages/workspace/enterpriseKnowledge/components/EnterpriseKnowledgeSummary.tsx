/** @description 企业知识门户身份横幅与治理统计卡片。 */
import { BookCopy, FileCheck2, Network, PanelsTopLeft, ShieldCheck } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { EnterpriseKnowledgePortal } from "@/api/services/enterpriseKnowledge";

interface EnterpriseKnowledgeSummaryProps {
  /** 服务端统一事务口径的门户快照。 */
  snapshot: EnterpriseKnowledgePortal;
}

interface SummaryCard {
  label: string;
  value: number;
  detail: string;
  icon: LucideIcon;
  tone: string;
}

/** 展示企业知识治理身份与分类、知识域、文档、知识库覆盖统计。 */
export function EnterpriseKnowledgeSummary({ snapshot }: EnterpriseKnowledgeSummaryProps) {
  const cards: SummaryCard[] = [
    {
      label: "活动分类",
      value: snapshot.statistics.active_categories,
      detail: "显式治理层级",
      icon: PanelsTopLeft,
      tone: "bg-brand-soft text-brand",
    },
    {
      label: "团队知识域",
      value: snapshot.statistics.active_domains,
      detail: "成员与部门范围",
      icon: Network,
      tone: "bg-brand-soft text-brand",
    },
    {
      label: "已分类文档",
      value: snapshot.statistics.classified_documents,
      detail: "不复制原文与 Chunk",
      icon: FileCheck2,
      tone: "bg-surface-subtle text-text-muted",
    },
    {
      label: "受治理知识库",
      value: snapshot.statistics.governed_knowledge_bases,
      detail: "运行时与 PDP 取交集",
      icon: BookCopy,
      tone: "bg-[#fff5df] text-[#966411]",
    },
  ];
  return (
    <>
      <section
        className="relative overflow-hidden rounded-panel bg-nav-bg px-7 py-6 text-status-foreground phone-down:px-5"
        aria-labelledby="enterprise-knowledge-identity"
      >
        <div className="absolute -right-14 -top-24 h-58 w-58 rounded-full border border-solid border-nav-separator bg-nav-hover" />
        <div className="absolute bottom-0 right-34 h-px w-42 bg-nav-separator" />
        <div className="relative flex items-center gap-4">
          <span className="grid h-13 w-13 flex-none place-items-center rounded-panel bg-white/10 text-accent">
            <ShieldCheck size={25} aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="m-0 text-xs font-700 tracking-[0.16em] text-status-label">
              KNOWLEDGE GOVERNANCE
            </p>
            <h2
              id="enterprise-knowledge-identity"
              className="mb-0 mt-2 truncate text-xl text-white"
            >
              {snapshot.workspace_name}
            </h2>
            <p className="mb-0 mt-2 max-w-180 text-sm leading-6 text-status-detail">
              分类负责治理关系，知识域负责运行范围；两者都不复制文档、版本、解析产物或索引。
            </p>
          </div>
          <span className="text-right text-xs leading-5 text-status-label phone-down:hidden">
            单一事务快照
            <br />
            {new Date(snapshot.generated_at).toLocaleString("zh-CN", { hour12: false })}
          </span>
        </div>
      </section>
      <section
        className="mt-4 grid grid-cols-[repeat(4,minmax(0,1fr))] gap-3 desktop-down:grid-cols-2 phone-down:grid-cols-1"
        aria-label="企业知识治理摘要"
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
                <span className={"grid h-10 w-10 place-items-center rounded-panel " + card.tone}>
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
