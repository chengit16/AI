/** @description Agent 不可变 Release 历史表格。 */
import { Empty, Skeleton, Table, Tag, Typography } from "antd";

import type { AgentRelease } from "@/api/services/agents";

import { formatAgentTime } from "../config";

/** Release 历史数据与首次加载状态。 */
export interface AgentReleaseListProps {
  /** 当前 Agent 经后端授权的不可变 Release 摘要。 */
  items: readonly AgentRelease[];
  /** Release 查询首次加载状态。 */
  isLoading: boolean;
}

/** 展示只读 Release 身份、来源 revision 和快照摘要，不暴露 Prompt 或正文。 */
export function AgentReleaseList({ items, isLoading }: AgentReleaseListProps) {
  if (isLoading) return <Skeleton active paragraph={{ rows: 9 }} />;
  return (
    <section aria-labelledby="agent-release-title">
      <div className="mb-4">
        <h2 id="agent-release-title" className="m-0 text-xl text-text-strong">
          Release 历史
        </h2>
        <p className="mb-0 mt-2 text-sm text-text-muted">
          每个 Release 绑定测试和审批证据，创建后不能原地修改。
        </p>
      </div>
      {items.length === 0 ? (
        <Empty description="还没有已发布 Release" />
      ) : (
        <Table<AgentRelease>
          rowKey="release_id"
          dataSource={[...items]}
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          scroll={{ x: 820 }}
          columns={[
            {
              title: "版本",
              dataIndex: "version",
              width: 100,
              render: (version: number) => <Tag color="success">v{version}</Tag>,
            },
            {
              title: "Release ID",
              dataIndex: "release_id",
              width: 280,
              render: (value: string) => <Typography.Text copyable>{value}</Typography.Text>,
            },
            {
              title: "来源草稿",
              dataIndex: "source_draft_revision",
              width: 120,
              render: (value: number | null) => (value ? `r${value}` : "系统版本"),
            },
            {
              title: "快照摘要",
              dataIndex: "snapshot_hash",
              width: 180,
              render: (value: string | null) => value?.slice(0, 16) || "-",
            },
            {
              title: "发布时间",
              dataIndex: "released_at",
              width: 150,
              render: formatAgentTime,
            },
          ]}
        />
      )}
    </section>
  );
}
