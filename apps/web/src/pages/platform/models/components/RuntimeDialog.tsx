/** @description 不可变 AI 运行配置版本的创建弹窗。 */
import { Button, Collapse, Form, Input, InputNumber, Modal, Select } from "antd";
import { Plus, Trash2 } from "lucide-react";

import type { components } from "@/api/generated/platform-api.v1";
import type { CreateAiRuntimeConfigRequest, ModelProvider } from "@/api/services/platformModels";

type RuntimeComponents = components["schemas"]["RuntimeComponentVersionsSchema"];
type GatewayPolicy = components["schemas"]["GatewayPolicySchema"];
type RuntimeRoute = components["schemas"]["RuntimeRouteRequest"];

interface RuntimeFormValues {
  display_name: string;
  system_prompt_template: string;
  components: RuntimeComponents;
  policy: GatewayPolicy;
  routes: RuntimeRoute[];
}

interface RuntimeDialogProps {
  /** 是否显示运行配置创建弹窗。 */
  open: boolean;
  /** 可作为运行路由目标的供应商治理视图。 */
  providers: readonly ModelProvider[];
  /** 创建请求是否正在提交。 */
  isSubmitting: boolean;
  /** 关闭弹窗但不创建版本。 */
  onClose: () => void;
  /** 提交完整运行配置快照；服务端会冻结版本与 Hash。 */
  onCreate: (values: CreateAiRuntimeConfigRequest) => Promise<unknown>;
}

const initialComponents: RuntimeComponents = {
  chunking: "recursive-cjk-v1",
  embedding: "deterministic-hash-1024-v1",
  index_schema: "index-v1",
  reranker: "bge-reranker-v1",
  retrieval: "hybrid-rrf-v1",
  source_ranking: "source-priority-v1",
  safety: "rag-safety-v2",
  data_source_interface: "data-source-v1",
  relevance_grader_interface: "relevance-grader-v1",
  multimodal_router_interface: "multimodal-router-v1",
};

const initialPolicy: GatewayPolicy = {
  attempt_timeout_ms: 2_000,
  total_timeout_ms: 8_000,
  max_attempts_per_route: 2,
  max_prompt_characters: 32_000,
  max_output_tokens: 2_048,
  max_response_characters: 64_000,
  circuit_failure_threshold: 3,
  circuit_recovery_ms: 30_000,
  rule_degradation_message: "当前模型暂不可用，请稍后重试",
  max_estimated_cost_microunits: 5_000_000,
};

/**
 * 创建新的不可变运行配置版本。
 *
 * 路由、成本策略与组件版本在提交后由服务端冻结；弹窗关闭只代表本地表单流程结束。
 */
