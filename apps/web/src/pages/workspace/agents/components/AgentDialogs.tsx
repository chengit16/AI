/** @description 创建 Agent 的基础配置与完整 JSON 双模式表单。 */
import { Form, Input, Modal, Segmented } from "antd";
import { useEffect } from "react";

import type { CreateAgentRequest } from "@/api/services/agents";

import { parseConfiguration } from "../config";

interface CreateAgentFormValues {
  name: string;
  description?: string;
  configurationMode: "starter" | "advanced";
  configuration?: string;
}

/** 创建 Agent 弹窗状态与提交回调。 */
export interface AgentDialogsProps {
  /** 是否打开创建弹窗。 */
  open: boolean;
  /** 创建请求正在提交。 */
  isCreating: boolean;
  /** 关闭弹窗且不提交。 */
  onClose: () => void;
  /** 提交名称、说明及互斥的基础配置或完整配置模式。 */
  onCreate: (body: CreateAgentRequest) => Promise<unknown>;
}

/** 创建 Agent 和首个草稿，成功后关闭并清空临时表单。 */
export function AgentDialogs({ open, isCreating, onClose, onCreate }: AgentDialogsProps) {
  const [form] = Form.useForm<CreateAgentFormValues>();
  const configurationMode = Form.useWatch("configurationMode", form) ?? "starter";

  useEffect(() => {
    if (open) {
      form.setFieldsValue({ configurationMode: "starter", configuration: "{}" });
    }
  }, [form, open]);

  const handleSubmit = async () => {
    const values = await form.validateFields();
    const definition = {
      name: values.name.trim(),
      description: values.description?.trim() || null,
    };
    if (values.configurationMode === "starter") {
      await onCreate({ ...definition, use_starter_configuration: true });
    } else {
      const configuration = parseConfiguration(values.configuration ?? "");
      if (!configuration) return;
      await onCreate({ ...definition, configuration, use_starter_configuration: false });
    }
    form.resetFields();
    onClose();
  };

  const handleClose = () => {
    form.resetFields();
    onClose();
  };

  return (
    <Modal
      open={open}
      title="创建 Agent"
      okText="创建"
      cancelText="取消"
      confirmLoading={isCreating}
      width={760}
      onCancel={handleClose}
      onOk={() => void handleSubmit()}
    >
      <Form form={form} layout="vertical" requiredMark="optional">
        <Form.Item name="name" label="名称" rules={[{ required: true, min: 1, max: 120 }]}>
          <Input placeholder="例如：合成制度问答助手" />
        </Form.Item>
        <Form.Item name="description" label="说明">
          <Input.TextArea rows={2} maxLength={500} placeholder="说明使用场景和维护边界" />
        </Form.Item>
        <Form.Item
          name="configurationMode"
          label="配置方式"
          extra={
            configurationMode === "starter"
              ? "使用当前 Runtime、安全策略、空知识范围和保守预算。"
              : "版本引用、工具权限和预算将由服务端逐项复核。"
          }
        >
          <Segmented
            block
            options={[
              { label: "基础配置", value: "starter" },
              { label: "完整 JSON", value: "advanced" },
            ]}
          />
        </Form.Item>
        {configurationMode === "advanced" ? (
          <Form.Item
            name="configuration"
            label="完整配置 JSON"
            rules={[
              { required: true },
              {
                validator: (_, value: string) =>
                  parseConfiguration(value)
                    ? Promise.resolve()
                    : Promise.reject(new Error("配置必须是有效 JSON 对象")),
              },
            ]}
          >
            <Input.TextArea
              aria-label="新 Agent 完整配置 JSON"
              autoSize={{ minRows: 8, maxRows: 18 }}
              className="font-mono text-xs leading-6"
            />
          </Form.Item>
        ) : null}
      </Form>
    </Modal>
  );
}
