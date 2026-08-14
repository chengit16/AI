/** 企业成员治理页，处理邀请创建、成员状态展示和即时停用入口。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App, Button, Form, Input, Modal, Popconfirm, Skeleton, Table, Tag } from "antd";
import type { TableColumnsType } from "antd";
import { UserMinus, UserPlus } from "lucide-react";
import { useState } from "react";

import { errorMessage, PlatformApiError } from "@/api/client";
import {
  disableWorkspaceMember,
  getWorkspaceMembers,
  inviteWorkspaceMember,
  type WorkspaceMember,
} from "@/api/services/workspaces";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";

/**
 * 展示企业成员清单和治理动作。
 *
 * 个人空间不会请求企业成员接口；无权限与请求错误分开呈现，页面按钮隐藏不替代后端授权。
 */
export default function WorkspaceMembersPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { workspaceId, workspaces, currentWorkspace } = useCurrentWorkspace();
  const [inviteOpen, setInviteOpen] = useState(false);
  const [form] = Form.useForm<{ loginName: string }>();
  // 个人空间没有成员集合，提前关闭 Query 可避免无意义请求和错误状态闪烁。
  const members = useQuery({
    queryKey: ["workspace-members", workspaceId],
    queryFn: ({ signal }) => getWorkspaceMembers(workspaceId!, signal),
    enabled: Boolean(workspaceId && currentWorkspace?.workspace_type === "enterprise"),
    retry: false,
  });
  const invite = useMutation({
    mutationFn: ({ loginName }: { loginName: string }) =>
      inviteWorkspaceMember(workspaceId!, loginName),
    onSuccess: (invitation) => {
      setInviteOpen(false);
      form.resetFields();
      void message.success(`邀请已创建：${invitation.invitation_id}`);
    },
    onError: (error) => void message.error(errorMessage(error)),
  });
  const disable = useMutation({
    mutationFn: (accountId: string) => disableWorkspaceMember(workspaceId!, accountId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["workspace-members", workspaceId] });
      void message.success("成员已停用");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });

  if (workspaces.isLoading || (!currentWorkspace && !workspaces.isError))
    return <Skeleton active paragraph={{ rows: 8 }} />;
  if (workspaces.isError || !currentWorkspace)
    return (
      <StateView
        kind="error"
        title="空间信息未能加载"
        description={errorMessage(workspaces.error)}
      />
    );
  if (currentWorkspace.workspace_type === "personal") {
    return (
      <StateView
        kind="empty"
        title="个人空间无需成员管理"
        description="个人空间只有唯一所有者。切换到企业空间后可邀请和治理成员。"
      />
    );
  }
  const denied =
    members.error instanceof PlatformApiError && members.error.code === "POLICY_DENIED";
  if (denied)
    return (
      <StateView
        kind="denied"
        title="当前账号没有成员治理权限"
        description="企业普通成员可以使用空间能力，但成员清单与停用操作仅向空间所有者开放。"
      />
    );
  if (members.isError)
    return (
      <StateView
        kind="error"
        title="成员清单未能加载"
        description={errorMessage(members.error)}
        action={<Button onClick={() => void members.refetch()}>重新加载</Button>}
      />
    );

  const columns: TableColumnsType<WorkspaceMember> = [
    {
      title: "成员",
      dataIndex: "display_name",
      key: "display_name",
      render: (name, record) => (
        <div>
          <strong className="block text-text-strong">{name}</strong>
          <span className="mt-[3px] block font-mono text-[11px] text-text-muted">
            {record.account_id}
          </span>
        </div>
      ),
    },
    {
      title: "身份",
      dataIndex: "membership_type",
      key: "membership_type",
      width: 110,
      render: (value) => (value === "owner" ? "所有者" : "成员"),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 100,
      render: (value) => (
        <Tag color={value === "active" ? "success" : "default"}>
          {value === "active" ? "启用" : value === "disabled" ? "已停用" : "已离开"}
        </Tag>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 120,
      render: (_, record) =>
        record.membership_type === "owner" || record.status !== "active" ? (
          <span className="text-xs text-text-muted">不可停用</span>
        ) : (
          <Popconfirm
            title="确认停用该成员？"
            description="下次请求会立即撤销空间访问。"
            okText="停用"
            cancelText="取消"
            onConfirm={() => disable.mutate(record.account_id)}
          >
            <Button type="text" danger icon={<UserMinus size={16} />}>
              停用
            </Button>
          </Popconfirm>
        ),
    },
  ];

  return (
    <>
      <PageHeader
        eyebrow="MEMBERS"
        title="成员管理"
        description="邀请已注册账号加入企业空间，查看成员状态，并在必要时立即撤销访问。"
        actions={
          <Button type="primary" icon={<UserPlus size={17} />} onClick={() => setInviteOpen(true)}>
            邀请成员
          </Button>
        }
      />
      <section className="ui-surface-panel overflow-hidden" aria-label="企业成员清单">
        <Table<WorkspaceMember>
          rowKey="account_id"
          columns={columns}
          dataSource={members.data ?? []}
          loading={members.isLoading}
          pagination={false}
          scroll={{ x: 680 }}
          locale={{
            emptyText: (
              <StateView
                kind="empty"
                title="还没有企业成员"
                description="邀请一个已注册账号，建立模拟企业协作空间。"
              />
            ),
          }}
        />
      </section>
      <Modal
        title="邀请成员"
        open={inviteOpen}
        okText="创建邀请"
        cancelText="取消"
        confirmLoading={invite.isPending}
        onCancel={() => setInviteOpen(false)}
        onOk={() => void form.validateFields().then((values) => invite.mutate(values))}
      >
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item
            label="成员登录名"
            name="loginName"
            rules={[
              { required: true, message: "请输入已注册账号的登录名" },
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
