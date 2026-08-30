/** @description 私有问答会话导航栏，保持列表操作轻量且适配窄屏。 */
import { Button, Empty, Popconfirm, Segmented, Skeleton, Typography } from "antd";
import { Archive, MessageSquarePlus } from "lucide-react";

import type { AssistantConversation } from "@/api/services/assistant";
import { cn } from "@/utils/cn";

/** 渲染当前账号私有会话列表和新建入口，不承担服务端权限判断。 */
export function ConversationRail({
  items,
  selectedId,
  loading,
  creating,
  filter,
  canArchive,
  archiving,
  onSelect,
  onFilter,
  onCreate,
  onArchive,
}: {
  items: ReadonlyArray<AssistantConversation>;
  selectedId: string | null;
  loading: boolean;
  creating: boolean;
  filter: "active" | "archived";
  canArchive: boolean;
  archiving: boolean;
  onSelect: (conversationId: string) => void;
  onFilter: (status: "active" | "archived") => void;
  onCreate: () => void;
  onArchive: (conversationId: string) => void;
}) {
  const filteredItems = items.filter((item) => item.status === filter);
  return (
    <aside className="ui-surface-panel flex min-h-[620px] flex-col overflow-hidden tablet-down:min-h-0">
      <div className="flex items-center justify-between border-b border-b-solid border-border px-4 py-4">
        <div>
          <Typography.Title level={5} className="!mb-0">
            问答会话
          </Typography.Title>
          <Typography.Text type="secondary" className="text-xs">
            仅显示当前账号创建的私有会话
          </Typography.Text>
        </div>
        <Button
          type="primary"
          aria-label="新建问答会话"
          icon={<MessageSquarePlus size={17} />}
          loading={creating}
          onClick={onCreate}
        />
      </div>
      <div className="border-b border-b-solid border-border px-3 py-2">
        <Segmented
          block
          value={filter}
          options={[
            { label: "活动", value: "active" },
            { label: "已归档", value: "archived" },
          ]}
          onChange={(value) => onFilter(value as "active" | "archived")}
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {loading ? (
          <div className="space-y-3 p-3">
            <Skeleton active paragraph={{ rows: 2 }} />
            <Skeleton active paragraph={{ rows: 2 }} />
          </div>
        ) : filteredItems.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={filter === "active" ? "还没有活动会话" : "还没有归档会话"}
          />
        ) : (
          <div>
            {filteredItems.map((item) => (
              <div key={item.conversation_id}>
                <div
                  className={cn(
                    "mb-1 flex w-full items-center gap-1 px-3 py-2 transition-colors",
                    "hover:bg-brand-soft",
                    selectedId === item.conversation_id &&
                      "bg-brand-soft shadow-[inset_3px_0_0_var(--color-brand)]",
                  )}
                >
                  <button
                    type="button"
                    className="min-w-0 flex-1 border-0 bg-transparent py-1 text-left"
                    onClick={() => onSelect(item.conversation_id)}
                  >
                    <span className="block truncate text-sm font-600 text-text">
                      {item.title || "未命名问答"}
                    </span>
                    <span className="mt-1 block text-xs text-text-muted">
                      {new Date(item.updated_at).toLocaleString("zh-CN", {
                        month: "numeric",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </span>
                  </button>
                  {filter === "active" && canArchive && (
                    <Popconfirm
                      title="归档这个会话？"
                      description="归档后仍可查看历史，临时附件会被清理。"
                      okText="归档"
                      cancelText="取消"
                      onConfirm={() => onArchive(item.conversation_id)}
                    >
                      <Button
                        type="text"
                        title="归档会话"
                        aria-label={`归档会话 ${item.title || "未命名问答"}`}
                        icon={<Archive size={15} />}
                        loading={archiving && selectedId === item.conversation_id}
                      />
                    </Popconfirm>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
