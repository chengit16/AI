import { useQuery } from "@tanstack/react-query";
import { Button, Skeleton, Tag, Tooltip } from "antd";
import { CheckCircle2, CircleAlert, Database, RefreshCw, Server } from "lucide-react";

import { getPlatformHealth } from "@/api/health";
import { PageHeader } from "@/components/PageHeader/PageHeader";

import styles from "./StatusPage.module.css";

const serviceLabels: Record<string, string> = { api: "平台 API", configuration: "配置中心" };

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
            className={`${styles.banner} ${health.isError || !isHealthy ? styles.pending : ""}`}
            aria-live="polite"
          >
            <span className={styles.bannerIcon}>
              {health.isError ? <CircleAlert size={24} /> : <CheckCircle2 size={24} />}
            </span>
            <div>
              <p>平台运行状态</p>
              <h2>
                {health.isError
                  ? "无法连接平台 API"
                  : isHealthy
                    ? "基础服务运行正常"
                    : "依赖服务降级"}
              </h2>
              <span>
                {health.data
                  ? `环境 ${health.data.environment} · 版本 ${health.data.version}`
                  : "请检查后端和容器进程"}
              </span>
            </div>
          </section>
          <section className={styles.serviceSection}>
            <div className={styles.sectionHeading}>
              <h2>服务检查</h2>
              <span>{health.data ? `${Object.keys(health.data.checks).length} 项` : "--"}</span>
            </div>
            <div>
              {Object.entries(health.data?.checks ?? {}).map(([name, status]) => (
                <div className={styles.serviceRow} key={name}>
                  <span className={styles.serviceIdentity}>
                    {name === "api" ? <Server size={18} /> : <Database size={18} />}
                    <span>
                      <strong>{serviceLabels[name] ?? name}</strong>
                      <small>{name}</small>
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
