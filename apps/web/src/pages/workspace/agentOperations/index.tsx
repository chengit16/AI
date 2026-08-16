/**
 * @description AgentRelease 运营页面编排
 *
 * 服务和时间窗口进入 URL；右侧只消费同一次报告中的 Route、Release、告警和晋级证据。
 */
import { Button, Segmented, Skeleton, Tag } from "antd";
import { RefreshCw } from "lucide-react";
import { useEffect, useMemo, type ReactNode } from "react";
import { useSearchParams } from "react-router";

import { errorMessage } from "@/api/client";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";

import { OperationsAlerts } from "./components/OperationsAlerts";
import { OperationsServiceRail } from "./components/OperationsServiceRail";
import { PromotionSummary } from "./components/PromotionSummary";
import { ReleaseMetricsTable } from "./components/ReleaseMetricsTable";
import { operationsWindowOptions, type OperationsWindowHours } from "./config";
import { useAgentOperations } from "./useAgentOperations";

/** 从 URL 读取受控窗口，非法值稳定回退到 24 小时。 */
function resolveWindowHours(value: string | null): OperationsWindowHours {
  const parsed = Number(value);
  return operationsWindowOptions.includes(parsed as OperationsWindowHours)
    ? (parsed as OperationsWindowHours)
    : 24;
}

/** 展示 AgentRelease 级质量、时延、错误、降级、成本与晋级结论。 */
export default function AgentOperationsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("service");
  const windowHours = resolveWindowHours(searchParams.get("window"));
  const model = useAgentOperations(selectedId, windowHours);

  // 1. 服务选择与窗口共同进入 URL；查询失败后清空旧列表，避免误认旧 Route 仍有效。
  const services = useMemo(
    () => (model.services.isError ? [] : (model.services.data ?? [])),
    [model.services.data, model.services.isError],
  );
  const selected = services.find((item) => item.service.service_id === selectedId) ?? null;
  const updateQuery = (serviceId: string | null, hours: OperationsWindowHours) => {
    const next = new URLSearchParams(searchParams);
    if (serviceId) next.set("service", serviceId);
    else next.delete("service");
    next.set("window", String(hours));
    setSearchParams(next);
  };
  useEffect(() => {
    if (!selectedId && services[0]) {
      const next = new URLSearchParams(searchParams);
      next.set("service", services[0].service.service_id);
      next.set("window", String(windowHours));
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, selectedId, services, setSearchParams, windowHours]);

  if (model.services.isError) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="Release 运营页面未能加载"
        description={errorMessage(model.services.error)}
        action={<Button onClick={() => void model.services.refetch()}>重新加载</Button>}
      />
    );
  }

  // 2. 报告区域区分未选择、首次加载和失败，不保留其他服务或窗口的历史数据。
  let reportArea: ReactNode;
  if (!selected) {
    reportArea = (
      <StateView
        kind="empty"
        title="选择一个服务查看运营状态"
        description="运营报告按当前 Route 对比正式版本与灰度或上一版本。"
      />
    );
  } else if (model.report.isLoading) {
    reportArea = <Skeleton active paragraph={{ rows: 12 }} />;
  } else if (model.report.isError || !model.report.data) {
    reportArea = (
      <StateView
        kind="error"
        title="Release 运营数据未能加载"
        description={errorMessage(model.report.error)}
        action={<Button onClick={() => void model.report.refetch()}>重新加载</Button>}
      />
    );
  } else {
    reportArea = (
      <>
        <PromotionSummary report={model.report.data} />
        <ReleaseMetricsTable report={model.report.data} />
        <OperationsAlerts alerts={model.report.data.alerts} />
      </>
    );
  }

  // 3. 时间窗口切换只触发只读聚合；刷新同时更新服务 Route 和当前报告。
  return (
    <>
      <PageHeader
        eyebrow="RELEASE OPERATIONS"
        title="Release 运营"
        description="按服务比较 Agent Release 的质量、时延、错误、降级和成本，并核验灰度晋级门禁。"
        actions={
          <Button
            icon={<RefreshCw size={16} />}
            loading={model.services.isFetching || model.report.isFetching}
            onClick={() => void Promise.all([model.services.refetch(), model.report.refetch()])}
          >
            刷新
          </Button>
        }
      />
      <div className="ui-surface-panel grid min-h-[720px] grid-cols-[minmax(220px,280px)_minmax(0,1fr)] overflow-hidden tablet-down:grid-cols-1">
        <OperationsServiceRail
          items={services}
          selectedId={selectedId}
          isLoading={model.services.isLoading}
          onSelect={(serviceId) => updateQuery(serviceId, windowHours)}
        />
        <section className="min-w-0 p-5 phone-down:p-3" aria-label="Release 运营报告">
          <div className="mb-5 flex items-center justify-between gap-3 border-0 border-b border-b-solid border-border-soft pb-4 nav-mobile:items-start phone-down:flex-col">
            <div className="flex flex-wrap items-center gap-2 text-sm text-text-muted">
              <span className="font-700 text-text-strong">统计窗口</span>
              <Tag>终态样本</Tag>
              <Tag>不含正文与主体标识</Tag>
            </div>
            <Segmented
              aria-label="统计窗口"
              value={windowHours}
              options={operationsWindowOptions.map((hours) => ({
                value: hours,
                label: hours === 168 ? "7 天" : `${hours} 小时`,
              }))}
              onChange={(value) => updateQuery(selectedId, value as OperationsWindowHours)}
            />
          </div>
          {reportArea}
        </section>
      </div>
    </>
  );
}
