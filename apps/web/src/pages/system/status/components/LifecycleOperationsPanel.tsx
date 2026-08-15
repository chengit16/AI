/** @description 工作空间导出、清除和保留期执行面板。 */
import { App, Button, Form, Input, Modal, Table, Tag } from "antd";
import type { TableColumnsType } from "antd";
import { Archive, Eraser, TimerReset } from "lucide-react";
import { useState } from "react";

import type { LifecycleOperation } from "@/api/services/operations";
import { StateView } from "@/components/StateView/StateView";

import { formatOperationsTime, presentStatus } from "../config";

interface LifecycleOperationsPanelProps {
  /** 清除业务数据时必须由操作者完整复述的空间名称。 */
  workspaceName: string;
  /** 三类生命周期操作按时间合并后的结果历史。 */
  items: readonly LifecycleOperation[];
  /** 生命周期历史首次加载时的表格状态。 */
  isLoading: boolean;
  /** 控制三类命令入口可见性，服务端仍执行独立权限检查。 */
  permissions: ReadonlySet<string>;
  /** 任一生命周期命令运行期间阻止重复提交。 */
  isMutating: boolean;
  /** 生成当前空间的确定性可校验导出包。 */
  onExport: () => void;
  /** 按冻结边界执行当前空间保留期策略。 */
  onRetention: () => void;
  /** 携带空间名称和原因码请求清除业务数据。 */
  onPurge: (values: { workspaceName: string; reasonCode: string }) => Promise<unknown>;
}

/** 将三类生命周期命令与历史放在同一处，清除操作必须复述完整空间名称。 */
export function LifecycleOperationsPanel(props: LifecycleOperationsPanelProps) {
  const { modal } = App.useApp();
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [form] = Form.useForm<{ workspaceName: string; reasonCode: string }>();
  const confirmExport = () =>
    modal.confirm({
      title: "导出当前工作空间？",
      content: "导出包会写入本地对象存储，并返回内容和对象清单摘要。",
      okText: "开始导出",
      cancelText: "取消",
      onOk: props.onExport,
    });
  const confirmRetention = () =>
    modal.confirm({
      title: "执行保留期策略？",
      content: "系统会按冻结截止时间删除到期运行数据，审计和治理保留项不受影响。",
      okText: "确认执行",
      cancelText: "取消",
      onOk: props.onRetention,
    });
  const submitPurge = async () => {
    const values = await form.validateFields();
    await props.onPurge(values);
    setPurgeOpen(false);
    form.resetFields();
  };
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-5">
      <div className="flex flex-wrap gap-2">
        {props.permissions.has("workspace.lifecycle.export") && (
          <Button icon={<Archive size={16} />} onClick={confirmExport}>
            导出空间
          </Button>
        )}
        {props.permissions.has("workspace.lifecycle.retention.execute") && (
          <Button icon={<TimerReset size={16} />} onClick={confirmRetention}>
            执行保留策略
          </Button>
        )}
        {props.permissions.has("workspace.lifecycle.purge") && (
          <Button danger icon={<Eraser size={16} />} onClick={() => setPurgeOpen(true)}>
            清除业务数据
          </Button>
        )}
      </div>
      <Table<LifecycleOperation>
        rowKey="operation_id"
        columns={columns}
        dataSource={[...props.items]}
        loading={props.isLoading}
        pagination={false}
        scroll={{ x: 760 }}
        locale={{
          emptyText: (
            <StateView
              kind="empty"
              title="暂无生命周期运行"
              description="导出、清除或保留期执行后会保留结果摘要。"
            />
          ),
        }}
      />
      <Modal
        title="清除工作空间业务数据"
        open={purgeOpen}
        okText="确认清除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        confirmLoading={props.isMutating}
        destroyOnHidden
        onCancel={() => {
          setPurgeOpen(false);
          form.resetFields();
        }}
        onOk={() => void submitPurge()}
      >
        <p className="mt-0 leading-7 text-danger-text">
          该操作会清除业务事实、对象、索引和缓存，仅保留可登录治理壳层与最小审计证明。
        </p>
        <Form form={form} layout="vertical">
          <Form.Item
            name="workspaceName"
            label={`输入空间名称 ${props.workspaceName} 确认`}
            rules={[
              { required: true },
              {
                validator: (_, value) =>
                  value === props.workspaceName
                    ? Promise.resolve()
                    : Promise.reject(new Error("空间名称不匹配")),
              },
            ]}
          >
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="reasonCode"
            label="清除原因码"
            initialValue="OPERATOR_PURGE"
            rules={[{ required: true }, { pattern: /^[A-Z][A-Z0-9_]{2,63}$/ }]}
          >
            <Input maxLength={64} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

const operationLabels = { export: "空间导出", purge: "业务清除", retention: "保留期执行" };
const columns: TableColumnsType<LifecycleOperation> = [
  {
    title: "操作",
    dataIndex: "operation_kind",
    render: (value: LifecycleOperation["operation_kind"]) => operationLabels[value],
  },
  {
    title: "状态",
    dataIndex: "status",
    width: 110,
    render: (value: string) => {
      const status = presentStatus(value);
      return <Tag color={status.color}>{status.label}</Tag>;
    },
  },
  { title: "结果数量", dataIndex: "result_count", width: 110, render: (value) => value ?? "--" },
  { title: "失败码", dataIndex: "error_code", width: 180, render: (value) => value ?? "--" },
  { title: "创建时间", dataIndex: "created_at", width: 150, render: formatOperationsTime },
  { title: "完成时间", dataIndex: "completed_at", width: 150, render: formatOperationsTime },
];
