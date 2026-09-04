/** @description P6B-06 AI 企业大脑简版页面，覆盖企业知识问答与报告闭环。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Segmented,
  Select,
  Skeleton,
  Space,
  Statistic,
  Tag,
  Typography,
} from "antd";
import { Archive, BrainCircuit, Download, FileText, Plus, Send, Square } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { errorMessage, PlatformApiError } from "@/api/client";
import { AssistantSseProtocolError, type AssistantStreamEvent } from "@/api/assistantSse";
import type { AssistantMessage, AssistantRun, FeedbackRequest } from "@/api/services/assistant";
import { AssistantFeedbackModal } from "@/pages/workspace/assistant/components/AssistantFeedbackModal";
import { AssistantSourcesDrawer } from "@/pages/workspace/assistant/components/AssistantSourcesDrawer";
import type { AssistantStreamState } from "@/pages/workspace/assistant/useAssistantConversation";
import { MessageThread } from "@/pages/workspace/assistant/components/MessageThread";
import {
  archiveEnterpriseBrainConversation,
  cancelEnterpriseBrainRun,
  createEnterpriseBrainConversation,
  createEnterpriseBrainMessage,
  createEnterpriseBrainReport,
  downloadEnterpriseBrainReport,
  getEnterpriseBrainConversations,
  getEnterpriseBrainFeedback,
  getEnterpriseBrainMessages,
  getEnterpriseBrainOverview,
  getEnterpriseBrainReport,
  getEnterpriseBrainReports,
  getEnterpriseBrainRuns,
  getEnterpriseBrainSources,
  streamEnterpriseBrainRun,
  submitEnterpriseBrainFeedback,
  type EnterpriseBrainReport,
} from "@/api/services/enterpriseBrain";
import { getEnterpriseKnowledgePortal } from "@/api/services/enterpriseKnowledge";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";

const EMPTY_STREAM: AssistantStreamState = {
  runId: null,
  assistantMessageId: null,
  status: "idle",
  stage: null,
  text: "",
  citations: [],
  errorCode: null,
};
const QUICK_PROMPTS = [
  { label: "总结要点", value: "请基于当前企业知识域总结核心要点，并按重要性排序。" },
  { label: "提取行动项", value: "请提取行动项，列出负责人、时间要求和依赖；缺失信息请明确标注。" },
  { label: "对比差异", value: "请对比当前企业知识域内材料的主要差异、共同点和冲突，并附上来源。" },
  { label: "依据来源回答", value: "请只依据当前企业知识域回答，并为关键结论标注可核对来源。" },
] as const;

function reduceStream(
  current: AssistantStreamState,
  event: AssistantStreamEvent,
): AssistantStreamState {
  // 1. 先归并运行阶段和增量正文，保持重连前后的累计文本一致。
  const payload = event.payload;
  if (event.eventType === "tool.status")
    return {
      ...current,
      status: "streaming",
      stage: typeof payload.stage === "string" ? payload.stage : current.stage,
    };
  if (event.eventType === "message.delta")
    return {
      ...current,
      status: "streaming",
      text: current.text + (typeof payload.delta === "string" ? payload.delta : ""),
    };
  if (event.eventType === "message.citation")
    return {
      ...current,
      citations: Array.isArray(payload.citations)
        ? (payload.citations as Array<Record<string, unknown>>)
        : current.citations,
    };
  // 2. 再收敛完成快照或失败终态，未知事件不改变当前可见状态。
  if (event.eventType === "message.completed" || event.eventType === "message.snapshot")
    return {
      ...current,
      status: "completed",
      text: typeof payload.text === "string" ? payload.text : current.text,
      citations: Array.isArray(payload.citations)
        ? (payload.citations as Array<Record<string, unknown>>)
        : current.citations,
    };
  if (event.eventType === "message.failed")
    return {
      ...current,
      status: "failed",
      errorCode: typeof payload.error_code === "string" ? payload.error_code : "RUN_FAILED",
    };
  return current;
}
function shortDate(value: string) {
  return new Date(value).toLocaleDateString("zh-CN");
}

/** 企业大脑只展示当前账号自己的会话、运行和报告。 */
export default function EnterpriseBrainPage() {
  // 长函数保留原因：页面需在同一 React Hook 调用序列中编排查询、流恢复和写操作，拆成条件 Hook 会破坏调用顺序；展示区已由共享组件承载。
  // 1. 解析当前企业空间、菜单权限和页面交互状态，个人空间与无权入口保持失败关闭。
  const { message } = App.useApp();
  const { workspaceId, currentWorkspace, workspaces } = useCurrentWorkspace();
  const menu = useWorkspaceMenuNavigation();
  const queryClient = useQueryClient();
  const permissions = menu.visiblePermissionCodes;
  const canRead = permissions.has("enterprise.brain.read");
  const canCreate = permissions.has("enterprise.brain.conversation.create");
  const canAsk = permissions.has("enterprise.brain.message.create");
  const canArchive = permissions.has("enterprise.brain.conversation.archive");
  const canCancel = permissions.has("enterprise.brain.run.cancel");
  const canSource = permissions.has("enterprise.brain.source.read");
  const canFeedback = permissions.has("enterprise.brain.feedback.manage");
  const canCreateReport = permissions.has("enterprise.brain.report.create");
  const canReadReport = permissions.has("enterprise.brain.report.read");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [domainId, setDomainId] = useState<string | null>(null);
  const [filter, setFilter] = useState<"active" | "archived">("active");
  const [prompt, setPrompt] = useState("");
  const [pendingRun, setPendingRun] = useState<AssistantRun | null>(null);
  const [stream, setStream] = useState<AssistantStreamState>(EMPTY_STREAM);
  const [sourceMessageId, setSourceMessageId] = useState<string | null>(null);
  const [feedbackMessageId, setFeedbackMessageId] = useState<string | null>(null);
  const [reportMessageId, setReportMessageId] = useState<string | null>(null);
  const [reportTitle, setReportTitle] = useState("");
  const [reportTemplate, setReportTemplate] = useState<"briefing" | "risk_review" | "comparison">(
    "briefing",
  );
  const [reportDrawer, setReportDrawer] = useState<EnterpriseBrainReport | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const consumedRunRef = useRef<string | null>(null);
  const enabledEnterprise = Boolean(
    workspaceId && currentWorkspace?.workspace_type === "enterprise" && canRead,
  );
  const overview = useQuery({
    queryKey: ["enterprise-brain-overview", workspaceId],
    queryFn: ({ signal }) => getEnterpriseBrainOverview(workspaceId!, signal),
    enabled: enabledEnterprise,
    retry: false,
  });
  const conversations = useQuery({
    queryKey: ["enterprise-brain-conversations", workspaceId],
    queryFn: ({ signal }) => getEnterpriseBrainConversations(workspaceId!, signal),
    enabled: enabledEnterprise,
    retry: false,
  });
  const portal = useQuery({
    queryKey: ["enterprise-knowledge", workspaceId],
    queryFn: ({ signal }) => getEnterpriseKnowledgePortal(workspaceId!, signal),
    enabled: enabledEnterprise,
    retry: false,
  });
  const visibleConversations = useMemo(
    () => (conversations.data ?? []).filter((item) => item.status === filter),
    [conversations.data, filter],
  );
  const effectiveConversationId =
    conversationId && visibleConversations.some((item) => item.conversation_id === conversationId)
      ? conversationId
      : (visibleConversations[0]?.conversation_id ?? null);
  const selected = useMemo(
    () =>
      conversations.data?.find((item) => item.conversation_id === effectiveConversationId) ?? null,
    [conversations.data, effectiveConversationId],
  );
  const messages = useQuery({
    queryKey: ["enterprise-brain-messages", workspaceId, effectiveConversationId],
    queryFn: ({ signal }) =>
      getEnterpriseBrainMessages(workspaceId!, effectiveConversationId!, signal),
    enabled: Boolean(enabledEnterprise && effectiveConversationId),
    retry: false,
  });
  const runs = useQuery({
    queryKey: ["enterprise-brain-runs", workspaceId, effectiveConversationId],
    queryFn: ({ signal }) => getEnterpriseBrainRuns(workspaceId!, effectiveConversationId!, signal),
    enabled: Boolean(enabledEnterprise && effectiveConversationId),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.some((run) => ["queued", "running"].includes(run.status)) ? 3000 : false,
  });
  const reports = useQuery({
    queryKey: ["enterprise-brain-reports", workspaceId],
    queryFn: ({ signal }) => getEnterpriseBrainReports(workspaceId!, signal),
    enabled: Boolean(enabledEnterprise && canReadReport),
    retry: false,
  });
  const domains = useMemo(
    () => portal.data?.domains.filter((item) => item.status === "active") ?? [],
    [portal.data?.domains],
  );
  const activeRun = useMemo(
    () =>
      pendingRun ?? runs.data?.find((run) => ["queued", "running"].includes(run.status)) ?? null,
    [pendingRun, runs.data],
  );

  // 2. 活动 Run 只消费一次可恢复 SSE，并在终态统一刷新消息、运行和聚合统计。
  useEffect(() => {
    if (
      !activeRun ||
      !workspaceId ||
      !effectiveConversationId ||
      consumedRunRef.current === activeRun.run_id
    )
      return;
    consumedRunRef.current = activeRun.run_id;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    void (async () => {
      try {
        for await (const event of streamEnterpriseBrainRun(
          workspaceId,
          effectiveConversationId,
          activeRun.run_id,
          { signal: controller.signal },
        )) {
          setStream((current) =>
            reduceStream(
              {
                ...current,
                runId: activeRun.run_id,
                assistantMessageId: activeRun.assistant_message_id,
                errorCode: null,
              },
              event,
            ),
          );
        }
        await Promise.all([
          queryClient.invalidateQueries({
            queryKey: ["enterprise-brain-messages", workspaceId, effectiveConversationId],
          }),
          queryClient.invalidateQueries({
            queryKey: ["enterprise-brain-runs", workspaceId, effectiveConversationId],
          }),
          queryClient.invalidateQueries({ queryKey: ["enterprise-brain-overview", workspaceId] }),
        ]);
        setPendingRun(null);
      } catch (error) {
        if (controller.signal.aborted) return;
        const code =
          error instanceof PlatformApiError
            ? error.code
            : error instanceof AssistantSseProtocolError
              ? "SSE_PROTOCOL_ERROR"
              : "SSE_STREAM_ERROR";
        setStream((current) => ({ ...current, status: "error", errorCode: code }));
      }
    })();
    return () => controller.abort();
  }, [activeRun, effectiveConversationId, queryClient, workspaceId]);
  useEffect(() => () => abortRef.current?.abort(), []);

  // 3. 所有写操作通过独立 Mutation 收敛缓存与提示，服务端仍负责资源级授权和事务边界。
  const createConversation = useMutation({
    mutationFn: () => createEnterpriseBrainConversation(workspaceId!, domainId!),
    onSuccess: async (value) => {
      setConversationId(value.conversation_id);
      setFilter("active");
      await queryClient.invalidateQueries({
        queryKey: ["enterprise-brain-conversations", workspaceId],
      });
      message.success("会话已创建，知识域策略已冻结");
    },
    onError: (error) => message.error(errorMessage(error)),
  });
  const sendMessage = useMutation({
    mutationFn: (value: string) =>
      createEnterpriseBrainMessage(
        workspaceId!,
        effectiveConversationId!,
        value,
        "enterprise-brain-" + crypto.randomUUID(),
      ),
    onSuccess: async (value) => {
      setPrompt("");
      setPendingRun(value.run as AssistantRun);
      setStream({
        ...EMPTY_STREAM,
        runId: value.run.run_id,
        assistantMessageId: value.run.assistant_message_id,
        status: "connecting",
      });
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["enterprise-brain-messages", workspaceId, effectiveConversationId],
        }),
        queryClient.invalidateQueries({
          queryKey: ["enterprise-brain-runs", workspaceId, effectiveConversationId],
        }),
      ]);
    },
    onError: (error) => message.error(errorMessage(error)),
  });
  const archiveConversation = useMutation({
    mutationFn: (id: string) => archiveEnterpriseBrainConversation(workspaceId!, id),
    onSuccess: async () => {
      setConversationId(null);
      setPendingRun(null);
      setStream(EMPTY_STREAM);
      await queryClient.invalidateQueries({
        queryKey: ["enterprise-brain-conversations", workspaceId],
      });
      await queryClient.invalidateQueries({ queryKey: ["enterprise-brain-overview", workspaceId] });
      message.success("会话已归档");
    },
    onError: (error) => message.error(errorMessage(error)),
  });
  const cancelRun = useMutation({
    mutationFn: (run: AssistantRun) =>
      cancelEnterpriseBrainRun(workspaceId!, run.conversation_id, run.run_id),
    onSuccess: async (run) => {
      abortRef.current?.abort();
      setPendingRun(null);
      setStream((current) => ({ ...current, status: "cancelled", errorCode: run.error_code }));
      await queryClient.invalidateQueries({
        queryKey: ["enterprise-brain-runs", workspaceId, run.conversation_id],
      });
      await queryClient.invalidateQueries({ queryKey: ["enterprise-brain-overview", workspaceId] });
    },
    onError: (error) => message.error(errorMessage(error)),
  });
  const sources = useQuery({
    queryKey: ["enterprise-brain-sources", workspaceId, effectiveConversationId, sourceMessageId],
    queryFn: ({ signal }) =>
      getEnterpriseBrainSources(workspaceId!, effectiveConversationId!, sourceMessageId!, signal),
    enabled: Boolean(enabledEnterprise && effectiveConversationId && sourceMessageId && canSource),
    retry: false,
  });
  const feedback = useQuery({
    queryKey: [
      "enterprise-brain-feedback",
      workspaceId,
      effectiveConversationId,
      feedbackMessageId,
    ],
    queryFn: ({ signal }) =>
      getEnterpriseBrainFeedback(
        workspaceId!,
        effectiveConversationId!,
        feedbackMessageId!,
        signal,
      ),
    enabled: Boolean(
      enabledEnterprise && effectiveConversationId && feedbackMessageId && canFeedback,
    ),
    retry: false,
  });
  const submitFeedback = useMutation({
    mutationFn: ({ id, body }: { id: string; body: FeedbackRequest }) =>
      submitEnterpriseBrainFeedback(workspaceId!, effectiveConversationId!, id, body),
    onSuccess: async (value) => {
      await queryClient.invalidateQueries({
        queryKey: [
          "enterprise-brain-feedback",
          workspaceId,
          effectiveConversationId,
          value.message_id,
        ],
      });
    },
    onError: (error) => message.error(errorMessage(error)),
  });
  const createReport = useMutation({
    mutationFn: () =>
      createEnterpriseBrainReport(
        workspaceId!,
        { message_id: reportMessageId!, template: reportTemplate, title: reportTitle.trim() },
        "enterprise-brain-report-" + crypto.randomUUID(),
      ),
    onSuccess: async (value) => {
      setReportMessageId(null);
      setReportTitle("");
      setReportDrawer(value);
      await queryClient.invalidateQueries({ queryKey: ["enterprise-brain-reports", workspaceId] });
      message.success("报告已生成并固定保存");
    },
    onError: (error) => message.error(errorMessage(error)),
  });
  const reportDetail = useMutation({
    mutationFn: (id: string) => getEnterpriseBrainReport(workspaceId!, id),
    onSuccess: (value) => setReportDrawer(value),
    onError: (error) => message.error(errorMessage(error)),
  });

  async function downloadReport(report: EnterpriseBrainReport) {
    try {
      const result = await downloadEnterpriseBrainReport(workspaceId!, report.report_id);
      const url = URL.createObjectURL(result.blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = result.fileName.endsWith(".md") ? result.fileName : result.fileName + ".md";
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      message.error(errorMessage(error));
    }
  }
  if (workspaces.isLoading || menu.isLoading || overview.isLoading || portal.isLoading)
    return <Skeleton active paragraph={{ rows: 12 }} />;
  if (!currentWorkspace || workspaces.isError)
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="空间信息未能加载"
        description={errorMessage(workspaces.error)}
      />
    );
  if (currentWorkspace.workspace_type !== "enterprise")
    return (
      <StateView
        kind="empty"
        headingLevel={1}
        title="个人空间不启用企业大脑"
        description="请切换到企业空间后再访问企业知识问答。"
      />
    );
  if (!canRead || overview.isError || portal.isError)
    return (
      <StateView
        kind="denied"
        headingLevel={1}
        title="当前账号没有企业大脑权限"
        description="企业大脑只向当前企业中已授权的成员开放。"
      />
    );
  const completedMessages = (messages.data ?? []).filter(
    (item) => item.role === "assistant" && item.status === "completed",
  );
  const feedbackMessage =
    messages.data?.find((item) => item.message_id === feedbackMessageId) ?? null;
  return (
    <>
      <PageHeader
        eyebrow="AI ENTERPRISE BRAIN"
        title="AI 企业大脑"
        description={
          "在 " +
          (overview.data?.workspace_name ?? "当前企业") +
          " 的获权知识域内提问；每次提交都会重新鉴权。"
        }
        actions={<BrainCircuit size={24} aria-hidden="true" />}
      />
      <Alert
        className="mb-4"
        type="info"
        showIcon
        message="阶段 5 模型门禁"
        description="当前页面仅验证本地合成机制，真实供应商开通仍需通过质量、成本和数据政策门禁。"
      />
      <div className="grid grid-cols-4 gap-4 tablet-down:grid-cols-2 phone-down:grid-cols-1">
        <Card>
          <Statistic title="活动会话" value={overview.data?.active_conversation_count ?? 0} />
        </Card>
        <Card>
          <Statistic title="近 30 天问答" value={overview.data?.run_count_30d ?? 0} />
        </Card>
        <Card>
          <Statistic
            title="完成 / 失败 / 取消"
            value={
              (overview.data?.completed_run_count_30d ?? 0) +
              " / " +
              (overview.data?.failed_run_count_30d ?? 0) +
              " / " +
              (overview.data?.cancelled_run_count_30d ?? 0)
            }
          />
        </Card>
        <Card>
          <Statistic title="Token" value={overview.data?.token_count_30d ?? 0} />
        </Card>
      </div>
      <Typography.Text type="secondary" className="mt-3 block text-xs">
        统计窗口：
        {overview.data
          ? shortDate(overview.data.window_started_at) +
            " – " +
            shortDate(overview.data.window_ended_at)
          : "加载中"}{" "}
        · 生成于{" "}
        {overview.data ? new Date(overview.data.generated_at).toLocaleString("zh-CN") : "—"}
      </Typography.Text>
      <div className="mt-5 grid grid-cols-[300px_minmax(0,1fr)] gap-5 tablet-down:grid-cols-1">
        <Card
          title="知识域与会话"
          extra={
            canCreate && domains.length > 0 ? (
              <Button
                icon={<Plus size={15} />}
                disabled={!domainId}
                loading={createConversation.isPending}
                onClick={() => createConversation.mutate()}
              >
                新建会话
              </Button>
            ) : undefined
          }
        >
          <Select
            className="mb-3 w-full"
            placeholder="选择团队知识域"
            value={domainId ?? undefined}
            onChange={setDomainId}
            options={domains.map((item) => ({
              label:
                item.name +
                " · " +
                item.rag_policy.mode +
                " · 策略 v" +
                item.rag_policy.policy_version,
              value: item.domain_id,
            }))}
          />
          {domains.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="暂无可访问知识域；请联系企业管理员配置范围。"
            />
          ) : (
            <>
              <Segmented
                block
                className="mb-3"
                value={filter}
                options={[
                  { label: "活动", value: "active" },
                  { label: "已归档", value: "archived" },
                ]}
                onChange={(value) => setFilter(value as "active" | "archived")}
              />
              <List
                dataSource={visibleConversations}
                locale={{ emptyText: filter === "active" ? "还没有活动会话" : "还没有归档会话" }}
                renderItem={(item) => (
                  <List.Item
                    className="cursor-pointer"
                    onClick={() => setConversationId(item.conversation_id)}
                    actions={
                      item.status === "active" && canArchive
                        ? [
                            <Popconfirm
                              key="archive"
                              title="归档这个会话？"
                              okText="归档"
                              cancelText="取消"
                              onConfirm={() => archiveConversation.mutate(item.conversation_id)}
                            >
                              <Button
                                type="text"
                                aria-label={"归档会话 " + (item.title || "未命名会话")}
                                icon={<Archive size={15} />}
                              />
                            </Popconfirm>,
                          ]
                        : undefined
                    }
                  >
                    <Space direction="vertical" size={0} className="min-w-0">
                      <Typography.Text strong ellipsis>
                        {item.title || "未命名会话"}
                      </Typography.Text>
                      <Typography.Text type="secondary" className="text-xs">
                        知识域策略 v{item.knowledge_domain_policy_version}
                      </Typography.Text>
                      <Tag>{item.status === "active" ? "活动" : "已归档"}</Tag>
                    </Space>
                  </List.Item>
                )}
              />
            </>
          )}
        </Card>
        <Card
          title={selected ? "企业问答 · " + (selected.title || "未命名会话") : "企业问答"}
          extra={
            selected && activeRun ? (
              <Tag color={stream.status === "error" ? "error" : "processing"}>
                {stream.status === "error" ? "连接中断" : stream.stage || "生成中"}
              </Tag>
            ) : undefined
          }
        >
          {!selected ? (
            <Empty description="请选择或新建企业大脑会话" />
          ) : (
            <>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <Select
                  size="small"
                  placeholder="快捷任务（只预填问题）"
                  options={QUICK_PROMPTS.map((item) => ({ label: item.label, value: item.value }))}
                  onChange={setPrompt}
                />
                <Typography.Text type="secondary" className="text-xs">
                  知识域：
                  {domains.find((item) => item.domain_id === selected.knowledge_domain_id)?.name ??
                    "已冻结范围"}
                </Typography.Text>
              </div>
              <MessageThread
                messages={messages.data ?? []}
                stream={stream}
                activeRun={activeRun}
                loading={messages.isLoading}
                onSources={(id) => canSource && setSourceMessageId(id)}
                onFeedback={(id, rating) => {
                  if (!canFeedback) return;
                  if (rating === "helpful")
                    submitFeedback.mutate({ id, body: { rating: "helpful", issue_codes: [] } });
                  else setFeedbackMessageId(id);
                }}
              />
              {(stream.status === "failed" ||
                stream.status === "error" ||
                stream.status === "cancelled") && (
                <Alert
                  className="mt-3"
                  type={stream.status === "cancelled" ? "info" : "warning"}
                  showIcon
                  message={stream.status === "cancelled" ? "本次回答已取消" : "本次回答未完成"}
                  description={stream.errorCode ?? "可刷新页面从已保存事件继续恢复。"}
                />
              )}
              {selected.status === "archived" ? (
                <Alert
                  className="mt-3"
                  type="info"
                  showIcon
                  message="该会话已归档，只能查看历史消息"
                />
              ) : (
                <Space.Compact className="mt-3 w-full">
                  <Input.TextArea
                    value={prompt}
                    onChange={(event) => setPrompt(event.target.value)}
                    placeholder="询问企业制度、风险或差异；Enter 发送"
                    autoSize={{ minRows: 2, maxRows: 5 }}
                    disabled={!canAsk || Boolean(activeRun) || sendMessage.isPending}
                    onPressEnter={(event) => {
                      if (!event.shiftKey) {
                        event.preventDefault();
                        if (prompt.trim()) sendMessage.mutate(prompt.trim());
                      }
                    }}
                  />
                  <Button
                    type="primary"
                    icon={activeRun ? <Square size={15} /> : <Send size={15} />}
                    disabled={!canAsk || (!activeRun && !prompt.trim())}
                    loading={sendMessage.isPending || cancelRun.isPending}
                    onClick={() =>
                      activeRun
                        ? canCancel
                          ? cancelRun.mutate(activeRun)
                          : undefined
                        : sendMessage.mutate(prompt.trim())
                    }
                  >
                    {activeRun ? "停止" : "发送"}
                  </Button>
                </Space.Compact>
              )}
              {completedMessages.length > 0 && canCreateReport && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {completedMessages.map((item) => (
                    <Button
                      key={item.message_id}
                      size="small"
                      icon={<FileText size={14} />}
                      onClick={() => {
                        setReportMessageId(item.message_id);
                        setReportTitle("企业问答报告");
                      }}
                    >
                      从此回答生成报告
                    </Button>
                  ))}
                </div>
              )}
            </>
          )}
        </Card>
      </div>
      {canReadReport && (
        <Card
          className="mt-5"
          title="我的企业大脑报告"
          extra={<Tag>{reports.data?.length ?? 0} 份</Tag>}
        >
          <List
            size="small"
            dataSource={[...(reports.data ?? [])]}
            locale={{ emptyText: "完成一次回答后可生成不可变 Markdown 报告" }}
            renderItem={(report) => (
              <List.Item
                actions={[
                  <Button
                    key="view"
                    type="link"
                    icon={<FileText size={14} />}
                    loading={reportDetail.isPending}
                    onClick={() => reportDetail.mutate(report.report_id)}
                  >
                    查看
                  </Button>,
                  <Button
                    key="download"
                    type="link"
                    icon={<Download size={14} />}
                    onClick={() => void downloadReport(report)}
                  >
                    下载
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  title={report.title}
                  description={
                    report.template +
                    " · SHA-256 " +
                    report.content_sha256.slice(0, 12) +
                    " · 引用 " +
                    report.citation_count
                  }
                />
              </List.Item>
            )}
          />
        </Card>
      )}
      <AssistantSourcesDrawer
        open={Boolean(sourceMessageId)}
        loading={sources.isLoading}
        error={sources.error}
        items={sources.data ?? []}
        onClose={() => setSourceMessageId(null)}
      />
      <AssistantFeedbackModal
        open={Boolean(feedbackMessageId)}
        message={feedbackMessage as AssistantMessage | null}
        loading={feedback.isLoading || submitFeedback.isPending}
        initial={feedback.data ?? null}
        onClose={() => setFeedbackMessageId(null)}
        onSubmit={(id, body) =>
          submitFeedback.mutate(
            { id, body },
            {
              onSuccess: () => {
                setFeedbackMessageId(null);
                message.success("反馈已保存");
              },
            },
          )
        }
      />
      <Modal
        title="生成不可变 Markdown 报告"
        open={Boolean(reportMessageId)}
        okText="生成报告"
        cancelText="取消"
        confirmLoading={createReport.isPending}
        okButtonProps={{ disabled: !reportTitle.trim() }}
        onCancel={() => setReportMessageId(null)}
        onOk={() => createReport.mutate()}
      >
        <Typography.Paragraph type="secondary">
          报告正文基于已完成回答生成，创建后内容与 SHA-256 不可变。
        </Typography.Paragraph>
        <Input
          value={reportTitle}
          maxLength={200}
          onChange={(event) => setReportTitle(event.target.value)}
          placeholder="报告标题"
        />
        <Select
          className="mt-3 w-full"
          value={reportTemplate}
          onChange={setReportTemplate}
          options={[
            { label: "简报", value: "briefing" },
            { label: "风险评审", value: "risk_review" },
            { label: "差异对比", value: "comparison" },
          ]}
        />
      </Modal>
      <Drawer
        title={reportDrawer?.title ?? "报告详情"}
        open={Boolean(reportDrawer)}
        onClose={() => setReportDrawer(null)}
        size="large"
      >
        {reportDrawer && (
          <>
            <Typography.Paragraph type="secondary">
              模板：{reportDrawer.template} · SHA-256：{reportDrawer.content_sha256}
            </Typography.Paragraph>
            <Typography.Paragraph className="whitespace-pre-wrap break-words">
              {reportDrawer.content}
            </Typography.Paragraph>
            <Button icon={<Download size={15} />} onClick={() => void downloadReport(reportDrawer)}>
              下载 Markdown
            </Button>
          </>
        )}
      </Drawer>
    </>
  );
}
