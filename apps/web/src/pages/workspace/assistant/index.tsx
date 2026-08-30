/** @description 私有知识问答页面，完成会话、流式生成、来源查看和反馈闭环。 */
import { Alert, App, Button, Divider, Result, Typography } from "antd";
import { Plus } from "lucide-react";
import { useSearchParams } from "react-router";

import { errorMessage } from "@/api/client";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";

import { AssistantComposer } from "./components/AssistantComposer";
import { AssistantContextBar } from "./components/AssistantContextBar";
import { AssistantFeedbackModal } from "./components/AssistantFeedbackModal";
import { AssistantSourcesDrawer } from "./components/AssistantSourcesDrawer";
import { ConversationRail } from "./components/ConversationRail";
import { MessageThread } from "./components/MessageThread";
import { useAssistantConversation } from "./useAssistantConversation";

/** 组合会话导航、问答线程、恢复状态、来源和反馈交互。 */
export default function AssistantConversationsPage() {
  const { message } = App.useApp();
  const [searchParams] = useSearchParams();
  const initialConversationId = searchParams.get("conversation");
  const initialPrompt = searchParams.get("prompt") ?? "";
  const { visiblePermissionCodes } = useWorkspaceMenuNavigation();
  const canCreate = visiblePermissionCodes.has("assistant.conversation.create");
  const canArchive = visiblePermissionCodes.has("assistant.conversation.archive");
  const canManageScope = visiblePermissionCodes.has("assistant.conversation.scope.manage");
  const canManageAttachments = visiblePermissionCodes.has("assistant.attachment.manage");
  const canAsk = visiblePermissionCodes.has("assistant.message.create");
  const canCancel = visiblePermissionCodes.has("assistant.run.cancel");
  const canSource = visiblePermissionCodes.has("assistant.source.read");
  const canFeedback = visiblePermissionCodes.has("assistant.feedback.manage");
  const model = useAssistantConversation(
    initialConversationId,
    canManageScope,
    canManageAttachments,
  );

  if (model.conversations.isError) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="问答会话未能加载"
        description={errorMessage(model.conversations.error)}
        action={<Button onClick={() => void model.conversations.refetch()}>重新加载</Button>}
      />
    );
  }

  function handleCreate() {
    if (!canCreate || model.createConversation.isPending) return;
    model.createConversation.mutate(undefined, {
      onError: (error) => message.error(errorMessage(error)),
    });
  }

  function handleSend(text: string) {
    if (!canAsk || model.selectedConversation?.status !== "active") return;
    model.sendMessage(text);
  }

  function handleArchive(conversationId: string) {
    if (!canArchive || model.archiveConversation.isPending) return;
    model.archiveConversation.mutate(conversationId, {
      onSuccess: () => message.success("会话已归档"),
      onError: (error) => message.error(errorMessage(error)),
    });
  }

  return (
    <>
      <PageHeader
        eyebrow="ASSISTANT"
        title="知识问答"
        description="在当前空间权限范围内提问，答案和来源会分别保存并可恢复。"
        actions={
          canCreate ? (
            <Button icon={<Plus size={16} />} onClick={handleCreate}>
              新建会话
            </Button>
          ) : undefined
        }
      />
      <div className="grid min-h-[620px] grid-cols-[280px_minmax(0,1fr)] gap-4 tablet-down:grid-cols-1">
        <ConversationRail
          items={model.conversations.data ?? []}
          selectedId={model.selectedConversationId}
          loading={model.conversations.isLoading}
          creating={model.createConversation.isPending}
          filter={model.conversationStatus}
          canArchive={canArchive}
          archiving={model.archiveConversation.isPending}
          onSelect={model.selectConversation}
          onFilter={model.filterConversations}
          onCreate={handleCreate}
          onArchive={handleArchive}
        />
        <section className="ui-surface-panel flex min-h-[620px] min-w-0 flex-col overflow-hidden">
          {!model.selectedConversation ? (
            <Result
              className="m-auto"
              status="info"
              title="选择或创建一个问答会话"
              subTitle="会话只对创建者可见，企业管理员不会默认读取成员私聊。"
              extra={
                canCreate ? (
                  <Button type="primary" onClick={handleCreate}>
                    创建会话
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <>
              <div className="flex items-start justify-between gap-4 border-b border-b-solid border-border px-5 py-4 phone-down:px-3">
                <div className="min-w-0">
                  <Typography.Title level={4} className="!mb-1 truncate">
                    {model.selectedConversation.title || "未命名问答"}
                  </Typography.Title>
                  <Typography.Text type="secondary" className="text-xs">
                    私有会话 · {model.messages.data?.length ?? 0} 条消息
                  </Typography.Text>
                </div>
                {model.stream.status === "error" && (
                  <Alert
                    type="warning"
                    showIcon
                    message="连接已中断"
                    description="可刷新页面从已保存事件继续恢复。"
                  />
                )}
              </div>
              {(model.knowledgeBases.isError || model.knowledgeTags.isError) &&
                model.selectedConversation.status === "active" && (
                  <Alert
                    type="warning"
                    showIcon
                    message="知识范围选项未能加载"
                    description={errorMessage(
                      model.knowledgeBases.error ?? model.knowledgeTags.error,
                    )}
                  />
                )}
              <AssistantContextBar
                key={`${model.selectedConversation.conversation_id}:${model.selectedConversation.version}`}
                conversation={model.selectedConversation}
                knowledgeBases={model.knowledgeBases.data ?? []}
                knowledgeTags={model.knowledgeTags.data ?? []}
                attachments={model.attachments.data ?? []}
                canManageScope={canManageScope && model.selectedConversation.status === "active"}
                canManageAttachments={
                  canManageAttachments && model.selectedConversation.status === "active"
                }
                busy={Boolean(model.activeRun)}
                savingScope={model.updateScope.isPending}
                uploading={model.uploadAttachment.isPending}
                onSaveScope={(body) => {
                  model.updateScope.mutate(body, {
                    onSuccess: () => message.success("知识范围已保存"),
                    onError: (error) => message.error(errorMessage(error)),
                  });
                }}
                onUpload={(file) => {
                  model.uploadAttachment.mutate(file, {
                    onSuccess: () => message.success("临时附件已添加"),
                    onError: (error) => message.error(errorMessage(error)),
                  });
                }}
                onDeleteAttachment={(attachmentId) => {
                  model.deleteAttachment.mutate(attachmentId, {
                    onSuccess: () => message.success("临时附件已删除"),
                    onError: (error) => message.error(errorMessage(error)),
                  });
                }}
              />
              <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 phone-down:px-3">
                <MessageThread
                  messages={model.messages.data ?? []}
                  stream={model.stream}
                  activeRun={model.activeRun}
                  loading={model.messages.isLoading}
                  onSources={(id) => canSource && model.openSources(id)}
                  onFeedback={(id, rating) => {
                    if (!canFeedback) return;
                    if (rating === "helpful") {
                      model.submitFeedback.mutate(
                        { messageId: id, body: { rating: "helpful", issue_codes: [] } },
                        {
                          onSuccess: () => message.success("反馈已保存"),
                          onError: (error) => message.error(errorMessage(error)),
                        },
                      );
                    } else {
                      model.openFeedback(id);
                    }
                  }}
                />
              </div>
              <Divider className="!my-0" />
              {model.selectedConversation.status === "archived" ? (
                <Alert
                  className="!rounded-none"
                  type="info"
                  showIcon
                  message="该会话已归档，只能查看历史消息"
                />
              ) : (
                <AssistantComposer
                  initialValue={initialPrompt}
                  disabled={!canAsk || Boolean(model.activeRun) || model.createMessage.isPending}
                  sending={model.createMessage.isPending}
                  cancellable={Boolean(model.activeRun && canCancel)}
                  onSend={handleSend}
                  onCancel={() => {
                    if (model.activeRun && canCancel) {
                      model.cancelRun.mutate(model.activeRun, {
                        onError: (error) => message.error(errorMessage(error)),
                      });
                    }
                  }}
                />
              )}
            </>
          )}
        </section>
      </div>
      <AssistantSourcesDrawer
        open={Boolean(model.sourceMessageId)}
        loading={model.sources.isLoading}
        error={model.sources.error}
        items={model.sources.data ?? []}
        onClose={model.closeSources}
      />
      <AssistantFeedbackModal
        open={Boolean(model.feedbackMessageId)}
        message={model.feedbackMessage}
        loading={model.feedback.isLoading || model.submitFeedback.isPending}
        initial={model.feedback.data ?? null}
        onClose={model.closeFeedback}
        onSubmit={(messageId, body) => {
          model.submitFeedback.mutate(
            { messageId, body },
            {
              onSuccess: () => {
                message.success("反馈已保存");
                model.closeFeedback();
              },
              onError: (error) => message.error(errorMessage(error)),
            },
          );
        }}
      />
    </>
  );
}
