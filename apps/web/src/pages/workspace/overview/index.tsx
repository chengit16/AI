/** @description 空间总览页，组合当前空间、套餐配额、Open API 开关和邀请加入入口。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  App,
  Button,
  Descriptions,
  Form,
  Input,
  Modal,
  Progress,
  Skeleton,
  Switch,
  Tag,
} from "antd";
import { Building2, KeyRound, TicketCheck, UserRound } from "lucide-react";
import { useState } from "react";

import { errorMessage } from "@/api/client";
import { getWorkspaceEntitlement, setWorkspaceOpenApiFeature } from "@/api/services/entitlements";
import { acceptWorkspaceInvitation } from "@/api/services/workspaces";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";
import { useSessionStore } from "@/store/session";

import { PersonalKnowledgeWorkbench } from "./PersonalKnowledgeWorkbench";

const quotaLabels = {
  members: "成员",
  storage_bytes: "存储空间",
  knowledge_bases: "知识库",
  published_agents: "已发布 Agent",
  questions_monthly: "本月问答",
};

function quotaValue(metric: keyof typeof quotaLabels, value: number) {
  if (metric === "storage_bytes")
    return `${(value / 1024 ** 3).toFixed(value < 1024 ** 3 ? 1 : 0)} GB`;
  return value.toLocaleString("zh-CN");
}

/**
 * 展示当前工作空间的服务端权益快照和可操作入口。
 *
 * 页面中的所有者判断只控制交互状态，套餐、配额和接口开关仍由后端逐请求授权并原子校验。
 */
