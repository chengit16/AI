/** @description 审批待办、层级进度和人工动作组件。 */
import { Button, Empty, Input, Modal, Progress, Skeleton, Table, Tag } from "antd";
import { ArrowRightLeft, Check, RotateCcw, X } from "lucide-react";
import { useState } from "react";

import type { ApprovalInstance } from "@/api/services/workflows";

import { approvalStatus, formatWorkflowTime } from "../config";

type ApprovalAction = "approve" | "reject" | "transfer" | "withdraw";

/** 审批待办的参与者身份、权限和命令回调。 */
export interface ApprovalInboxProps {
  /** 当前账号作为申请人或历史审批人可见的实例。 */
  items: readonly ApprovalInstance[];
  /** 当前浏览器会话账号，用于裁剪责任相关按钮。 */
  accountId: string | null;
  /** 待办首次加载状态。 */
  isLoading: boolean;
  /** 各人工动作对应的体验权限集合。 */
  permissions: ReadonlySet<string>;
  /** 任一人工动作提交状态。 */
  isMutating: boolean;
  /** 提交通过、驳回或撤回。 */
  onAct: (instanceId: string, action: "approve" | "reject" | "withdraw", reason?: string) => void;
  /** 提交转交目标账号。 */
  onTransfer: (instanceId: string, targetAccountId: string) => void;
}

interface PendingCommand {
  instance: ApprovalInstance;
  action: ApprovalAction;
}

/** 展示参与者可见审批，并只向当前活动责任人提供对应动作。 */
export function ApprovalInbox(props: ApprovalInboxProps) {
  const [command, setCommand] = useState<PendingCommand | null>(null);
  const [value, setValue] = useState("");
  const has = (code: string) => props.permissions.has(code);
  const openCommand = (instance: ApprovalInstance, action: ApprovalAction) => {
    setValue("");
    setCommand({ instance, action });
  };
  const submitCommand = () => {
    if (!command) return;
    if (command.action === "transfer") {
      props.onTransfer(command.instance.approval_instance_id, value.trim());
    } else {
      props.onAct(
        command.instance.approval_instance_id,
        command.action,
        command.action === "reject" ? value.trim() : undefined,
      );
    }
    setCommand(null);
  };

  if (props.isLoading) return <Skeleton active paragraph={{ rows: 8 }} />;
  return (
    <>
      <div className="mb-4">
        <h2 className="m-0 text-lg text-text-strong">审批待办</h2>
        <p className="mb-0 mt-1 text-sm text-text-muted">
          仅展示当前账号发起或曾经承担审批责任的实例。
        </p>
      </div>
      {props.items.length === 0 ? (
        <Empty description="暂无审批记录" />
      ) : (
        <Table<ApprovalInstance>
          rowKey="approval_instance_id"
          dataSource={[...props.items]}
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          scroll={{ x: 960 }}
          columns={[
            {
              title: "状态",
              dataIndex: "status",
              width: 110,
              render: (status: ApprovalInstance["status"]) => (
                <Tag color={approvalStatus[status].color}>{approvalStatus[status].label}</Tag>
              ),
            },
            {
              title: "业务动作",
              key: "subject",
              width: 210,
              render: (_, item) => (
                <div>
                  <p className="m-0 text-sm font-650 text-text-strong">{item.resource_type}</p>
                  <p className="mb-0 mt-1 text-xs text-text-muted">{item.operation}</p>
                </div>
              ),
            },
            {
              title: "审批进度",
              key: "progress",
              width: 180,
              render: (_, item) => (
                <Progress
                  size="small"
                  percent={Math.round((item.current_sequence_no / item.levels.length) * 100)}
                  format={() => `${item.current_sequence_no}/${item.levels.length}`}
                />
              ),
            },
            {
              title: "更新时间",
              dataIndex: "updated_at",
              width: 150,
              render: formatWorkflowTime,
            },
            {
              title: "操作",
              key: "actions",
              fixed: "right",
              width: 330,
              render: (_, item) => {
                const assigned = item.assignments.some(
                  (assignment) =>
                    assignment.approver_account_id === props.accountId &&
                    assignment.status === "pending",
                );
                const requester = item.requester_account_id === props.accountId;
                return (
                  <div className="flex flex-wrap gap-1">
                    {item.status === "pending" && assigned && has("approval.instance.approve") && (
                      <Button
                        type="link"
                        icon={<Check size={15} />}
                        onClick={() => openCommand(item, "approve")}
                      >
                        通过
                      </Button>
                    )}
                    {item.status === "pending" && assigned && has("approval.instance.reject") && (
                      <Button
                        danger
                        type="link"
                        icon={<X size={15} />}
                        onClick={() => openCommand(item, "reject")}
                      >
                        驳回
                      </Button>
                    )}
                    {item.status === "pending" && assigned && has("approval.instance.transfer") && (
                      <Button
                        type="link"
                        icon={<ArrowRightLeft size={15} />}
                        onClick={() => openCommand(item, "transfer")}
                      >
                        转交
                      </Button>
                    )}
                    {item.status === "pending" &&
                      requester &&
                      has("approval.instance.withdraw") && (
                        <Button
                          type="link"
                          icon={<RotateCcw size={15} />}
                          onClick={() => openCommand(item, "withdraw")}
                        >
                          撤回
                        </Button>
                      )}
                    {!assigned && !(requester && item.status === "pending") && (
                      <span className="text-xs text-text-muted">只读</span>
                    )}
                  </div>
                );
              },
            },
          ]}
        />
      )}
      <Modal
        title={command ? actionTitle[command.action] : "审批动作"}
        open={Boolean(command)}
        okText="确认"
        cancelText="取消"
        confirmLoading={props.isMutating}
        okButtonProps={{
          danger: command?.action === "reject",
          disabled:
            (command?.action === "reject" || command?.action === "transfer") && !value.trim(),
        }}
        onOk={submitCommand}
        onCancel={() => setCommand(null)}
      >
        {command?.action === "reject" && (
          <Input
            value={value}
            placeholder="输入小写原因码，例如 policy_mismatch"
            maxLength={128}
            onChange={(event) => setValue(event.target.value)}
          />
        )}
        {command?.action === "transfer" && (
          <Input
            value={value}
            placeholder="输入目标成员账号 ID"
            onChange={(event) => setValue(event.target.value)}
          />
        )}
        {(command?.action === "approve" || command?.action === "withdraw") && (
          <p className="m-0 text-sm text-text-muted">服务端将再次校验当前责任、版本和审批状态。</p>
        )}
      </Modal>
    </>
  );
}

const actionTitle: Record<ApprovalAction, string> = {
  approve: "通过审批",
  reject: "驳回审批",
  transfer: "转交审批",
  withdraw: "撤回审批",
};
