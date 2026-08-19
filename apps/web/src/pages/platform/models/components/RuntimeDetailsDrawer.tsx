/** @description 以低敏只读视图展示不可变运行配置、模型路由和冻结策略。 */
import { Alert, Descriptions, Drawer, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";

import type { AiRuntimeConfig, ModelProvider } from "@/api/services/platformModels";

type RuntimeRoute = AiRuntimeConfig["routes"][number];
type RuntimeCapability = RuntimeRoute["capabilities"][number];

interface RuntimeDetailsDrawerProps {
  /** 当前查看的不可变配置；为空时关闭详情。 */
  runtime: AiRuntimeConfig | null;
  /** 用于把路由中的稳定供应商标识映射为管理员可识别名称。 */
  providers: readonly ModelProvider[];
  /** 关闭详情但不改变配置或发布状态。 */
  onClose: () => void;
}

const capabilityLabels: Record<RuntimeCapability, string> = {
  generation: "生成",
  streaming: "流式输出",
  tools: "工具",
  structured_output: "结构化输出",
};

const policyLabels = [
  ["attempt_timeout_ms", "单次超时", "ms"],
  ["total_timeout_ms", "总超时", "ms"],
  ["max_attempts_per_route", "每路由最大尝试", "次"],
  ["max_prompt_characters", "Prompt 最大字符", "字符"],
  ["max_output_tokens", "最大输出 Token", "Token"],
  ["max_response_characters", "响应最大字符", "字符"],
  ["circuit_failure_threshold", "熔断失败阈值", "次"],
  ["circuit_recovery_ms", "熔断恢复", "ms"],
  ["max_estimated_cost_microunits", "单次成本上限", "微元"],
] as const;

const componentLabels = [
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
] as const;

function digest(value: string) {
  return <Typography.Text className="break-all font-mono text-xs">{value}</Typography.Text>;
}

/** 展示可用于验收截图的配置摘要，同时避免再次暴露系统 Prompt 正文。 */
export function RuntimeDetailsDrawer({ runtime, providers, onClose }: RuntimeDetailsDrawerProps) {
  const providerById = new Map(providers.map((provider) => [provider.provider_id, provider]));
  const hasZeroPrice = runtime?.routes.some(
    (route) =>
      route.input_price_microunits_per_million_tokens === 0 ||
      route.output_price_microunits_per_million_tokens === 0,
  );
  const routeColumns: TableColumnsType<RuntimeRoute> = [
    {
      title: "优先级",
      dataIndex: "priority",
      width: 82,
    },
    {
      title: "供应商",
      key: "provider",
      render: (_, route) => {
        const provider = providerById.get(route.provider_id);
        return (
          <div className="grid min-w-0 gap-[2px]">
            <strong>{provider?.display_name ?? "未知供应商"}</strong>
            <Typography.Text type="secondary" className="text-xs">
              {provider?.provider_key ?? route.provider_id}
            </Typography.Text>
          </div>
        );
      },
    },
    {
      title: "模型",
      dataIndex: "model_id",
      minWidth: 150,
    },
    {
      title: "能力",
      dataIndex: "capabilities",
      minWidth: 160,
      render: (capabilities: RuntimeCapability[]) => (
        <div className="flex flex-wrap gap-1">
          {capabilities.map((capability) => (
            <Tag key={capability}>{capabilityLabels[capability]}</Tag>
          ))}
        </div>
      ),
    },
    {
      title: "价格（微元/百万 Token）",
      key: "price",
      minWidth: 210,
      render: (_, route) => (
        <span>
          输入 {route.input_price_microunits_per_million_tokens.toLocaleString("zh-CN")} / 输出{" "}
          {route.output_price_microunits_per_million_tokens.toLocaleString("zh-CN")}{" "}
          {route.currency}
        </span>
      ),
    },
  ];

  return (
    <Drawer title="运行配置详情" open={runtime !== null} size="large" onClose={onClose}>
      {runtime && (
        <div className="grid gap-5">
          <Descriptions title="不可变身份" size="small" bordered column={{ xs: 1, sm: 2 }}>
            <Descriptions.Item label="版本名称">{runtime.display_name}</Descriptions.Item>
            <Descriptions.Item label="版本号">
              <Tag color="success">V{runtime.version_number}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="配置 ID" span={2}>
              {digest(runtime.runtime_config_version_id)}
            </Descriptions.Item>
            <Descriptions.Item label="内容摘要" span={2}>
              {digest(runtime.content_hash)}
            </Descriptions.Item>
            <Descriptions.Item label="系统 Prompt 摘要" span={2}>
              {digest(runtime.system_prompt_hash)}
            </Descriptions.Item>
          </Descriptions>

          {hasZeroPrice && (
            <Alert
              type="warning"
              showIcon
              message="存在价格为 0 的路由"
              description="该配置可以用于质量验收，但不能作为真实价格、成本或账单验收证据。"
            />
          )}

          <section aria-label="模型路由">
            <Typography.Title level={5}>模型路由</Typography.Title>
            <Table<RuntimeRoute>
              rowKey="route_id"
              columns={routeColumns}
              dataSource={[...runtime.routes].sort((left, right) => left.priority - right.priority)}
              pagination={false}
              size="small"
              scroll={{ x: 760 }}
            />
          </section>

          <Descriptions title="网关策略" size="small" bordered column={{ xs: 1, sm: 2 }}>
            {policyLabels.map(([key, label, unit]) => (
              <Descriptions.Item key={key} label={label}>
                {runtime.policy[key].toLocaleString("zh-CN")} {unit}
              </Descriptions.Item>
            ))}
            <Descriptions.Item label="规则降级文案" span={2}>
              {runtime.policy.rule_degradation_message ?? "未配置"}
            </Descriptions.Item>
          </Descriptions>

          <Descriptions title="知识运行组件" size="small" bordered column={{ xs: 1, sm: 2 }}>
            {componentLabels.map(([key, label]) => (
              <Descriptions.Item key={key} label={label}>
                {runtime.components[key]}
              </Descriptions.Item>
            ))}
          </Descriptions>
        </div>
      )}
    </Drawer>
  );
}