export default function WorkspaceOverviewPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { workspaceId, workspaces, currentWorkspace } = useCurrentWorkspace();
  const isEnterprise = currentWorkspace?.workspace_type === "enterprise";
  const setWorkspaceId = useSessionStore((state) => state.setWorkspaceId);
  const [invitationOpen, setInvitationOpen] = useState(false);
  const [form] = Form.useForm<{ invitationId: string }>();
  const entitlement = useQuery({
    queryKey: ["entitlement", workspaceId],
    queryFn: ({ signal }) => getWorkspaceEntitlement(workspaceId!, signal),
    enabled: Boolean(workspaceId && isEnterprise),
  });
  const toggleOpenApi = useMutation({
    mutationFn: (enabled: boolean) => setWorkspaceOpenApiFeature(workspaceId!, enabled),
    onSuccess: (data) => {
      queryClient.setQueryData(["entitlement", workspaceId], data);
      void message.success(data.open_api_enabled ? "Open API 已启用" : "Open API 已关闭");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });
  const acceptInvitation = useMutation({
    mutationFn: ({ invitationId }: { invitationId: string }) =>
      acceptWorkspaceInvitation(invitationId),
    onSuccess: async (workspace) => {
      // 接受邀请会切换隔离上下文，先刷新空间清单再更新当前空间，避免选项短暂缺失。
      await queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      setWorkspaceId(workspace.workspace_id);
      setInvitationOpen(false);
      form.resetFields();
      void message.success("已加入企业空间");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });

  const invitationModal = (
    <Modal
      title="接受企业空间邀请"
      open={invitationOpen}
      okText="接受邀请"
      cancelText="取消"
      confirmLoading={acceptInvitation.isPending}
      onCancel={() => setInvitationOpen(false)}
      onOk={() => void form.validateFields().then((values) => acceptInvitation.mutate(values))}
    >
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item
          label="邀请 ID"
          name="invitationId"
          rules={[{ required: true, message: "请输入邀请 ID" }]}
        >
          <Input placeholder="由企业空间所有者提供" />
        </Form.Item>
      </Form>
    </Modal>
  );

  if (workspaces.isLoading || (isEnterprise && entitlement.isLoading))
    return <Skeleton active paragraph={{ rows: 10 }} />;
  if (workspaces.isError || !currentWorkspace) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="空间信息未能加载"
        description={errorMessage(workspaces.error)}
        action={
          <Button
            onClick={() => {
              void workspaces.refetch();
            }}
          >
            重新加载
          </Button>
        }
      />
    );
  }
  if (!isEnterprise) {
    return (
      <>
        <PersonalKnowledgeWorkbench onAcceptInvitation={() => setInvitationOpen(true)} />
        {invitationModal}
      </>
    );
  }
  if (entitlement.isError || !entitlement.data) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="企业权益未能加载"
        description={errorMessage(entitlement.error)}
        action={<Button onClick={() => void entitlement.refetch()}>重新加载</Button>}
      />
    );
  }
  const isOwner = currentWorkspace.membership_type === "owner";

  return (
    <>
      <PageHeader
        eyebrow="WORKSPACE"
        title={currentWorkspace.name}
        description="查看当前空间状态、套餐权益和资源余量。所有额度均由后端原子配额入口校验。"
        actions={
          <Button icon={<TicketCheck size={17} />} onClick={() => setInvitationOpen(true)}>
            接受邀请
          </Button>
        }
      />

      <section
        className="ui-surface-panel p-6 phone-down:p-5"
        aria-labelledby="workspace-summary-title"
      >
        <div className="mb-6 flex items-center gap-4">
          <span className="ui-icon-badge h-11 w-11">
            {isEnterprise ? <Building2 size={22} /> : <UserRound size={22} />}
          </span>
          <div className="flex-1">
            <h2 className="m-0 text-[17px]" id="workspace-summary-title">
              空间概况
            </h2>
            <p className="mb-0 mt-1 text-[13px] text-text-muted">
              {isEnterprise ? "企业治理空间" : "默认个人空间"}
            </p>
          </div>
          <Tag color={currentWorkspace.status === "active" ? "success" : "warning"}>
            {currentWorkspace.status === "active" ? "运行中" : currentWorkspace.status}
          </Tag>
        </div>
        <Descriptions column={{ xs: 1, sm: 2, md: 3 }} size="small">
          <Descriptions.Item label="空间类型">
            {isEnterprise ? "企业版" : "个人版"}
          </Descriptions.Item>
          <Descriptions.Item label="当前身份">{isOwner ? "所有者" : "成员"}</Descriptions.Item>
          <Descriptions.Item label="套餐">
            {entitlement.data.plan_code === "personal_local" ? "本地个人版" : "模拟企业版"}
          </Descriptions.Item>
        </Descriptions>
      </section>

      <section className="ui-surface-panel mt-6" aria-labelledby="quota-title">
        <div className="flex items-center justify-between border-b border-b-solid border-border px-6 py-5 phone-down:p-5">
          <div>
            <h2 className="m-0 text-[17px]" id="quota-title">
              资源配额
            </h2>
            <p className="mb-0 mt-1 text-[13px] text-text-muted">当前周期用量与可用余量</p>
          </div>
          <span className="text-xs text-text-muted">
            版本 {entitlement.data.entitlement_version}
          </span>
        </div>
        <div className="grid grid-cols-[repeat(5,minmax(140px,1fr))] desktop-down:grid-cols-2 phone-down:grid-cols-1">
          {entitlement.data.quotas.map((quota) => {
            // 进度只用于展示，零额度必须显式归零，避免除零产生无效 CSS 百分比。
            const percent =
              quota.limit_value === 0
                ? 0
                : Math.min(100, Math.round((quota.used_value / quota.limit_value) * 100));
            return (
              <article
                className="min-w-0 border-r border-r-solid border-border-soft p-5 last:border-r-0 desktop-down:border-b desktop-down:border-b-solid phone-down:border-r-0"
                key={quota.metric}
              >
                <div className="flex justify-between gap-2">
                  <strong className="text-sm">{quotaLabels[quota.metric]}</strong>
                  <span className="text-[11px] text-text-muted">
                    {quota.period_key === "lifetime" ? "总额度" : quota.period_key}
                  </span>
                </div>
                <p className="mb-3 mt-5 text-xs text-text-muted">
                  <b className="text-[19px] text-text-strong">
                    {quotaValue(quota.metric, quota.used_value)}
                  </b>{" "}
                  / {quotaValue(quota.metric, quota.limit_value)}
                </p>
                <Progress percent={percent} showInfo={false} strokeColor="var(--color-brand)" />
                <small className="mt-2 block text-[11px] text-text-muted">
                  剩余 {quotaValue(quota.metric, quota.remaining_value)}
                </small>
              </article>
            );
          })}
        </div>
      </section>

      <section
        className="ui-surface-panel mt-6 flex min-h-22 items-center gap-4 px-6 py-5"
        aria-labelledby="open-api-title"
      >
        <span className="ui-icon-badge h-11 w-11">
          <KeyRound size={20} />
        </span>
        <div className="flex-1">
          <h2 className="m-0 text-[17px]" id="open-api-title">
            Open API
          </h2>
          <p className="mb-0 mt-1 text-[13px] text-text-muted">
            {entitlement.data.open_api_allowed
              ? "允许由企业所有者配置接口访问"
              : "当前套餐不包含 Open API"}
          </p>
        </div>
        <span className="grid h-11 w-11 flex-none place-items-center">
          <Switch
            aria-label="Open API 开关"
            className="ui-touch-switch"
            checked={entitlement.data.open_api_enabled}
            loading={toggleOpenApi.isPending}
            disabled={!entitlement.data.open_api_allowed || !isOwner}
            onChange={(checked) => toggleOpenApi.mutate(checked)}
          />
        </span>
      </section>

      {invitationModal}
    </>
  );
}
