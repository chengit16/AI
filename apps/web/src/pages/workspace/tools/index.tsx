/** @description 工具目录、参数编排与冻结执行计划页面。 */
import { Alert, Button, Form, Input, InputNumber, Select, Tag, Typography } from "antd";
import { ArrowRight, Play, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router";

import type { ToolCatalogItem } from "@/api/services/tools";
import type { ServiceDeployment } from "@/api/services/services";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";

import { useToolExecutionConsole } from "./useToolExecutionConsole";

interface JsonSchemaProperty {
  type?: string | readonly string[];
  format?: string;
  enum?: readonly string[];
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  maxItems?: number;
  items?: { type?: string; format?: string; enum?: readonly string[] };
}

interface PlannedToolCall {
  tool_key: string;
  tool_id: string;
  tool_version: number;
  arguments: Record<string, unknown>;
  displayName: string;
}

/** 提供工具选择、Route Release 约束和当前计划提交入口。 */
export default function ToolExecutionConsolePage() {
  const navigate = useNavigate();
  const model = useToolExecutionConsole();
  const { visiblePermissionCodes } = useWorkspaceMenuNavigation();
  const [selectedToolId, setSelectedToolId] = useState<string | null>(null);
  const [selectedServiceId, setSelectedServiceId] = useState<string | null>(null);
  const [selectedReleaseId, setSelectedReleaseId] = useState<string | null>(null);
  const [calls, setCalls] = useState<PlannedToolCall[]>([]);
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [maxSeconds, setMaxSeconds] = useState(300);
  const tools = model.tools.isError ? [] : (model.tools.data ?? []);
  const services = model.services.isError ? [] : (model.services.data ?? []);
  const selectedTool = tools.find((item) => item.tool_id === selectedToolId) ?? tools[0] ?? null;
  const selectedService =
    services.find((item) => item.service.service_id === selectedServiceId) ?? services[0] ?? null;
  const releaseOptions = useMemo(() => serviceReleaseOptions(selectedService), [selectedService]);
  const effectiveReleaseId = releaseOptions.some((item) => item.value === selectedReleaseId)
    ? selectedReleaseId
    : (releaseOptions[0]?.value ?? null);

  if (model.tools.isError || model.services.isError) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="工具执行页面未能加载"
        description="工具目录或服务 Route 未能从服务端同步，旧计划已清空。"
        action={
          <Button
            onClick={() => void Promise.all([model.tools.refetch(), model.services.refetch()])}
          >
            重新加载
          </Button>
        }
      />
    );
  }

  const canCreate = visiblePermissionCodes.has("tool.run.create");
  return (
    <>
      <PageHeader
        eyebrow="CONTROLLED TOOLING"
        title="工具执行"
        description="从当前授权目录选择工具，以服务 Route 的不可变 Release 冻结执行计划。"
      />
      <div className="ui-surface-panel grid min-h-[660px] grid-cols-[minmax(210px,260px)_minmax(0,1fr)_minmax(250px,320px)] overflow-hidden tablet-down:grid-cols-1">
        <ToolCatalogRail
          tools={tools}
          selectedId={selectedTool?.tool_id ?? null}
          onSelect={setSelectedToolId}
        />
        <main className="min-w-0 border-x border-solid border-[var(--ui-border)] p-5 tablet-down:border-x-0 tablet-down:border-y phone-down:p-3">
          {selectedTool ? (
            <ToolArgumentEditor
              key={`${selectedTool.tool_id}:${selectedTool.tool_version}`}
              tool={selectedTool}
              canAdd={canCreate}
              onAdd={(argumentsValue) =>
                setCalls((current) => [
                  ...current,
                  {
                    tool_key: selectedTool.tool_key,
                    tool_id: selectedTool.tool_id,
                    tool_version: selectedTool.tool_version,
                    arguments: argumentsValue,
                    displayName: selectedTool.display_name,
                  },
                ])
              }
            />
          ) : (
            <StateView
              kind="empty"
              title="当前没有可执行工具"
              description="目录只显示套餐、注册状态与当前权限的交集。"
            />
          )}
        </main>
        <ToolPlanPanel
          calls={calls}
          services={services}
          selectedServiceId={selectedService?.service.service_id ?? null}
          selectedReleaseId={effectiveReleaseId}
          releaseOptions={releaseOptions}
          maxAttempts={maxAttempts}
          maxSeconds={maxSeconds}
          canCreate={canCreate}
          isCreating={model.create.isPending}
          onServiceChange={(serviceId) => {
            setSelectedServiceId(serviceId);
            setSelectedReleaseId(null);
          }}
          onReleaseChange={setSelectedReleaseId}
          onAttemptsChange={setMaxAttempts}
          onSecondsChange={setMaxSeconds}
          onRemove={(index) =>
            setCalls((current) => current.filter((_, itemIndex) => itemIndex !== index))
          }
          onCreate={async () => {
            if (!selectedService || !effectiveReleaseId || !calls.length) return;
            const detail = await model.create.mutateAsync({
              service_id: selectedService.service.service_id,
              agent_release_id: effectiveReleaseId,
              tool_calls: calls.map((call) => ({
                tool_key: call.tool_key,
                tool_id: call.tool_id,
                tool_version: call.tool_version,
                arguments: call.arguments,
              })),
              max_attempts_per_step: maxAttempts,
              max_execution_seconds: maxSeconds,
            });
            navigate(`/workspace/tool-runs?run=${detail.run.run_id}`);
          }}
        />
      </div>
    </>
  );
}

