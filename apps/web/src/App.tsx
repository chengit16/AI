import { useQuery } from "@tanstack/react-query";
import { Button, Tag, Tooltip } from "antd";
import {
  Activity,
  Boxes,
  CheckCircle2,
  ChevronsLeft,
  ChevronsRight,
  CircleAlert,
  Database,
  PanelLeft,
  RefreshCw,
  Server,
} from "lucide-react";
import { Navigate, Route, Routes } from "react-router";

import { getPlatformHealth } from "./api/health";
import { useUiStore } from "./store/ui";

const serviceLabels: Record<string, string> = {
  api: "平台 API",
  configuration: "配置中心",
};

function PlatformMark() {
  return (
    <div className="platform-mark" aria-label="AI 智能平台">
      <span className="platform-mark__symbol">AI</span>
      <span className="platform-mark__name">智能平台</span>
    </div>
  );
}

function StatusDot({ status }: { status: "ok" | "degraded" }) {
  return <span className={`status-dot status-dot--${status}`} aria-hidden="true" />;
}

function RuntimeOverview() {
  const health = useQuery({
    queryKey: ["platform-health"],
    queryFn: getPlatformHealth,
    refetchInterval: 30_000,
  });

  const isHealthy = health.data?.status === "ok";

  return (
    <main className="main-content">
      <header className="page-header">
        <div>
          <p className="page-header__eyebrow">本地运行中心</p>
          <h1>系统状态</h1>
        </div>
        <div className="page-header__actions">
          <Tag variant="filled" className="stage-tag">
            阶段 0
          </Tag>
          <Tooltip title="刷新状态">
            <Button
              aria-label="刷新状态"
              icon={<RefreshCw size={17} />}
              loading={health.isFetching}
              onClick={() => void health.refetch()}
            />
          </Tooltip>
        </div>
      </header>

      <section className={`runtime-banner runtime-banner--${isHealthy ? "ok" : "pending"}`}>
        <div className="runtime-banner__icon">
          {health.isError ? <CircleAlert size={25} /> : <CheckCircle2 size={25} />}
        </div>
        <div>
          <p className="runtime-banner__label">平台运行状态</p>
          <h2>{health.isLoading ? "正在连接" : isHealthy ? "基础服务运行正常" : "等待服务就绪"}</h2>
          <p>
            {health.isError
              ? "无法连接平台 API，请检查后端进程。"
              : `环境 ${health.data?.environment ?? "local"} · 版本 ${health.data?.version ?? "0.0.0"}`}
          </p>
        </div>
      </section>

      <section className="status-section" aria-labelledby="service-status-heading">
        <div className="section-heading">
          <div>
            <p className="section-heading__index">01</p>
            <h2 id="service-status-heading">服务检查</h2>
          </div>
          <span>{health.data ? `${Object.keys(health.data.checks).length} 项` : "--"}</span>
        </div>

        <div className="service-list">
          {health.data ? (
            Object.entries(health.data.checks).map(([name, status]) => (
              <div className="service-row" key={name}>
                <div className="service-row__identity">
                  {name === "api" ? <Server size={19} /> : <Database size={19} />}
                  <div>
                    <strong>{serviceLabels[name] ?? name}</strong>
                    <span>{name}</span>
                  </div>
                </div>
                <div className="service-row__status">
                  <StatusDot status={status} />
                  {status === "ok" ? "正常" : "降级"}
                </div>
              </div>
            ))
          ) : (
            <div className="service-row service-row--empty">
              <Activity size={19} />
              <span>{health.isError ? "API 未连接" : "正在读取服务状态"}</span>
            </div>
          )}
        </div>
      </section>

      <footer className="build-footer">
        <Boxes size={16} />
        <span>本地开发环境</span>
        <span className="build-footer__separator" />
        <span>API {health.data?.service ?? "未连接"}</span>
      </footer>
    </main>
  );
}

export function App() {
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);

  return (
    <div className={`app-shell${collapsed ? " app-shell--collapsed" : ""}`}>
      <aside className="sidebar">
        <PlatformMark />
        <nav className="primary-nav" aria-label="平台主导航">
          <a className="primary-nav__item primary-nav__item--active" href="/status">
            <PanelLeft size={18} />
            <span>运行状态</span>
          </a>
        </nav>
        <Tooltip title={collapsed ? "展开侧栏" : "收起侧栏"} placement="right">
          <Button
            className="sidebar__toggle"
            type="text"
            aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
            icon={collapsed ? <ChevronsRight size={18} /> : <ChevronsLeft size={18} />}
            onClick={toggleSidebar}
          />
        </Tooltip>
      </aside>

      <Routes>
        <Route path="/status" element={<RuntimeOverview />} />
        <Route path="*" element={<Navigate to="/status" replace />} />
      </Routes>
    </div>
  );
}
