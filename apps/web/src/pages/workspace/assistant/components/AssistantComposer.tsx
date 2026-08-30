/** @description 问答输入区，负责输入长度提示、提交和活动 Run 取消。 */
import { Button, Dropdown, Input, Tooltip, Typography } from "antd";
import { ArrowUp, Sparkles, Square } from "lucide-react";
import { useState } from "react";

const MAX_LENGTH = 100_000;
const QUICK_COMMANDS = [
  {
    key: "summary",
    label: "总结要点",
    prompt: "请基于当前知识范围总结核心要点，并按重要性排序。",
  },
  {
    key: "actions",
    label: "提取行动项",
    prompt: "请基于当前知识范围提取行动项，列出负责人、时间要求和依赖；缺失信息请明确标注。",
  },
  {
    key: "compare",
    label: "对比差异",
    prompt: "请对比当前知识范围内相关材料的主要差异、共同点和冲突，并附上来源。",
  },
  {
    key: "evidence",
    label: "依据来源回答",
    prompt: "请只依据当前知识范围回答，并为每个关键结论标注可核对的来源；证据不足时请直接说明。",
  },
] as const;

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
        <div className="flex items-center gap-2">
          <Dropdown
            trigger={["click"]}
            menu={{
              items: QUICK_COMMANDS.map((item) => ({ key: item.key, label: item.label })),
              onClick: ({ key }) => {
                const command = QUICK_COMMANDS.find((item) => item.key === key);
                if (command) setValue(command.prompt);
              },
            }}
            disabled={disabled}
          >
            <Tooltip title="快捷指令">
              <Button
                type="text"
                aria-label="选择快捷指令"
                icon={<Sparkles size={16} />}
                disabled={disabled}
              />
            </Tooltip>
          </Dropdown>
          <Typography.Text type="secondary" className="text-xs">
            {value.length.toLocaleString()} / {MAX_LENGTH.toLocaleString()}
          </Typography.Text>
        </div>
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