function ToolCatalogRail(props: {
  tools: readonly ToolCatalogItem[];
  selectedId: string | null;
  onSelect: (toolId: string) => void;
}) {
  return (
    <aside className="min-w-0 p-3" aria-label="可用工具目录">
      <Typography.Text className="px-2 text-xs" type="secondary">
        可用目录
      </Typography.Text>
      <div className="mt-2 grid gap-1">
        {props.tools.map((tool) => (
          <button
            key={`${tool.tool_id}:${tool.tool_version}`}
            type="button"
            className={`w-full border-0 bg-transparent p-3 text-left transition-colors ${props.selectedId === tool.tool_id ? "bg-[var(--ui-fill-secondary)]" : "hover:bg-[var(--ui-fill-tertiary)]"}`}
            onClick={() => props.onSelect(tool.tool_id)}
          >
            <span className="block font-medium">{tool.display_name}</span>
            <span className="mt-1 block break-all text-xs text-[var(--ui-text-secondary)]">
              {tool.tool_key} · V{tool.tool_version}
            </span>
          </button>
        ))}
      </div>
    </aside>
  );
}

function ToolArgumentEditor(props: {
  tool: ToolCatalogItem;
  canAdd: boolean;
  onAdd: (value: Record<string, unknown>) => void;
}) {
  const [form] = Form.useForm<Record<string, unknown>>();
  const schema = props.tool.input_schema as {
    required?: readonly string[];
    properties?: Record<string, JsonSchemaProperty>;
  };
  const properties = Object.entries(schema.properties ?? {});
  return (
    <section aria-label="工具参数">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Typography.Title level={3} className="!mb-1 !text-lg">
            {props.tool.display_name}
          </Typography.Title>
          <Typography.Paragraph type="secondary">{props.tool.description}</Typography.Paragraph>
        </div>
        <div className="flex gap-2">
          <Tag>{props.tool.access_mode === "read" ? "只读" : "写入"}</Tag>
          <Tag color={props.tool.risk_level === "low" ? "green" : "gold"}>
            {props.tool.risk_level}
          </Tag>
        </div>
      </div>
      <Alert
        className="mb-5"
        type="info"
        showIcon
        message={`超时 ${props.tool.timeout_seconds} 秒 · ${props.tool.retry_mode === "safe_read" ? "仅安全只读重试" : "不自动重试"}`}
      />
      <Form
        form={form}
        layout="vertical"
        onFinish={(values) => {
          props.onAdd(
            Object.fromEntries(
              Object.entries(values).filter(([, value]) => value !== undefined && value !== ""),
            ),
          );
          form.resetFields();
        }}
      >
        {properties.map(([name, property]) => (
          <SchemaField
            key={name}
            name={name}
            property={property}
            required={schema.required?.includes(name) ?? false}
          />
        ))}
        {!properties.length && (
          <Typography.Paragraph type="secondary">此工具不需要参数。</Typography.Paragraph>
        )}
        {props.canAdd && (
          <Button htmlType="submit" icon={<Plus size={16} />}>
            加入计划
          </Button>
        )}
      </Form>
    </section>
  );
}

function SchemaField(props: { name: string; property: JsonSchemaProperty; required: boolean }) {
  const label = fieldLabel(props.name);
  const rules = [{ required: props.required, message: `请填写${label}` }];
  if (props.property.type === "integer") {
    return (
      <Form.Item name={props.name} label={label} rules={rules}>
        <InputNumber
          className="!w-full"
          min={props.property.minimum}
          max={props.property.maximum}
        />
      </Form.Item>
    );
  }
  if (props.property.type === "array") {
    return (
      <Form.Item name={props.name} label={label} rules={rules}>
        <Select
          mode="tags"
          tokenSeparators={[","]}
          maxCount={props.property.maxItems}
          options={props.property.items?.enum?.map((value) => ({ value, label: value }))}
        />
      </Form.Item>
    );
  }
  if (props.property.enum) {
    return (
      <Form.Item name={props.name} label={label} rules={rules}>
        <Select options={props.property.enum.map((value) => ({ value, label: value }))} />
      </Form.Item>
    );
  }
  return (
    <Form.Item name={props.name} label={label} rules={rules}>
      <Input
        maxLength={props.property.maxLength}
        placeholder={props.property.format === "uuid" ? "UUID" : undefined}
      />
    </Form.Item>
  );
}

