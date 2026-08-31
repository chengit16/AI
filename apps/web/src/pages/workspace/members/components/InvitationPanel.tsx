/** @description 成员邀请创建、复制与撤销面板。 */
import { Button, Form, Input, Modal, Popconfirm, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { Link2, UserRoundPlus } from "lucide-react";
import { useState } from "react";

import type { TeamInvitation } from "@/api/services/teamManagement";

import { formatTeamTime, invitationStatusLabel } from "../teamUtils";

interface InvitationPanelProps {
  /** 服务端按当前时间计算过期状态后的邀请集合。 */
  invitations: readonly TeamInvitation[];
  /** 邀请创建或撤销是否正在执行。 */
  pending: boolean;
  /** 创建目标登录名邀请。 */
  onInvite: (loginName: string) => void;
  /** 撤销指定有效邀请。 */
  onCancel: (invitationId: string) => void;
}

function invitationLink(invitationId: string) {
  return `${window.location.origin}/workspace?invitation_id=${invitationId}`;
}

/** 展示完整邀请生命周期，并提供真实可复制的邀请直达链接。 */
export function InvitationPanel({
  invitations,
  pending,
  onInvite,
  onCancel,
}: InvitationPanelProps) {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<{ loginName: string }>();
  const columns: TableColumnsType<TeamInvitation> = [
    {
      title: "邀请对象",
      key: "invitee",
      render: (_, record) => (
        <div>
          <strong className="block text-text-strong">
            {record.invited_display_name ?? "受字段权限保护"}
          </strong>
          <span className="mt-1 block text-xs text-text-muted">
            {record.invited_login_name ?? "登录名不可见"}
          </span>
        </div>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (value: TeamInvitation["status"]) => (
        <Tag
          color={value === "pending" ? "processing" : value === "accepted" ? "success" : "default"}
        >
          {invitationStatusLabel(value)}
        </Tag>
      ),
    },
    {
      title: "有效期至",
      dataIndex: "expires_at",
      width: 170,
      render: (value: string) => formatTeamTime(value),
    },
    {
      title: "操作",
      key: "actions",
      width: 170,
      render: (_, record) => (
        <div className="flex items-center gap-1">
          <Typography.Text copyable={{ text: invitationLink(record.invitation_id) }}>
            <Button type="link" icon={<Link2 size={15} />}>
              复制链接
            </Button>
          </Typography.Text>
          {record.status === "pending" && (
            <Popconfirm
              title="撤销这条邀请？"
              description="成员将无法再使用该邀请加入。"
              okText="撤销"
              cancelText="取消"
              onConfirm={() => onCancel(record.invitation_id)}
            >
              <Button type="link" danger>
                撤销
              </Button>
            </Popconfirm>
          )}
        </div>
      ),
    },
  ];
  return (
    <>
      <section className="ui-surface-panel overflow-hidden" aria-labelledby="invitation-title">
        <div className="flex items-center justify-between gap-4 border-b border-b-solid border-border px-6 py-5 phone-down:flex-col phone-down:items-stretch">
          <div>
            <h2 id="invitation-title" className="m-0 text-[17px] text-text-strong">
              邀请治理
            </h2>
            <p className="mb-0 mt-1 text-xs text-text-muted">
              邀请仅对已注册目标账号有效，链接不会扩大接受权限。
            </p>
          </div>
          <Button type="primary" icon={<UserRoundPlus size={16} />} onClick={() => setOpen(true)}>
            邀请成员
          </Button>
        </div>
        <Table<TeamInvitation>
          rowKey="invitation_id"
          columns={columns}
          dataSource={[...invitations]}
          pagination={{ pageSize: 5, hideOnSinglePage: true }}
          scroll={{ x: 760 }}
          locale={{ emptyText: "暂无邀请记录" }}
        />
      </section>
      <Modal
        title="邀请成员"
        open={open}
        okText="创建邀请"
        cancelText="取消"
        confirmLoading={pending}
        onCancel={() => setOpen(false)}
        onOk={() =>
          void form.validateFields().then((values) => {
            onInvite(values.loginName);
            setOpen(false);
            form.resetFields();
          })
        }
      >
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item
            label="已注册账号登录名"
            name="loginName"
            rules={[
              { required: true, message: "请输入已注册账号登录名" },
              { min: 3 },
              { max: 255 },
            ]}
          >
            <Input autoFocus placeholder="member@example.com" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
