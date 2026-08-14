/** @description 知识库选择侧栏与受权限控制的创建入口。 */
import { Button, Skeleton } from "antd";
import { BookOpen, Plus } from "lucide-react";

import { StateView } from "@/components/StateView/StateView";
import type { KnowledgeBaseSummary } from "@/api/services/knowledge";
import { cn } from "@/utils/cn";

import { securityLevelLabels } from "../config";

interface KnowledgeBaseRailProps {
  /** 当前空间可见的知识库摘要。 */
  items: readonly KnowledgeBaseSummary[];
  /** 当前页面选中的知识库 ID。 */
  selectedId: string | null;
  /** 知识库清单是否正在首次加载。 */
  isLoading: boolean;
  /** 只控制创建入口展示，后端仍独立授权。 */
  canCreate: boolean;
  /** 切换当前页面知识库上下文。 */
  onSelect: (knowledgeBaseId: string) => void;
  /** 打开知识库创建流程。 */
  onCreate: () => void;
}

/**
 * 提供知识库选择与创建入口。
 *
 * `canCreate` 仅影响按钮展示，创建接口仍由后端权限策略独立授权。
 */
export function KnowledgeBaseRail({
  items,
  selectedId,
  isLoading,
  canCreate,
  onSelect,
  onCreate,
}: KnowledgeBaseRailProps) {
  return (
    <aside
      className="min-w-0 border-r border-r-solid border-border bg-surface-subtle p-4 tablet-down:border-b tablet-down:border-b-solid tablet-down:border-r-0"
      aria-label="知识库列表"
    >
      <div className="mb-3 flex min-h-11 items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <strong>知识库</strong>
          <span className="text-xs text-text-muted">{items.length} 个</span>
        </div>
        {canCreate && (
          <Button
            type="text"
            aria-label="创建知识库"
            icon={<Plus size={17} />}
            onClick={onCreate}
          />
        )}
      </div>
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 6 }} title={false} />
      ) : items.length === 0 ? (
        <StateView
          kind="empty"
          title="还没有知识库"
          description="创建知识库后即可上传和管理文档。"
        />
      ) : (
        <div className="grid gap-1 tablet-down:flex tablet-down:overflow-x-auto">
          {items.map((item) => (
            <button
              key={item.knowledge_base_id}
              type="button"
              className={cn(
                "flex min-h-[58px] w-full cursor-pointer items-center gap-3 rounded-ui border border-solid p-3 text-left transition-colors tablet-down:w-[210px] tablet-down:flex-[0_0_210px]",
                selectedId === item.knowledge_base_id
                  ? "border-brand-border bg-brand-soft text-brand hover:border-brand-border hover:bg-brand-soft"
                  : "border-transparent bg-transparent text-text hover:border-border-soft hover:bg-surface",
              )}
              onClick={() => onSelect(item.knowledge_base_id)}
            >
              <BookOpen size={17} aria-hidden="true" />
              <span className="grid min-w-0 gap-[3px]">
                <strong className="overflow-hidden text-ellipsis whitespace-nowrap">
                  {item.name}
                </strong>
                <small className="overflow-hidden text-ellipsis whitespace-nowrap text-[11px] text-text-muted">
                  {securityLevelLabels[item.default_security_level]}
                </small>
              </span>
            </button>
          ))}
        </div>
      )}
    </aside>
  );
}
