/** @description 服务发布页面左侧服务选择栏。 */
import { Empty, Skeleton, Tag } from "antd";
import { Route } from "lucide-react";

import type { ServiceDeployment } from "@/api/services/services";

import { serviceStatus, serviceTypeLabel } from "../config";

/** 服务列表选择和加载状态。 */
export interface ServiceRailProps {
  /** 当前空间可管理的服务聚合。 */
  items: readonly ServiceDeployment[];
  /** URL 中选中的服务 ID。 */
  selectedId: string | null;
  /** 首次服务查询状态。 */
  isLoading: boolean;
  /** 选择服务并同步 URL。 */
  onSelect: (serviceId: string) => void;
}

/** 用稳定 service_id 提供当前 Route 主从选择入口。 */
export function ServiceRail({ items, selectedId, isLoading, onSelect }: ServiceRailProps) {
  return (
    <aside className="border-0 border-r border-r-solid border-border bg-surface-subtle p-4 tablet-down:border-b tablet-down:border-b-solid tablet-down:border-r-0">
      <div className="mb-3 flex items-center gap-2 text-sm font-700 text-text-strong">
        <Route size={17} aria-hidden="true" />
        服务列表
      </div>
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : items.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无服务" />
      ) : (
        <div className="flex max-h-[680px] flex-col gap-2 overflow-y-auto tablet-down:max-h-64">
          {items.map((item) => {
            const display = serviceStatus[item.service.status] ?? {
              label: item.service.status,
              color: "default",
            };
            return (
              <button
                key={item.service.service_id}
                type="button"
                className={`w-full cursor-pointer rounded-ui border border-solid p-3 text-left transition-colors ${
                  selectedId === item.service.service_id
                    ? "border-brand-border bg-brand-soft"
                    : "border-border-soft bg-surface hover:border-brand-border"
                }`}
                aria-pressed={selectedId === item.service.service_id}
                onClick={() => onSelect(item.service.service_id)}
              >
                <span className="flex items-center justify-between gap-2">
                  <strong className="min-w-0 truncate text-sm text-text-strong">
                    {item.service.name}
                  </strong>
                  <Tag color={display.color}>{display.label}</Tag>
                </span>
                <span className="mt-2 block truncate text-xs text-text-muted">
                  {serviceTypeLabel[item.service.service_type] ?? item.service.service_type} · Route
                  v{item.route.route_version}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </aside>
  );
}
