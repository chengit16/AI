/** @description 平台依赖健康横幅和服务检查列表。 */
import { Skeleton, Tag } from "antd";
import { CheckCircle2, CircleAlert, Clock3, Database, Server } from "lucide-react";

import type { HealthResponse } from "@/api/health";
import { cn } from "@/utils/cn";

const serviceLabels: Record<string, string> = {
  api: "平台 API",
  configuration: "配置中心",
  postgres: "关系数据库",
  valkey: "缓存与会话服务",
  object_storage: "对象存储",
  document_parser: "文档解析服务",
};

const reasonLabels: Record<string, string> = { dependency_unavailable: "依赖不可用" };

interface RuntimeHealthPanelProps {
  /** 当前健康快照，首次请求前为空。 */
  health: HealthResponse | undefined;
  /** 首次加载是否仍在进行。 */
  isLoading: boolean;
  /** API 是否不可达。 */
  isError: boolean;
}

/** 区分健康、降级和不可达状态；该快照只用于观测，不替代部署层 Readiness。 */
export function RuntimeHealthPanel({ health, isLoading, isError }: RuntimeHealthPanelProps) {
  if (isLoading) return <Skeleton active paragraph={{ rows: 7 }} />;
  const isHealthy = health?.status === "ok";
  return (
    <>
      <section
        className={cn(
          "flex min-h-[150px] items-center gap-5 rounded-panel border-l-[5px] border-l-solid border-accent bg-status-banner p-8 text-status-foreground nav-mobile:p-5",
          (isError || !isHealthy) && "border-warning bg-status-banner-pending",
        )}
        aria-live="polite"
      >
        <span className="grid h-12 w-12 flex-none place-items-center rounded-panel bg-accent text-nav-bg">
          {isError || !isHealthy ? <CircleAlert size={24} /> : <CheckCircle2 size={24} />}
        </span>
        <div>
          <p className="mb-2 mt-0 text-xs font-700 text-status-label">平台运行状态</p>
          <h2 className="mb-2 mt-0 text-[21px]">
            {isError ? "无法连接平台 API" : isHealthy ? "基础服务运行正常" : "依赖服务降级"}
          </h2>
          <span className="text-[13px] text-status-detail">
            {health
              ? `环境 ${health.environment} · 版本 ${health.version}`
              : "请检查后端和容器进程"}
          </span>
        </div>
      </section>
      <section className="ui-surface-panel mt-6 overflow-hidden">
        <div className="flex items-center justify-between gap-4 border-b border-b-solid border-border-soft px-6 py-5">
          <h2 className="m-0 text-[17px]">服务检查</h2>
          <span className="text-xs text-text-muted">
            {health ? `${Object.keys(health.checks).length} 项` : "--"}
          </span>
        </div>
        <div>
          {Object.entries(health?.checks ?? {}).map(([name, status]) => (
            <div
              className="flex items-center justify-between gap-4 border-b border-b-solid border-border-soft px-6 py-5 nav-mobile:px-4"
              key={name}
            >
              <span className="flex min-w-0 items-center gap-3">
                {name === "api" ? <Server size={18} /> : <Database size={18} />}
                <span className="min-w-0">
                  <strong className="block">{serviceLabels[name] ?? name}</strong>
                  <small className="mt-[3px] flex flex-wrap items-center gap-x-3 gap-y-1 text-text-muted">
                    <span>{name}</span>
                    <span>{formatLatency(health?.details?.[name]?.latency_ms)}</span>
                    <span className="inline-flex items-center gap-1">
                      <Clock3 size={12} />
                      {formatCheckedAt(health?.details?.[name]?.checked_at)}
                    </span>
                  </small>
                </span>
              </span>
              <span className="flex flex-none items-center gap-2">
                {health?.details?.[name]?.critical && <Tag>关键</Tag>}
                <Tag color={status === "ok" ? "success" : "warning"}>
                  {status === "ok"
                    ? "正常"
                    : (reasonLabels[health?.details?.[name]?.reason_code ?? ""] ?? "降级")}
                </Tag>
              </span>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}

function formatLatency(value: number | undefined): string {
  if (value === undefined) return "未采集延迟";
  return value < 1 ? "< 1 ms" : `${Math.round(value)} ms`;
}

function formatCheckedAt(value: string | undefined): string {
  if (!value) return "尚无检查时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "检查时间无效";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}