function ToolPlanPanel(props: {
  calls: PlannedToolCall[];
  services: readonly ServiceDeployment[];
  selectedServiceId: string | null;
  selectedReleaseId: string | null;
  releaseOptions: Array<{ value: string; label: string }>;
  maxAttempts: number;
  maxSeconds: number;
  canCreate: boolean;
  isCreating: boolean;
  onServiceChange: (value: string) => void;
  onReleaseChange: (value: string) => void;
  onAttemptsChange: (value: number) => void;
  onSecondsChange: (value: number) => void;
  onRemove: (index: number) => void;
  onCreate: () => void;
}) {
  return (
    <aside className="min-w-0 p-4" aria-label="当前执行计划">
      <Typography.Title level={3} className="!text-base">
        执行计划
      </Typography.Title>
      <label className="mb-1 block text-xs text-[var(--ui-text-secondary)]">服务</label>
      <Select
        className="mb-3 w-full"
        value={props.selectedServiceId}
        onChange={props.onServiceChange}
        options={props.services.map((item) => ({
          value: item.service.service_id,
          label: item.service.name,
        }))}
      />
      <label className="mb-1 block text-xs text-[var(--ui-text-secondary)]">不可变 Release</label>
      <Select
        className="mb-4 w-full"
        value={props.selectedReleaseId}
        onChange={props.onReleaseChange}
        options={props.releaseOptions}
      />
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs text-[var(--ui-text-secondary)]">单步尝试</label>
          <InputNumber
            className="!w-full"
            min={1}
            max={5}
            value={props.maxAttempts}
            onChange={(value) => props.onAttemptsChange(value ?? 1)}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-[var(--ui-text-secondary)]">总时限（秒）</label>
          <InputNumber
            className="!w-full"
            min={1}
            max={1800}
            value={props.maxSeconds}
            onChange={(value) => props.onSecondsChange(value ?? 300)}
          />
        </div>
      </div>
      <div className="my-4 border-t border-solid border-[var(--ui-border)] pt-3">
        {props.calls.length ? (
          props.calls.map((call, index) => (
            <div
              key={`${call.tool_id}:${index}`}
              className="flex items-center gap-2 border-b border-solid border-[var(--ui-border)] py-2"
            >
              <span className="grid size-6 shrink-0 place-items-center bg-[var(--ui-fill-secondary)] text-xs">
                {index + 1}
              </span>
              <span className="min-w-0 flex-1 truncate text-sm">{call.displayName}</span>
              <Button
                type="text"
                size="small"
                aria-label={`移除 ${call.displayName}`}
                icon={<Trash2 size={15} />}
                onClick={() => props.onRemove(index)}
              />
            </div>
          ))
        ) : (
          <Typography.Paragraph type="secondary">从目录加入一个或多个工具。</Typography.Paragraph>
        )}
      </div>
      {props.canCreate ? (
        <Button
          type="primary"
          block
          icon={<Play size={16} />}
          loading={props.isCreating}
          disabled={!props.calls.length || !props.selectedReleaseId}
          onClick={props.onCreate}
        >
          冻结并执行 <ArrowRight size={15} />
        </Button>
      ) : (
        <Alert type="warning" showIcon message="当前角色不能创建工具任务" />
      )}
    </aside>
  );
}

function serviceReleaseOptions(service: ServiceDeployment | null) {
  if (!service) return [];
  const values = [
    {
      value: service.route.primary_release_id,
      label: `主版本 · ${service.route.primary_release_id.slice(0, 8)}`,
    },
  ];
  if (
    service.route.canary_release_id &&
    service.route.canary_release_id !== service.route.primary_release_id
  ) {
    values.push({
      value: service.route.canary_release_id,
      label: `灰度版本 · ${service.route.canary_release_id.slice(0, 8)}`,
    });
  }
  return values;
}

function fieldLabel(name: string) {
  const labels: Record<string, string> = {
    query: "检索问题",
    knowledge_base_ids: "知识库 ID",
    limit: "返回条数",
    document_id: "文档 ID",
    start_sequence: "起始片段序号",
    end_sequence: "结束片段序号",
    workflow_run_id: "工作流 Run ID",
    approval_instance_id: "审批实例 ID",
    metrics: "用量指标",
  };
  return labels[name] ?? name;
}
