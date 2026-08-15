/** @description Outbox 巡检、事件元数据和受控重放面板。 */
import { Button, Form, Input, Modal, Table, Tag } from "antd";
import type { TableColumnsType } from "antd";
import { RotateCcw } from "lucide-react";
import { useState } from "react";

import type { IntegrationInspection, OutboxEvent } from "@/api/services/operations";
import { StateView } from "@/components/StateView/StateView";

import { formatOperationsTime, presentStatus } from "../config";

interface OutboxOperationsPanelProps {
  /** 不包含事件 Payload 的当前空间 Outbox 元数据。 */
  items: readonly OutboxEvent[];
  /** 积压、Schema 和消费者回执的一致性巡检摘要。 */
  inspection: IntegrationInspection | undefined;
  /** 事件列表与巡检摘要首次加载时的统一状态。 */
  isLoading: boolean;
  /** 是否展示受控重放入口；服务端仍执行独立授权。 */
  canReplay: boolean;
  /** 重放请求登记期间防止重复提交。 */
  isReplaying: boolean;
  /** 使用原事件 ID 和结构化原因码登记重放请求。 */
  onReplay: (values: { eventId: string; reasonCode: string }) => Promise<unknown>;
}

/** 只展示事件元数据；重放保留原事件 ID，Payload 从不进入浏览器响应。 */
export function OutboxOperationsPanel(props: OutboxOperationsPanelProps) {
  const [event, setEvent] = useState<OutboxEvent | null>(null);
  const [form] = Form.useForm<{ reasonCode: string }>();
  const submit = async () => {
    if (!event) return;
    const values = await form.validateFields();
    await props.onReplay({ eventId: event.event_id, reasonCode: values.reasonCode });
    setEvent(null);
    form.resetFields();
  };
  const columns: TableColumnsType<OutboxEvent> = [
    { title: "事件类型", dataIndex: "event_type", ellipsis: true },
    {
      title: "状态",
      dataIndex: "status",
      width: 105,
      render: (value: string) => {
        const status = presentStatus(value);
        return <Tag color={status.color}>{status.label}</Tag>;
      },
    },
    { title: "投递尝试", dataIndex: "attempt_count", width: 95 },
    { title: "人工重放", dataIndex: "replay_count", width: 95 },
    {
      title: "失败码",
      dataIndex: "last_error_code",
      width: 180,
      render: (value: string | null) => value ?? "--",
    },
    { title: "发生时间", dataIndex: "occurred_at", width: 150, render: formatOperationsTime },
    {
      title: "操作",
      key: "actions",
      width: 90,
      render: (_, item) =>
        props.canReplay && ["published", "dead_letter"].includes(item.status) ? (
          <Button type="text" icon={<RotateCcw size={15} />} onClick={() => setEvent(item)}>
            重放
          </Button>
        ) : null,
    },
  ];
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-5">
      <InspectionBand inspection={props.inspection} />
      <Table<OutboxEvent>
        rowKey="event_id"
        columns={columns}
        dataSource={[...props.items]}
        loading={props.isLoading}
        pagination={false}
        scroll={{ x: 920 }}
        locale={{
          emptyText: (
            <StateView
              kind="empty"
              title="暂无 Outbox 事件"
              description="业务变更会在这里留下发布事实。"
            />
          ),
        }}
      />
      <Modal
        title="重放 Outbox 事件"
        open={Boolean(event)}
        okText="确认重放"
        cancelText="取消"
        confirmLoading={props.isReplaying}
        destroyOnHidden
        onCancel={() => {
          setEvent(null);
          form.resetFields();
        }}
        onOk={() => void submit()}
      >
        <p className="mt-0 leading-7 text-text-muted">
          事件将保留原 ID 重新排队，已处理消费者会继续按回执判重。
        </p>
        <Form form={form} layout="vertical">
          <Form.Item
            name="reasonCode"
            label="重放原因码"
            initialValue="OPERATOR_REPLAY"
            rules={[{ required: true }, { pattern: /^[A-Z][A-Z0-9_]{2,63}$/ }]}
          >
            <Input maxLength={64} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

function InspectionBand({ inspection }: { inspection: IntegrationInspection | undefined }) {
  const items = [
    ["最老积压", `${Math.round(inspection?.oldest_pending_age_seconds ?? 0)} 秒`],
    ["过期租约", inspection?.expired_claim_count ?? 0],
    ["不兼容 Schema", inspection?.incompatible_schema_count ?? 0],
    ["幂等异常", inspection?.idempotency_issue_count ?? 0],
  ];
  return (
    <div className="grid grid-cols-4 divide-x divide-solid divide-border-soft rounded-ui border border-solid border-border-soft bg-surface-subtle nav-mobile:grid-cols-2 nav-mobile:divide-y">
      {items.map(([label, value]) => (
        <span className="px-4 py-3" key={label}>
          <small className="block text-text-muted">{label}</small>
          <strong className="mt-1 block">{value}</strong>
        </span>
      ))}
    </div>
  );
}