export function RuntimeDialog({
  open,
  providers,
  isSubmitting,
  onClose,
  onCreate,
}: RuntimeDialogProps) {
  const [form] = Form.useForm<RuntimeFormValues>();
  const finish = async () => {
    await onCreate(await form.validateFields());
    form.resetFields();
    onClose();
  };
  const providerOptions = providers
    .filter((provider) => provider.status === "active")
    .map((provider) => ({ value: provider.provider_id, label: provider.display_name }));

  return (
    <Modal
      title="创建不可变运行配置"
      open={open}
      width={920}
      okText="创建版本"
      cancelText="取消"
      confirmLoading={isSubmitting}
      onCancel={onClose}
      onOk={() => void finish().catch(() => undefined)}
    >
      <Form
        form={form}
        layout="vertical"
        requiredMark={false}
        initialValues={{
          components: initialComponents,
          policy: initialPolicy,
          routes: [
            {
              priority: 1,
              capabilities: ["generation", "streaming"],
              input_price_microunits_per_million_tokens: 0,
              output_price_microunits_per_million_tokens: 0,
            },
          ],
        }}
      >
        <div className="grid grid-cols-2 gap-x-4 form-down:grid-cols-1">
          <Form.Item
            label="版本名称"
            name="display_name"
            rules={[{ required: true }, { max: 120 }]}
          >
            <Input autoFocus placeholder="例如：本地 MVP 主备路由" />
          </Form.Item>
          <Form.Item
            className="col-span-full form-down:col-auto"
            label="系统 Prompt 模板"
            name="system_prompt_template"
            rules={[{ required: true }, { max: 16000 }]}
          >
            <Input.TextArea rows={4} placeholder="定义平台助手的稳定系统边界" />
          </Form.Item>
        </div>

        <Form.List name="routes">
          {(fields, { add, remove }) => (
            <section className="mb-4 border-t border-t-solid border-border" aria-label="模型路由">
              <div className="flex min-h-[52px] items-center justify-between gap-3">
                <strong>模型路由</strong>
                <Button
                  type="text"
                  icon={<Plus size={16} />}
                  disabled={fields.length >= 8}
                  onClick={() =>
                    add({
                      priority: fields.length + 1,
                      capabilities: ["generation"],
                      input_price_microunits_per_million_tokens: 0,
                      output_price_microunits_per_million_tokens: 0,
                    })
                  }
                >
                  添加备用路由
                </Button>
              </div>
              {fields.map((field) => (
                <div
                  className="relative grid grid-cols-[1.1fr_1.1fr_88px_1.25fr_1fr_1fr_44px] gap-3 border-t border-t-solid border-border-soft pt-3 compact-down:grid-cols-2 form-down:!grid-cols-1"
                  key={field.key}
                >
                  <Form.Item
                    label="供应商"
                    name={[field.name, "provider_id"]}
                    rules={[{ required: true }]}
                  >
                    <Select options={providerOptions} placeholder="选择已启用供应商" />
                  </Form.Item>
                  <Form.Item
                    label="模型 ID"
                    name={[field.name, "model_id"]}
                    rules={[{ required: true }]}
                  >
                    <Input placeholder="gpt-5-mini" />
                  </Form.Item>
                  <Form.Item
                    label="优先级"
                    name={[field.name, "priority"]}
                    rules={[{ required: true }]}
                  >
                    <InputNumber className="!w-full" min={1} max={8} precision={0} />
                  </Form.Item>
                  <Form.Item
                    label="能力"
                    name={[field.name, "capabilities"]}
                    rules={[{ required: true }]}
                  >
                    <Select
                      mode="multiple"
                      options={[
                        { value: "generation", label: "生成" },
                        { value: "streaming", label: "流式" },
                        { value: "tools", label: "工具" },
                        { value: "structured_output", label: "结构化" },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item
                    label="输入价（微元/百万 Token）"
                    name={[field.name, "input_price_microunits_per_million_tokens"]}
                  >
                    <InputNumber className="!w-full" min={0} precision={0} />
                  </Form.Item>
                  <Form.Item
                    label="输出价（微元/百万 Token）"
                    name={[field.name, "output_price_microunits_per_million_tokens"]}
                  >
                    <InputNumber className="!w-full" min={0} precision={0} />
                  </Form.Item>
                  <Button
                    type="text"
                    danger
                    aria-label="删除模型路由"
                    icon={<Trash2 size={16} />}
                    disabled={fields.length === 1}
                    onClick={() => remove(field.name)}
                  />
                </div>
              ))}
            </section>
          )}
        </Form.List>

        <Collapse
          ghost
          items={[
            {
              key: "policy",
              label: "超时、熔断与成本策略",
              children: (
                <div className="grid grid-cols-2 gap-x-4 form-down:grid-cols-1">
                  {[
                    ["attempt_timeout_ms", "单次超时（ms）"],
                    ["total_timeout_ms", "总超时（ms）"],
                    ["max_attempts_per_route", "每路由最大尝试"],
                    ["max_prompt_characters", "Prompt 最大字符"],
                    ["max_output_tokens", "最大输出 Token"],
                    ["max_response_characters", "响应最大字符"],
                    ["circuit_failure_threshold", "熔断失败阈值"],
                    ["circuit_recovery_ms", "熔断恢复（ms）"],
                    ["max_estimated_cost_microunits", "单次成本上限（微元）"],
                  ].map(([key, label]) => (
                    <Form.Item
                      key={key}
                      label={label}
                      name={["policy", key]}
                      rules={[{ required: true }]}
                    >
                      <InputNumber className="!w-full" min={1} precision={0} />
                    </Form.Item>
                  ))}
                  <Form.Item
                    className="col-span-full form-down:col-auto"
                    label="规则降级文案"
                    name={["policy", "rule_degradation_message"]}
                  >
                    <Input />
                  </Form.Item>
                </div>
              ),
            },
            {
              key: "components",
              label: "知识运行组件版本",
              children: (
                <div className="grid grid-cols-2 gap-x-4 form-down:grid-cols-1">
                  {[
                    ["chunking", "切片"],
                    ["embedding", "Embedding"],
                    ["index_schema", "索引 Schema"],
                    ["reranker", "Reranker"],
                    ["retrieval", "检索"],
                    ["source_ranking", "来源排序"],
                    ["safety", "安全策略"],
                    ["data_source_interface", "DataSource 接口"],
                    ["relevance_grader_interface", "Grader 接口"],
                    ["multimodal_router_interface", "多模态路由接口"],
                  ].map(([key, label]) => (
                    <Form.Item
                      key={key}
                      label={label}
                      name={["components", key]}
                      rules={[{ required: true }]}
                    >
                      <Input />
                    </Form.Item>
                  ))}
                </div>
              ),
            },
          ]}
        />
      </Form>
    </Modal>
  );
}
