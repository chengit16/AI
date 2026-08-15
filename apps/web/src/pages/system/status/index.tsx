/** @description 本地运行状态页，轮询平台健康接口并区分正常、降级与不可达状态。 */
import { useQuery } from "@tanstack/react-query";
import { Button, Skeleton, Tag, Tooltip } from "antd";
import { CheckCircle2, CircleAlert, Database, RefreshCw, Server } from "lucide-react";

import { getPlatformHealth } from "@/api/health";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { cn } from "@/utils/cn";

const serviceLabels: Record<string, string> = {
  api: "平台 API",
  configuration: "配置中心",
  postgres: "关系数据库",
  valkey: "缓存与会话服务",
  object_storage: "对象存储",
  document_parser: "文档解析服务",
};

/** 展示本地运行健康快照；30 秒轮询只用于观测，不替代部署层 Readiness。 */
export default function StatusPage() {
  const health = useQuery({
    queryKey: ["platform-health"],
    queryFn: getPlatformHealth,
    refetchInterval: 30_000,
  });
  const isHealthy = health.data?.status === "ok";

  return (
    <>
      <PageHeader
        eyebrow="LOCAL RUNTIME"
        title="运行状态"
        description="查看本地 API 与基础配置的就绪状态。"
        actions={
          <Tooltip title="刷新状态">
            <Button
              aria-label="刷新状态"
              icon={<RefreshCw size={17} />}
              loading={health.isFetching}
              onClick={() => void health.refetch()}
            />
          </Tooltip>
        }
      />
      {health.isLoading ? (
        <Skeleton active paragraph={{ rows: 7 }} />
      ) : (
        <>
          <section
            className={cn(
              "flex min-h-[150px] items-center gap-5 rounded-panel border-l-[5px] border-l-solid border-accent bg-status-banner p-8 text-status-foreground",
              (health.isError || !isHealthy) && "border-warning bg-status-banner-pending",
            )}
            aria-live="polite"
          >
            <span className="grid h-12 w-12 flex-none place-items-center rounded-panel bg-accent text-nav-bg">
              {health.isError || !isHealthy ? (
                <CircleAlert size={24} />
              ) : (
                <CheckCircle2 size={24} />
              )}
            </span>
            <div>
              <p className="mb-2 mt-0 text-xs font-700 text-status-label">平台运行状态</p>
              <h2 className="mb-2 mt-0 text-[21px]">
                {health.isError
                  ? "无法连接平台 API"
                  : isHealthy
                    ? "基础服务运行正常"
                    : "依赖服务降级"}
              </h2>
              <span className="text-[13px] text-status-detail">
                {health.data
                  ? `环境 ${health.data.environment} · 版本 ${health.data.version}`
                  : "请检查后端和容器进程"}
              </span>
            </div>
          </section>
          <section className="ui-surface-panel mt-6 overflow-hidden">
            <div className="flex items-center justify-between gap-4 border-b border-b-solid border-border-soft px-6 py-5">
              <h2 className="m-0 text-[17px]">服务检查</h2>
              <span className="text-xs text-text-muted">
                {health.data ? `${Object.keys(health.data.checks).length} 项` : "--"}
              </span>
            </div>
            <div>
              {Object.entries(health.data?.checks ?? {}).map(([name, status]) => (
                <div
                  className="flex items-center justify-between gap-4 border-b border-b-solid border-border-soft px-6 py-5"
                  key={name}
                >
                  <span className="flex items-center gap-3">
                    {name === "api" ? <Server size={18} /> : <Database size={18} />}
                    <span>
                      <strong className="block">{serviceLabels[name] ?? name}</strong>
                      <small className="mt-[3px] block text-text-muted">{name}</small>
                    </span>
                  </span>
                  <Tag color={status === "ok" ? "success" : "warning"}>
                    {status === "ok" ? "正常" : "降级"}
                  </Tag>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </>
  );
}
