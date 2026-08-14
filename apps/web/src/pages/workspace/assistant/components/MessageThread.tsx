/** @description 问答消息区，合并持久化消息与当前 Run 的流式临时内容。 */
import { Button, Empty, Spin, Tag, Tooltip, Typography } from "antd";
import { BookOpen, CircleAlert, ThumbsDown, ThumbsUp } from "lucide-react";

import type { AssistantMessage } from "@/api/services/assistant";
import type { AssistantStreamState } from "../useAssistantConversation";
import { cn } from "@/utils/cn";

function messageText(message: AssistantMessage) {
  return message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n");
}

/** 合并持久化消息和活动 SSE 临时正文，并暴露来源与反馈命令。 */
export function MessageThread({
  messages,
  stream,
  activeRun,
  loading,
  onSources,
  onFeedback,
}: {
  messages: ReadonlyArray<AssistantMessage>;
  stream: AssistantStreamState;
  activeRun: { assistant_message_id: string | null } | null;
  loading: boolean;
  onSources: (messageId: string) => void;
  onFeedback: (messageId: string, rating: "helpful" | "unhelpful") => void;
}) {
  if (loading) return <Spin className="m-auto" />;
  if (!messages.length) {
    return (
      <Empty
        className="m-auto"
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="从一个具体问题开始，回答会保留可追溯来源"
      />
    );
  }
  return (
    <div className="flex min-h-full flex-col gap-6">
      {messages.map((message) => {
        const isAssistant = message.role === "assistant";
        const isStreaming = isAssistant && activeRun?.assistant_message_id === message.message_id;
        const text = isStreaming && stream.text ? stream.text : messageText(message);
        const failed =
          isAssistant &&
          (message.status === "failed" ||
            (stream.assistantMessageId === message.message_id && stream.status === "failed"));
        return (
          <article
            key={message.message_id}
            className={cn("flex gap-3", isAssistant ? "items-start" : "justify-end")}
          >
            <div
              className={cn(
                "max-w-[min(760px,88%)] border border-solid px-4 py-3",
                isAssistant ? "border-border bg-surface" : "border-brand/20 bg-brand-soft",
              )}
            >
              <div className="mb-2 flex items-center gap-2">
                <Typography.Text strong>{isAssistant ? "知识助手" : "我"}</Typography.Text>
                {isStreaming && <Tag color="processing">{stream.stage || "生成中"}</Tag>}
                {failed && <Tag color="error">回答未完成</Tag>}
              </div>
              {isStreaming && !text ? (
                <div className="flex items-center gap-2 text-text-muted">
                  <Spin size="small" /> 正在准备回答
                </div>
              ) : (
                <Typography.Paragraph className="!mb-0 whitespace-pre-wrap break-words text-sm leading-7">
                  {text || (failed ? "本次回答没有生成正文。" : "")}
                </Typography.Paragraph>
              )}
              {isAssistant && message.status === "completed" && !isStreaming && (
                <div className="mt-3 flex flex-wrap items-center gap-1 border-t border-t-solid border-border pt-2">
                  <Tooltip title="查看来源">
                    <Button
                      type="text"
                      size="small"
                      icon={<BookOpen size={15} />}
                      onClick={() => onSources(message.message_id)}
                    >
                      来源
                    </Button>
                  </Tooltip>
                  <Tooltip title="回答有帮助">
                    <Button
                      type="text"
                      size="small"
                      aria-label="回答有帮助"
                      icon={<ThumbsUp size={15} />}
                      onClick={() => onFeedback(message.message_id, "helpful")}
                    />
                  </Tooltip>
                  <Tooltip title="回答需要改进">
                    <Button
                      type="text"
                      size="small"
                      aria-label="回答需要改进"
                      icon={<ThumbsDown size={15} />}
                      onClick={() => onFeedback(message.message_id, "unhelpful")}
                    />
                  </Tooltip>
                </div>
              )}
              {failed && (
                <div className="mt-2 flex items-center gap-1 text-xs text-danger">
                  <CircleAlert size={14} /> {stream.errorCode || "RUN_FAILED"}
                </div>
              )}
            </div>
          </article>
        );
      })}
    </div>
  );
}
