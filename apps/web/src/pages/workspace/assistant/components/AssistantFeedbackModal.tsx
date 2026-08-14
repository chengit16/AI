/** @description 助手反馈表单，问题标签和自由文本只提交给反馈接口。 */
import { Checkbox, Form, Input, Modal, Radio, Space, Typography } from "antd";

import type { FeedbackRequest, MessageFeedback } from "@/api/services/assistant";

type FeedbackIssue = NonNullable<FeedbackRequest["issue_codes"]>[number];
interface FeedbackFormValue {
  rating: FeedbackRequest["rating"];
  issue_codes: FeedbackIssue[];
  comment?: string;
}

const issueOptions: Array<{ label: string; value: FeedbackIssue }> = [
  { label: "内容不准确", value: "incorrect" },
  { label: "缺少来源", value: "missing_source" },
  { label: "来源不匹配", value: "source_mismatch" },
  { label: "存在安全问题", value: "unsafe" },
  { label: "其他问题", value: "other" },
];

/** 使用受控表单新增或修订当前消息反馈，并在提交前执行问题标签校验。 */
export function AssistantFeedbackModal({
  open,
  message,
  loading,
  initial,
  onClose,
  onSubmit,
}: {
  open: boolean;
  message: { message_id: string } | null;
  loading: boolean;
  initial: MessageFeedback | null;
  onClose: () => void;
  onSubmit: (messageId: string, body: FeedbackRequest) => void;
}) {
  const [form] = Form.useForm<FeedbackFormValue>();
  const formKey = `${message?.message_id ?? "none"}-${initial?.version ?? 0}`;

  return (
    <Modal
      title="反馈这条回答"
      open={open}
      okText="提交反馈"
      cancelText="取消"
      confirmLoading={loading}
      okButtonProps={{ disabled: !message }}
      destroyOnHidden
      onCancel={onClose}
      onOk={async () => {
        if (!message) return;
        const value = await form.validateFields();
        onSubmit(message.message_id, {
          rating: value.rating,
          issue_codes: value.rating === "helpful" ? [] : value.issue_codes,
          comment: value.comment?.trim() || null,
        });
      }}
    >
      <Form<FeedbackFormValue>
        key={formKey}
        form={form}
        layout="vertical"
        preserve={false}
        initialValues={{
          rating: initial?.rating ?? "unhelpful",
          issue_codes: initial?.issue_codes ? [...initial.issue_codes] : [],
          comment: initial?.comment ?? "",
        }}
      >
        <Space orientation="vertical" size={16} className="w-full">
          <Form.Item name="rating" className="!mb-0">
            <Radio.Group>
              <Radio value="helpful">有帮助</Radio>
              <Radio value="unhelpful">需要改进</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(before, after) => before.rating !== after.rating}>
            {({ getFieldValue }) =>
              getFieldValue("rating") === "unhelpful" ? (
                <div>
                  <Typography.Text strong>请选择问题类型</Typography.Text>
                  <Form.Item
                    name="issue_codes"
                    className="!mb-0 mt-2"
                    rules={[
                      {
                        type: "array",
                        required: true,
                        min: 1,
                        message: "至少选择一个问题类型",
                      },
                    ]}
                  >
                    <Checkbox.Group className="grid gap-2" options={issueOptions} />
                  </Form.Item>
                </div>
              ) : null
            }
          </Form.Item>
          <Form.Item name="comment" className="!mb-0">
            <Input.TextArea
              maxLength={1000}
              showCount
              autoSize={{ minRows: 3, maxRows: 6 }}
              placeholder="补充说明 (可选)"
            />
          </Form.Item>
        </Space>
      </Form>
    </Modal>
  );
}
