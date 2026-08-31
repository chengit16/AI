/** @description P6B-01 企业控制台聚合页，展示治理概况、知识增长和低敏最近内容。 */
import { useQuery } from "@tanstack/react-query";
import { Button, Progress, Skeleton, Tag } from "antd";
import {
  ArrowRight,
  BookOpenText,
  Bot,
  Building2,
  CircleAlert,
  Database,
  FileCheck2,
  FileClock,
  FileText,
  Network,
  UsersRound,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Link } from "react-router";

import { errorMessage } from "@/api/client";
import { getEnterpriseConsole } from "@/api/services/workspaces";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { pageRoutes } from "@/config/resources";

interface EnterpriseConsoleProps {
  /** 当前可信会话选中的企业空间。 */
  workspaceId: string;
  /** 打开接受邀请弹窗，不在控制台内复制邀请流程。 */
  onAcceptInvitation: () => void;
}

interface SummaryItem {
  label: string;
  value: number;
  detail: string;
  icon: LucideIcon;
  tone: "brand" | "neutral" | "warning";
}

const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const monthFormatter = new Intl.DateTimeFormat("zh-CN", { month: "short" });

function byteLabel(value: number) {
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(value < 1024 ? 0 : 1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(value < 10 * 1024 ** 3 ? 1 : 0)} GB`;
}

function trendPath(values: readonly number[]) {
  const maximum = Math.max(1, ...values);
  return values
    .map((value, index) => {
      const x = values.length === 1 ? 50 : (index / (values.length - 1)) * 100;
      const y = 38 - (value / maximum) * 30;
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function monthLabel(period: string) {
  const [year, month] = period.split("-").map(Number);
  if (!year || !month) return period;
  return monthFormatter.format(new Date(Date.UTC(year, month - 1, 1)));
}

/**
 * 呈现企业空间的统一事实入口。
 *
 * 页面只消费后端已按密级、字段遮罩和全空间授权裁剪的聚合，不在浏览器重新推导治理口径。
 */
export function EnterpriseConsole({ workspaceId, onAcceptInvitation }: EnterpriseConsoleProps) {
  const consoleQuery = useQuery({
    queryKey: ["enterprise-console", workspaceId],
    queryFn: ({ signal }) => getEnterpriseConsole(workspaceId, signal),
  });

  if (consoleQuery.isLoading) return <Skeleton active paragraph={{ rows: 12 }} />;
  if (consoleQuery.isError || !consoleQuery.data) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="企业控制台未能加载"
        description={errorMessage(consoleQuery.error)}
        action={<Button onClick={() => void consoleQuery.refetch()}>重新加载</Button>}
      />
    );
  }

  const snapshot = consoleQuery.data;
  const statistics = snapshot.statistics;
  const storagePercent =
    statistics.storage_limit_bytes === 0
      ? 0
      : Math.min(
          100,
          Math.round((statistics.storage_used_bytes / statistics.storage_limit_bytes) * 100),
        );
  const summaries: SummaryItem[] = [
    {
      label: "活跃成员",
      value: statistics.active_member_count,
      detail: "当前可参与企业协作",
      icon: UsersRound,
      tone: "brand",
    },
    {
      label: "知识库",
      value: statistics.active_knowledge_base_count,
      detail: "企业知识边界",
      icon: BookOpenText,
      tone: "neutral",
    },
    {
      label: "活跃文档",
      value: statistics.active_document_count,
      detail: `${statistics.published_document_count.toLocaleString("zh-CN")} 篇已发布`,
      icon: FileCheck2,
      tone: "brand",
    },
    {
      label: "处理中",
      value: statistics.processing_document_count,
      detail: `${statistics.failed_document_count.toLocaleString("zh-CN")} 篇需关注`,
      icon: statistics.failed_document_count > 0 ? CircleAlert : FileClock,
      tone: statistics.failed_document_count > 0 ? "warning" : "neutral",
    },
  ];
  const trendValues = snapshot.trend.map((point) => point.document_count);
  const toneClass = {
    brand: "bg-brand-soft text-brand",
    neutral: "bg-surface-subtle text-text-muted",
    warning: "bg-[#fff5df] text-[#966411]",
  } as const;

  return (
    <>
      <PageHeader
        eyebrow="ENTERPRISE COMMAND CENTER"
        title={snapshot.workspace.name}
        description="从成员、知识资产到处理状态，使用同一服务端口径掌握企业知识运行情况。"
        actions={<Button onClick={onAcceptInvitation}>接受邀请</Button>}
      />

      <section
        className="relative overflow-hidden rounded-panel bg-nav-bg px-7 py-6 text-status-foreground phone-down:px-5"
        aria-labelledby="enterprise-profile-title"
      >
        <div className="absolute -right-15 -top-20 h-55 w-55 rounded-full border border-solid border-nav-separator bg-nav-hover" />
        <div className="relative flex items-center gap-4 phone-down:items-start">
          <span className="grid h-13 w-13 flex-none place-items-center rounded-panel bg-white/10 text-accent">
            <Building2 size={25} aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 id="enterprise-profile-title" className="m-0 truncate text-xl text-white">
                {snapshot.workspace.name}
              </h2>
              <Tag color="success">
                {snapshot.workspace.status === "active" ? "运行中" : snapshot.workspace.status}
              </Tag>
            </div>
            <p className="mb-0 mt-2 max-w-180 text-sm leading-6 text-status-detail">
              {snapshot.workspace.description ??
                "企业知识治理空间 · 资料描述将在后续企业资料节点开放维护"}
            </p>
          </div>
          <span className="text-right text-xs leading-5 text-status-label phone-down:hidden">
            最终一致统计
            <br />
            {dateFormatter.format(new Date(snapshot.generated_at))}
          </span>
        </div>
      </section>

      <section
        className="mt-5 grid grid-cols-[repeat(4,minmax(0,1fr))] gap-3 desktop-down:grid-cols-2 phone-down:grid-cols-1"
        aria-label="企业知识统计"
      >
        {summaries.map((item) => {
          const Icon = item.icon;
          return (
            <article className="ui-surface-panel min-w-0 p-5" key={item.label}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="m-0 text-xs font-650 text-text-muted">{item.label}</p>
                  <strong className="mt-2 block text-[27px] leading-none text-text-strong">
                    {item.value.toLocaleString("zh-CN")}
                  </strong>
                </div>
                <span
                  className={`grid h-10 w-10 flex-none place-items-center rounded-panel ${toneClass[item.tone]}`}
                >
                  <Icon size={19} aria-hidden="true" />
                </span>
              </div>
              <p className="mb-0 mt-4 truncate text-xs text-text-muted">{item.detail}</p>
            </article>
          );
        })}
      </section>

      <div className="mt-5 grid grid-cols-[minmax(0,1.35fr)_minmax(280px,0.65fr)] gap-5 tablet-down:grid-cols-1">
        <section
          className="ui-surface-panel min-w-0 p-6 phone-down:p-5"
          aria-labelledby="growth-title"
        >
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 id="growth-title" className="m-0 text-[17px] text-text-strong">
                知识增长
              </h2>
              <p className="mb-0 mt-1 text-xs text-text-muted">按 UTC 月份统计活动文档</p>
            </div>
            <span className="text-xs text-text-muted">近 {snapshot.trend.length} 个月</span>
          </div>
          <div className="mt-6 overflow-hidden">
            <svg
              aria-label={`知识增长趋势：${trendValues.join("、")}`}
              className="block h-35 w-full overflow-visible"
              preserveAspectRatio="none"
              role="img"
              viewBox="0 0 100 44"
            >
              <path
                d="M 0 38 L 100 38"
                fill="none"
                stroke="var(--color-border-soft)"
                strokeWidth="0.5"
              />
              <path
                d={trendPath(trendValues)}
                fill="none"
                stroke="var(--color-brand)"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="2"
                vectorEffect="non-scaling-stroke"
              />
            </svg>
            <div className="grid grid-cols-[repeat(6,minmax(0,1fr))] gap-1">
              {snapshot.trend.map((point) => (
                <div className="min-w-0 text-center" key={point.period}>
                  <strong className="block text-xs text-text-strong">{point.document_count}</strong>
                  <span className="mt-1 block truncate text-[11px] text-text-muted">
                    {monthLabel(point.period)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="ui-surface-panel p-6 phone-down:p-5" aria-labelledby="storage-title">
          <div className="flex items-center gap-3">
            <span className="ui-icon-badge h-10 w-10 flex-none">
              <Database size={19} aria-hidden="true" />
            </span>
            <div>
              <h2 id="storage-title" className="m-0 text-[17px] text-text-strong">
                企业存储
              </h2>
              <p className="mb-0 mt-1 text-xs text-text-muted">原始文件累计用量</p>
            </div>
          </div>
          <p className="mb-3 mt-7 text-sm text-text-muted">
            <strong className="text-2xl text-text-strong">
              {byteLabel(statistics.storage_used_bytes)}
            </strong>{" "}
            / {byteLabel(statistics.storage_limit_bytes)}
          </p>
          <Progress percent={storagePercent} showInfo={false} strokeColor="var(--color-brand)" />
          <p className="mb-0 mt-4 text-xs leading-5 text-text-muted">
            已使用 {storagePercent}%；配额仍由上传和发布用例在服务端原子校验。
          </p>
        </section>
      </div>

      <div className="mt-5 grid grid-cols-[minmax(0,1.35fr)_minmax(280px,0.65fr)] gap-5 tablet-down:grid-cols-1">
        <section
          className="ui-surface-panel min-w-0 overflow-hidden"
          aria-labelledby="recent-content-title"
        >
          <div className="flex items-center justify-between border-b border-b-solid border-border-soft px-6 py-5 phone-down:px-5">
            <div>
              <h2 id="recent-content-title" className="m-0 text-[17px] text-text-strong">
                最近内容
              </h2>
              <p className="mb-0 mt-1 text-xs text-text-muted">仅展示当前角色可读取的低敏摘要</p>
            </div>
            <Link
              className="text-sm text-brand no-underline"
              to={pageRoutes.KnowledgeProductionPage}
            >
              查看全部
            </Link>
          </div>
          {snapshot.recent_documents.length === 0 ? (
            <div className="px-6 py-10 text-center text-sm text-text-muted">
              暂无可展示的最近内容
            </div>
          ) : (
            <ul className="m-0 list-none p-0">
              {snapshot.recent_documents.map((document) => (
                <li
                  className="flex min-w-0 items-center gap-3 border-b border-b-solid border-border-soft px-6 py-4 last:border-b-0 phone-down:px-5"
                  key={document.document_id}
                >
                  <span className="grid h-9 w-9 flex-none place-items-center rounded-panel bg-surface-subtle text-text-muted">
                    <FileText size={17} aria-hidden="true" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <strong className="block truncate text-sm text-text-strong">
                      {document.title}
                    </strong>
                    <span className="mt-1 block truncate text-xs text-text-muted">
                      {document.knowledge_base_name} ·{" "}
                      {dateFormatter.format(new Date(document.updated_at))}
                    </span>
                  </div>
                  <Tag color={document.status === "published" ? "success" : "default"}>
                    {document.status === "published" ? "已发布" : "未发布"}
                  </Tag>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section
          className="ui-surface-panel p-6 phone-down:p-5"
          aria-labelledby="quick-entry-title"
        >
          <h2 id="quick-entry-title" className="m-0 text-[17px] text-text-strong">
            治理入口
          </h2>
          <p className="mb-4 mt-1 text-xs text-text-muted">进入现有真实页面继续工作</p>
          <nav className="grid gap-2" aria-label="企业治理快捷入口">
            {[
              { label: "成员管理", icon: UsersRound, to: pageRoutes.WorkspaceMembersPage },
              { label: "组织架构", icon: Network, to: pageRoutes.WorkspaceOrganizationPage },
              { label: "知识生产", icon: BookOpenText, to: pageRoutes.KnowledgeProductionPage },
              { label: "知识助手", icon: Bot, to: pageRoutes.AssistantConversationsPage },
            ].map((entry) => {
              const Icon = entry.icon;
              return (
                <Link
                  className="flex min-h-11 items-center gap-3 rounded-ui border border-solid border-border-soft px-3 text-sm text-text no-underline transition-colors hover:border-brand-border hover:bg-brand-soft hover:text-brand"
                  key={entry.label}
                  to={entry.to}
                >
                  <Icon size={17} aria-hidden="true" />
                  <span className="flex-1">{entry.label}</span>
                  <ArrowRight size={15} aria-hidden="true" />
                </Link>
              );
            })}
          </nav>
        </section>
      </div>
    </>
  );
}
