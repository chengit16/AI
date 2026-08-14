/** @description 私有问答会话导航栏，保持列表操作轻量且适配窄屏。 */
import { Button, Empty, List, Skeleton, Typography } from "antd";
import { MessageSquarePlus } from "lucide-react";

import type { AssistantConversation } from "@/api/services/assistant";
import { cn } from "@/utils/cn";

/** 渲染当前账号私有会话列表和新建入口，不承担服务端权限判断。 */
export function ConversationRail({
  items,
  selectedId,
  loading,
  creating,
  onSelect,
  onCreate,
}: {
  items: ReadonlyArray<AssistantConversation>;
  selectedId: string | null;
  loading: boolean;
  creating: boolean;
  onSelect: (conversationId: string) => void;
  onCreate: () => void;
}) {
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
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {loading ? (
          <div className="space-y-3 p-3">
            <Skeleton active paragraph={{ rows: 2 }} />
            <Skeleton active paragraph={{ rows: 2 }} />
          </div>
        ) : items.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有问答会话" />
        ) : (
          <List
            split={false}
            dataSource={[...items]}
            renderItem={(item) => (
              <List.Item className="!p-0">
                <button
                  type="button"
                  className={cn(
                    "mb-1 w-full border-0 bg-transparent px-3 py-3 text-left transition-colors",
                    "hover:bg-brand-soft",
                    selectedId === item.conversation_id &&
                      "bg-brand-soft shadow-[inset_3px_0_0_var(--color-brand)]",
                  )}
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
              </List.Item>
            )}
          />
        )}
      </div>
    </aside>
  );
}
