/** @description Agent 控制台左侧选择栏，展示定义状态与当前草稿 revision。 */
import { Empty, Skeleton, Tag } from "antd";
import { Bot } from "lucide-react";

import type { AgentDetail } from "@/api/services/agents";

/** Agent 列表选择和加载状态。 */
export interface AgentRailProps {
  /** 当前空间经过后端授权后的 Agent 聚合。 */
  items: readonly AgentDetail[];
  /** URL 中选中的 Agent ID。 */
  selectedId: string | null;
  /** 首次列表查询状态。 */
  isLoading: boolean;
  /** 选择 Agent 时同步 URL 的回调。 */
  onSelect: (agentId: string) => void;
}

/** 以稳定 Agent ID 提供可键盘操作的主从选择入口。 */
export function AgentRail({ items, selectedId, isLoading, onSelect }: AgentRailProps) {
  return (
    <aside className="border-0 border-r border-r-solid border-border bg-surface-subtle p-4 tablet-down:border-b tablet-down:border-b-solid tablet-down:border-r-0">
      <div className="mb-3 flex items-center gap-2 text-sm font-700 text-text-strong">
        <Bot size={17} aria-hidden="true" />
        Agent 列表
      </div>
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : items.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 Agent" />
      ) : (
        <div className="flex max-h-[680px] flex-col gap-2 overflow-y-auto tablet-down:max-h-64">
          {items.map((item) => (
            <button
              key={item.agent.agent_id}
              type="button"
              className={`w-full cursor-pointer rounded-ui border border-solid p-3 text-left transition-colors ${
                selectedId === item.agent.agent_id
                  ? "border-brand-border bg-brand-soft"
                  : "border-border-soft bg-surface hover:border-brand-border"
              }`}
              aria-pressed={selectedId === item.agent.agent_id}
              onClick={() => onSelect(item.agent.agent_id)}
            >
              <span className="flex items-center justify-between gap-2">
                <strong className="min-w-0 truncate text-sm text-text-strong">
                  {item.agent.name}
                </strong>
                <Tag color={item.agent.status === "active" ? "success" : "default"}>
                  {item.agent.status === "active" ? "使用中" : "已归档"}
                </Tag>
              </span>
              <span className="mt-2 block truncate text-xs text-text-muted">
                {item.agent.agent_key} · 草稿 r{item.draft.revision}
              </span>
            </button>
          ))}
        </div>
      )}
    </aside>
  );
}
