/** @description 模型供应商创建、凭证轮换和数据政策复核弹窗集合。 */
import { Form, Input, InputNumber, Modal, Select, Switch } from "antd";

import type {
  CreateModelProviderRequest,
  ModelProvider,
  ReviewModelProviderDataPolicyRequest,
} from "@/api/services/platformModels";

interface ProviderDialogsProps {
  /** 是否打开新建供应商弹窗。 */
  createOpen: boolean;
  /** 当前准备轮换凭证的供应商；为空时关闭对应弹窗。 */
  credentialProvider: ModelProvider | null;
  /** 当前准备复核数据政策的供应商；为空时关闭对应弹窗。 */
  policyProvider: ModelProvider | null;
  /** 任一供应商写操作进行中时统一锁定确认按钮。 */
  isSubmitting: boolean;
  /** 关闭创建弹窗但不提交表单。 */
  onCloseCreate: () => void;
  /** 关闭凭证轮换弹窗。 */
  onCloseCredential: () => void;
  /** 关闭数据政策复核弹窗。 */
  onClosePolicy: () => void;
  /** 提交新供应商配置和仅本次可见的明文凭证。 */
  onCreate: (values: CreateModelProviderRequest) => Promise<unknown>;
  /** 为指定供应商提交新凭证；成功后旧凭证由服务端失效。 */
  onRotateCredential: (providerId: string, apiKey: string) => Promise<unknown>;
  /** 提交指定供应商的数据政策人工复核结论。 */
  onReviewPolicy: (
    providerId: string,
    values: ReviewModelProviderDataPolicyRequest,
  ) => Promise<unknown>;
}

/** 编排三个相互独立的供应商治理表单，并只在成功后重置本地字段。 */
export function ProviderDialogs({
  createOpen,
  credentialProvider,
  policyProvider,
  isSubmitting,
  onCloseCreate,
  onCloseCredential,
  onClosePolicy,
  onCreate,
  onRotateCredential,
  onReviewPolicy,
}: ProviderDialogsProps) {
  const [createForm] = Form.useForm<CreateModelProviderRequest>();
  const [credentialForm] = Form.useForm<{ apiKey: string }>();
  const [policyForm] = Form.useForm<ReviewModelProviderDataPolicyRequest>();

  const finishCreate = async () => {
    await onCreate(await createForm.validateFields());
    createForm.resetFields();
    onCloseCreate();
  };
  const finishCredential = async () => {
    if (!credentialProvider) return;
    const values = await credentialForm.validateFields();
    await onRotateCredential(credentialProvider.provider_id, values.apiKey);
    credentialForm.resetFields();
    onCloseCredential();
  };
  const finishPolicy = async () => {
    if (!policyProvider) return;
    await onReviewPolicy(policyProvider.provider_id, await policyForm.validateFields());
    policyForm.resetFields();
    onClosePolicy();
  };

  return (
    <>
      <Modal
        title="创建模型供应商"
        open={createOpen}
        width={680}
        okText="创建"
        cancelText="取消"
        confirmLoading={isSubmitting}
        onCancel={onCloseCreate}
        onOk={() => void finishCreate().catch(() => undefined)}
      >
        <Form
          form={createForm}
          layout="vertical"
          requiredMark={false}
          initialValues={{
            adapter_kind: "openai_compatible",
            wire_api: "chat_completions",
            location: "external",
            declared_capabilities: ["generation", "streaming"],
          }}
        >
          <Form.Item
            label="显示名称"
            name="display_name"
            rules={[{ required: true }, { max: 120 }]}
          >
            <Input autoFocus placeholder="例如：GPT 主模型" />
          </Form.Item>
          <Form.Item
            label="供应商标识"
            name="provider_key"
            rules={[
              { required: true },
              { pattern: /^[a-z][a-z0-9_]+$/, message: "使用小写字母、数字和下划线" },
            ]}
          >
            <Input placeholder="gpt_primary" />
          </Form.Item>
          <Form.Item label="自定义 Base URL" name="base_url" rules={[{ required: true }]}>
            <Input placeholder="https://approved-gateway.example/v1" />
          </Form.Item>
          <Form.Item label="调用协议" name="wire_api" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "chat_completions", label: "Chat Completions" },
                { value: "responses", label: "Responses（Codex）" },
              ]}
            />
          </Form.Item>
          <Form.Item label="探测模型 ID" name="probe_model_id" rules={[{ required: true }]}>
            <Input placeholder="gpt-5-mini" />
          </Form.Item>
          <Form.Item label="部署位置" name="location">
            <Select
              options={[
                { value: "external", label: "外部供应商" },
                { value: "private", label: "私有部署" },
              ]}
            />
          </Form.Item>
          <Form.Item label="声明能力" name="declared_capabilities" rules={[{ required: true }]}>
            <Select
              mode="multiple"
              options={[
                { value: "generation", label: "生成" },
                { value: "streaming", label: "流式输出" },
                { value: "tools", label: "工具调用" },
                { value: "structured_output", label: "结构化输出" },
              ]}
            />
          </Form.Item>
          <Form.Item label="API Key" name="api_key" rules={[{ required: true }]}>
            <Input.Password autoComplete="new-password" placeholder="仅在提交边缘使用，不会回显" />
          </Form.Item>
          <Form.Item name="adapter_kind" hidden>
            <Input />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`轮换凭证${credentialProvider ? `：${credentialProvider.display_name}` : ""}`}
        open={Boolean(credentialProvider)}
        okText="轮换"
        cancelText="取消"
        confirmLoading={isSubmitting}
        onCancel={onCloseCredential}
        onOk={() => void finishCredential().catch(() => undefined)}
      >
        <Form form={credentialForm} layout="vertical" requiredMark={false}>
          <Form.Item label="新 API Key" name="apiKey" rules={[{ required: true }]}>
            <Input.Password autoComplete="new-password" placeholder="提交后不会再次显示" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`审核数据政策${policyProvider ? `：${policyProvider.display_name}` : ""}`}
        open={Boolean(policyProvider)}
        okText="记录审核结果"
        cancelText="取消"
        confirmLoading={isSubmitting}
        onCancel={onClosePolicy}
        onOk={() => void finishPolicy().catch(() => undefined)}
      >
        <Form
          form={policyForm}
          layout="vertical"
          requiredMark={false}
          initialValues={{
            approved: true,
            max_security_level: "PUBLIC",
            retention_days: 0,
            training_usage_allowed: false,
          }}
        >
          <Form.Item label="审核通过" name="approved" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item label="允许外发的最高密级" name="max_security_level">
            <Select
              options={[
                { value: "PUBLIC", label: "公开" },
                { value: "INTERNAL", label: "内部" },
                { value: "CONFIDENTIAL", label: "机密" },
                { value: "RESTRICTED", label: "严格限制" },
              ]}
            />
          </Form.Item>
          <Form.Item label="供应商数据保留天数" name="retention_days">
            <InputNumber min={0} max={3650} precision={0} />
          </Form.Item>
          <Form.Item label="允许用于训练" name="training_usage_allowed" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item label="政策链接" name="policy_url" rules={[{ type: "url" }]}>
            <Input placeholder="https://provider.example/data-policy" />
          </Form.Item>
          <Form.Item label="政策版本" name="policy_version" rules={[{ max: 128 }]}>
            <Input placeholder="例如：2026-08" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
