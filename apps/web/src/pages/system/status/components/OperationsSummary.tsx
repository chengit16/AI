/** @description 运营工作台低基数指标摘要带。 */
import { Skeleton } from "antd";
import { Activity, DatabaseZap, Files, RadioTower } from "lucide-react";

import type { OperationsOverview } from "@/api/services/operations";

interface OperationsSummaryProps {
  /** 服务端同一快照计算的状态计数。 */
  overview: OperationsOverview | undefined;
  /** 首次加载状态。 */
  isLoading: boolean;
}

/** 只展示安全聚合计数，不在浏览器解析 Prometheus 原始文本。 */
export function OperationsSummary({ overview, isLoading }: OperationsSummaryProps) {
  if (isLoading) return <Skeleton active paragraph={{ rows: 2 }} />;
  const items = [
    {
      label: "活动入库任务",
      value: sumStatuses(overview?.ingestion, ["queued", "running", "retry_wait"]),
      detail: `${sumStatuses(overview?.ingestion)} 个任务事实`,
      icon: Files,
    },
    {
      label: "索引异常 / 待执行",
      value: sumStatuses(overview?.index_requests, [
        "pending",
        "running",
        "retry_wait",
        "dead_letter",
      ]),
      detail: `${sumStatuses(overview?.index_versions)} 个索引版本`,
      icon: DatabaseZap,
    },
    {
      label: "Outbox 积压",
      value: sumStatuses(overview?.outbox, ["pending", "publishing", "dead_letter"]),
      detail: `${sumStatuses(overview?.outbox, ["published"])} 条已发布`,
      icon: RadioTower,
    },
    {
      label: "生命周期运行",
      value: overview?.lifecycle_active_count ?? 0,
      detail: "导出、清除与保留期",
      icon: Activity,
    },
  ];
  return (
    <section className="ui-surface-panel mt-6 overflow-hidden" aria-label="运营指标摘要">
      <div className="grid grid-cols-4 divide-x divide-y-0 divide-solid divide-border-soft desktop-down:grid-cols-2 nav-mobile:grid-cols-1 nav-mobile:divide-x-0 nav-mobile:divide-y">
        {items.map((item) => {
          const Icon = item.icon;
          return (
            <div className="flex min-h-28 items-center gap-4 px-5 py-4" key={item.label}>
              <span className="ui-icon-badge h-10 w-10 flex-none">
                <Icon size={19} aria-hidden="true" />
              </span>
              <span className="min-w-0">
                <span className="block text-xs text-text-muted">{item.label}</span>
                <strong className="mt-1 block text-2xl text-text-strong">{item.value}</strong>
                <span className="mt-1 block truncate text-xs text-text-muted">{item.detail}</span>
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function sumStatuses(
  items: readonly { status: string; count: number }[] | undefined,
  statuses?: readonly string[],
): number {
  return (items ?? [])
    .filter((item) => !statuses || statuses.includes(item.status))
    .reduce((total, item) => total + item.count, 0);
}
