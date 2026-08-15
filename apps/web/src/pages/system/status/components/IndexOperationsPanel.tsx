/** @description 索引维护请求、运行证据和受控命令面板。 */
import { Button, Form, Input, Modal, Table, Tag, Tooltip } from "antd";
import type { TableColumnsType } from "antd";
import { DatabaseZap, SearchCheck, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import type {
  IndexCommand,
  IndexMaintenanceRequest,
  IndexMaintenanceRun,
} from "@/api/services/operations";
import { StateView } from "@/components/StateView/StateView";

import { formatOperationsTime, indexCommandPresentation, presentStatus } from "../config";
import type { IndexCommandValues } from "../useOperationsWorkbench";

interface IndexOperationsPanelProps {
  /** 当前空间的人工维护请求，不包含内部租约字段。 */
  requests: readonly IndexMaintenanceRequest[];
  /** 已完成维护的计数和结果摘要。 */
  runs: readonly IndexMaintenanceRun[];
  /** 请求或运行证据首次加载时的统一状态。 */
  isLoading: boolean;
  /** 防止危险命令表单在服务端处理期间重复提交。 */
  isSubmitting: boolean;
  /** 动态菜单发布快照提供的交互权限，后端仍会独立授权。 */
  permissions: ReadonlySet<string>;
  /** 提交包含原因码和固定确认词的完整维护命令。 */
  onSubmit: (values: IndexCommandValues) => Promise<unknown>;
}

interface CommandFormValues {
  reasonCode: string;
  confirmation: string;
}

const commandPermission: Record<IndexCommand, string> = {
  inspection: "operations.index.inspect",
  full_rebuild: "operations.index.rebuild",
  cleanup: "operations.index.cleanup",
};

/** 展示命令状态与完成证据，并用服务端约定确认词保护三类维护操作。 */
export function IndexOperationsPanel(props: IndexOperationsPanelProps) {
  const [command, setCommand] = useState<IndexCommand | null>(null);
  const [form] = Form.useForm<CommandFormValues>();
  const selected = command ? indexCommandPresentation[command] : null;
  const submit = async () => {
    if (!command) return;
    const values = await form.validateFields();
    await props.onSubmit({ command, ...values });
    setCommand(null);
    form.resetFields();
  };
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-5">
      <div className="flex flex-wrap items-center gap-2">
        <CommandButton
          command="inspection"
          icon={<SearchCheck size={16} />}
          permissions={props.permissions}
          onSelect={setCommand}
        />
        <CommandButton
          command="full_rebuild"
          icon={<DatabaseZap size={16} />}
          permissions={props.permissions}
          onSelect={setCommand}
        />
        <CommandButton
          command="cleanup"
          icon={<Trash2 size={16} />}
          permissions={props.permissions}
          onSelect={setCommand}
          danger
        />
      </div>
      <section>
        <h3 className="mb-3 mt-0 text-[15px]">人工维护请求</h3>
        <Table<IndexMaintenanceRequest>
          rowKey="maintenance_request_id"
          columns={requestColumns}
          dataSource={[...props.requests]}
          loading={props.isLoading}
          pagination={false}
          scroll={{ x: 820 }}
          locale={{
            emptyText: (
              <StateView
                kind="empty"
                title="暂无维护请求"
                description="执行巡检、重建或清理后会显示 Worker 处理状态。"
              />
            ),
          }}
        />
      </section>
      <section>
        <h3 className="mb-3 mt-0 text-[15px]">完成证据</h3>
        <Table<IndexMaintenanceRun>
          rowKey="maintenance_run_id"
          columns={runColumns}
          dataSource={[...props.runs]}
          loading={props.isLoading}
          pagination={false}
          scroll={{ x: 900 }}
          locale={{
            emptyText: (
              <StateView
                kind="empty"
                title="暂无完成记录"
                description="维护执行完成后会保留计数和可复算摘要。"
              />
            ),
          }}
        />
      </section>
      <Modal
        title={selected?.label ?? "索引维护"}
        open={Boolean(command)}
        okText="登记请求"
        cancelText="取消"
        confirmLoading={props.isSubmitting}
        destroyOnHidden
        onCancel={() => {
          setCommand(null);
          form.resetFields();
        }}
        onOk={() => void submit()}
      >
        {selected && (
          <>
            <p className="mt-0 leading-7 text-text-muted">{selected.description}</p>
            <Form form={form} layout="vertical">
              <Form.Item
                name="reasonCode"
                label="原因码"
                initialValue="OPERATOR_REQUEST"
                rules={[{ required: true }, { pattern: /^[A-Z][A-Z0-9_]{2,63}$/ }]}
              >
                <Input maxLength={64} />
              </Form.Item>
              <Form.Item
                name="confirmation"
                label={`输入 ${selected.confirmation} 确认`}
                rules={[
                  { required: true },
                  {
                    validator: (_, value) =>
                      value === selected.confirmation
                        ? Promise.resolve()
                        : Promise.reject(new Error("确认文本不匹配")),
                  },
                ]}
              >
                <Input autoComplete="off" />
              </Form.Item>
            </Form>
          </>
        )}
      </Modal>
    </div>
  );
}

interface CommandButtonProps {
  /** 决定按钮文案、固定确认词和后端路由的维护命令。 */
  command: IndexCommand;
  /** 表达维护动作语义的图标。 */
  icon: ReactNode;
  /** 控制按钮体验可见性，不能替代服务端权限判断。 */
  permissions: ReadonlySet<string>;
  /** 选中命令后打开对应确认表单。 */
  onSelect: (command: IndexCommand) => void;
  /** 标识会删除不可恢复派生数据的高风险动作。 */
  danger?: boolean;
}

function CommandButton(props: CommandButtonProps) {
  const item = indexCommandPresentation[props.command];
  if (!props.permissions.has(commandPermission[props.command])) return null;
  return (
    <Tooltip title={item.description}>
      <Button icon={props.icon} danger={props.danger} onClick={() => props.onSelect(props.command)}>
        {item.label}
      </Button>
    </Tooltip>
  );
}

const requestColumns: TableColumnsType<IndexMaintenanceRequest> = [
  {
    title: "命令",
    dataIndex: "command",
    render: (value: IndexCommand) => indexCommandPresentation[value].label,
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
  { title: "尝试", dataIndex: "attempt_count", width: 80 },
  {
    title: "结果 / 失败",
    key: "result",
    render: (_, item) =>
      item.last_error_code ?? (item.status === "completed" ? "已生成运行证据" : "--"),
  },
  { title: "创建时间", dataIndex: "created_at", width: 150, render: formatOperationsTime },
];

const runColumns: TableColumnsType<IndexMaintenanceRun> = [
  {
    title: "类型",
    dataIndex: "run_kind",
    render: (value: IndexCommand) => indexCommandPresentation[value].label,
  },
  { title: "扫描文档", dataIndex: "scanned_document_count", width: 100 },
  { title: "差异", dataIndex: "inconsistency_count", width: 80 },
  { title: "修复", dataIndex: "repaired_count", width: 80 },
  { title: "排队重建", dataIndex: "rebuild_queued_count", width: 100 },
  { title: "清理 Chunk", dataIndex: "cleaned_chunk_count", width: 110 },
  {
    title: "摘要",
    dataIndex: "result_digest",
    width: 150,
    render: (value: string) => <code>{value.slice(0, 12)}</code>,
  },
  { title: "完成时间", dataIndex: "completed_at", width: 150, render: formatOperationsTime },
];
