/** @description Release 运营页面的服务选择栏。 */
import { Empty, Skeleton, Tag } from "antd";
import { Activity } from "lucide-react";

import type { ServiceDeployment } from "@/api/services/services";

/** 服务选择栏的查询状态与 URL 选择回调。 */
export interface OperationsServiceRailProps {
  /** 当前空间可见的服务定义及 Route 聚合。 */
  items: readonly ServiceDeployment[];
  /** URL 中当前选中的稳定 Service ID。 */
  selectedId: string | null;
  /** 首次服务清单查询状态。 */
  isLoading: boolean;
  /** 选择服务并更新可恢复 URL。 */
  onSelect: (serviceId: string) => void;
}

/** 使用服务身份和 Route 模式提供运营查询入口。 */
export function OperationsServiceRail(props: OperationsServiceRailProps) {
  return (
    <aside className="border-0 border-r border-r-solid border-border bg-surface-subtle p-4 tablet-down:border-b tablet-down:border-b-solid tablet-down:border-r-0">
      <div className="mb-3 flex items-center gap-2 text-sm font-700 text-text-strong">
        <Activity size={17} aria-hidden="true" />
        监控服务
      </div>
      {props.isLoading ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : props.items.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可监控服务" />
      ) : (
        <div className="flex max-h-[720px] flex-col gap-2 overflow-y-auto tablet-down:max-h-56">
          {props.items.map((item) => {
            const isSelected = props.selectedId === item.service.service_id;
            return (
              <button
                key={item.service.service_id}
                type="button"
                className={`w-full cursor-pointer rounded-ui border border-solid p-3 text-left transition-colors ${
                  isSelected
                    ? "border-brand-border bg-brand-soft"
                    : "border-border-soft bg-surface hover:border-brand-border"
                }`}
                aria-pressed={isSelected}
                onClick={() => props.onSelect(item.service.service_id)}
              >
                <span className="flex items-center justify-between gap-2">
                  <strong className="min-w-0 truncate text-sm text-text-strong">
                    {item.service.name}
                  </strong>
                  <Tag color={item.route.route_mode === "canary" ? "processing" : "default"}>
                    {item.route.route_mode === "canary"
                      ? "灰度"
                      : `Route v${item.route.route_version}`}
                  </Tag>
                </span>
                <span className="mt-2 block truncate text-xs text-text-muted">
                  {item.service.service_key} · Release 运营
                </span>
              </button>
            );
          })}
        </div>
      )}
    </aside>
  );
}
