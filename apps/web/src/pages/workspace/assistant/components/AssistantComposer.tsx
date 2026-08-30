/** @description 问答输入区，负责输入长度提示、提交和活动 Run 取消。 */
import { Button, Input, Typography } from "antd";
import { ArrowUp, Square } from "lucide-react";
import { useState } from "react";

const MAX_LENGTH = 100_000;

/** 渲染有界文本输入、发送命令和活动 Run 的取消命令。 */
export function AssistantComposer({
  initialValue = "",
  disabled,
  sending,
  cancellable,
  onSend,
  onCancel,
}: {
  /** 工作台跳转时预填的提问草稿；组件不会自动发送。 */
  initialValue?: string;
  disabled: boolean;
  sending: boolean;
  cancellable: boolean;
  onSend: (text: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initialValue);
  const canSend = value.trim().length > 0 && value.length <= MAX_LENGTH && !disabled;
  function submit() {
    const text = value.trim();
    if (!text || text.length > MAX_LENGTH || disabled) return;
    onSend(text);
    setValue("");
  }
  return (
    <div className="border-t border-t-solid border-border bg-surface px-4 py-3 phone-down:px-3">
      <Input.TextArea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onPressEnter={(event) => {
          if (!event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
        autoSize={{ minRows: 2, maxRows: 6 }}
        maxLength={MAX_LENGTH}
        disabled={disabled}
        placeholder="输入问题，Enter 发送，Shift+Enter 换行"
        aria-label="输入知识问答问题"
      />
      <div className="mt-2 flex items-center justify-between gap-3">
        <Typography.Text type="secondary" className="text-xs">
          {value.length.toLocaleString()} / {MAX_LENGTH.toLocaleString()}
        </Typography.Text>
        {cancellable ? (
          <Button icon={<Square size={15} />} onClick={onCancel}>
            停止生成
          </Button>
        ) : (
          <Button
            type="primary"
            icon={<ArrowUp size={16} />}
            loading={sending}
            disabled={!canSend}
            onClick={submit}
          >
            发送
          </Button>
        )}
      </div>
    </div>
  );
}
