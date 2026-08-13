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

import styles from "./OverviewPage.module.css";

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

export default function WorkspaceOverviewPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { workspaceId, workspaces, currentWorkspace } = useCurrentWorkspace();
  const setWorkspaceId = useSessionStore((state) => state.setWorkspaceId);
  const [invitationOpen, setInvitationOpen] = useState(false);
  const [form] = Form.useForm<{ invitationId: string }>();
  const entitlement = useQuery({
    queryKey: ["entitlement", workspaceId],
    queryFn: ({ signal }) => getWorkspaceEntitlement(workspaceId!, signal),
    enabled: Boolean(workspaceId),
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
      await queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      setWorkspaceId(workspace.workspace_id);
      setInvitationOpen(false);
      form.resetFields();
      void message.success("已加入企业空间");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });

  if (workspaces.isLoading || entitlement.isLoading)
    return <Skeleton active paragraph={{ rows: 10 }} />;
  if (workspaces.isError || entitlement.isError || !currentWorkspace || !entitlement.data) {
    return (
      <StateView
        kind="error"
        title="空间信息未能加载"
        description={errorMessage(workspaces.error ?? entitlement.error)}
        action={
          <Button
            onClick={() => {
              void workspaces.refetch();
              void entitlement.refetch();
            }}
          >
            重新加载
          </Button>
        }
      />
    );
  }
  const isOwner = currentWorkspace.membership_type === "owner";
  const isEnterprise = currentWorkspace.workspace_type === "enterprise";

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

      <section className={styles.summary} aria-labelledby="workspace-summary-title">
        <div className={styles.summaryHeading}>
          <span className={styles.workspaceIcon}>
            {isEnterprise ? <Building2 size={22} /> : <UserRound size={22} />}
          </span>
          <div>
            <h2 id="workspace-summary-title">空间概况</h2>
            <p>{isEnterprise ? "企业治理空间" : "默认个人空间"}</p>
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

      <section className={styles.section} aria-labelledby="quota-title">
        <div className={styles.sectionHeading}>
          <div>
            <h2 id="quota-title">资源配额</h2>
            <p>当前周期用量与可用余量</p>
          </div>
          <span>版本 {entitlement.data.entitlement_version}</span>
        </div>
        <div className={styles.quotaGrid}>
          {entitlement.data.quotas.map((quota) => {
            const percent =
              quota.limit_value === 0
                ? 0
                : Math.min(100, Math.round((quota.used_value / quota.limit_value) * 100));
            return (
              <article className={styles.quotaItem} key={quota.metric}>
                <div>
                  <strong>{quotaLabels[quota.metric]}</strong>
                  <span>{quota.period_key === "lifetime" ? "总额度" : quota.period_key}</span>
                </div>
                <p>
                  <b>{quotaValue(quota.metric, quota.used_value)}</b> /{" "}
                  {quotaValue(quota.metric, quota.limit_value)}
                </p>
                <Progress percent={percent} showInfo={false} strokeColor="#176b52" />
                <small>剩余 {quotaValue(quota.metric, quota.remaining_value)}</small>
              </article>
            );
          })}
        </div>
      </section>

      <section className={styles.featureRow} aria-labelledby="open-api-title">
        <span className={styles.featureIcon}>
          <KeyRound size={20} />
        </span>
        <div>
          <h2 id="open-api-title">Open API</h2>
          <p>
            {entitlement.data.open_api_allowed
              ? "允许由企业所有者配置接口访问"
              : "当前套餐不包含 Open API"}
          </p>
        </div>
        <Switch
          aria-label="Open API 开关"
          checked={entitlement.data.open_api_enabled}
          loading={toggleOpenApi.isPending}
          disabled={!entitlement.data.open_api_allowed || !isOwner}
          onChange={(checked) => toggleOpenApi.mutate(checked)}
        />
      </section>

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
    </>
  );
}
