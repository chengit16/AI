/** @description 助手来源 Drawer，展示服务端重新授权后的引用事实。 */
import { Alert, Descriptions, Drawer, Empty, Skeleton, Tag, Typography } from "antd";

import type { AssistantSource } from "@/api/services/assistant";

/** 展示服务端重新授权后的来源，撤权或版本漂移统一显示不可查看状态。 */
export function AssistantSourcesDrawer({
  open,
  loading,
  error,
  items,
  onClose,
}: {
  open: boolean;
  loading: boolean;
  error: unknown;
  items: ReadonlyArray<AssistantSource>;
  onClose: () => void;
}) {
  return (
    <Drawer title="回答来源" open={open} onClose={onClose} size="default">
      {loading ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : error ? (
        <Alert
          type="warning"
          showIcon
          message="来源当前不可查看"
          description="文档权限、版本或索引状态可能已经发生变化。"
        />
      ) : items.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="这条回答没有可展示的来源" />
      ) : (
        <div className="space-y-4">
          {items.map((item) => (
            <section
              key={`${item.chunk_id}-${item.rank}`}
              className="border-b border-b-solid border-border pb-4"
            >
              <div className="mb-2 flex items-start justify-between gap-3">
                <Typography.Text strong>{item.document_title}</Typography.Text>
                <Tag>#{item.rank}</Tag>
              </div>
              <Typography.Paragraph className="!mb-2 whitespace-pre-wrap text-sm leading-6">
                “{item.quote}”
              </Typography.Paragraph>
              <Descriptions
                size="small"
                column={1}
                items={[
                  {
                    key: "source",
                    label: "来源",
                    children: `${item.source_name} · ${item.source_kind}`,
                  },
                  { key: "hash", label: "版本摘要", children: item.content_hash.slice(0, 12) },
                ]}
              />
              {item.conflict_detected && <Tag color="warning">存在来源冲突</Tag>}
            </section>
          ))}
        </div>
      )}
    </Drawer>
  );
}
