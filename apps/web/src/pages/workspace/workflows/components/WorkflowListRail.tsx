/** @description 工作流定义列表、发布状态和选择交互组件。 */
import { Badge, Button, Empty, Skeleton } from "antd";
import { Plus } from "lucide-react";

import type { WorkflowDefinition } from "@/api/services/workflows";
import { cn } from "@/utils/cn";

/** 工作流列表栏的服务端事实和权限化操作。 */
export interface WorkflowListRailProps {
  /** 当前授权范围内的工作流定义。 */
  items: readonly WorkflowDefinition[];
  /** URL 中当前选中的工作流 ID。 */
  selectedId: string | null;
  /** 列表首次加载状态。 */
  isLoading: boolean;
  /** 是否展示创建入口；接口仍由后端独立拒绝。 */
  canCreate: boolean;
  /** 打开创建工作流对话框。 */
  onCreate: () => void;
  /** 切换当前设计和运行上下文。 */
  onSelect: (workflowId: string) => void;
}

/** 展示密集工作流导航，并以原生按钮保证键盘选择可达。 */
export function WorkflowListRail(props: WorkflowListRailProps) {
  return (
    <aside className="border-0 border-r border-solid border-border-soft bg-surface-subtle p-4 tablet-down:border-b tablet-down:border-r-0">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <p className="m-0 text-sm font-750 text-text-strong">工作流定义</p>
          <p className="mb-0 mt-1 text-xs text-text-muted">{props.items.length} 项</p>
        </div>
        {props.canCreate && (
          <Button
            type="text"
            icon={<Plus size={17} />}
            aria-label="创建工作流"
            title="创建工作流"
            onClick={props.onCreate}
          />
        )}
      </div>
      {props.isLoading ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : props.items.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无工作流" />
      ) : (
        <nav className="flex max-h-[640px] flex-col gap-2 overflow-y-auto" aria-label="工作流列表">
          {props.items.map((workflow) => (
            <button
              key={workflow.workflow_id}
              type="button"
              className={cn(
                "min-h-[64px] w-full cursor-pointer rounded-ui border border-solid px-3 py-2 text-left transition-colors",
                props.selectedId === workflow.workflow_id
                  ? "border-brand-border bg-brand-soft"
                  : "border-transparent bg-transparent hover:border-border hover:bg-surface",
              )}
              aria-current={props.selectedId === workflow.workflow_id ? "page" : undefined}
              onClick={() => props.onSelect(workflow.workflow_id)}
            >
              <span className="block truncate text-sm font-700 text-text-strong">
                {workflow.name}
              </span>
              <span className="mt-2 flex items-center gap-2 text-xs text-text-muted">
                <Badge status={workflow.current_version_id ? "success" : "default"} />
                {workflow.current_version_id ? "已发布" : "仅草稿"}
              </span>
            </button>
          ))}
        </nav>
      )}
    </aside>
  );
}
