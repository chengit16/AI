/**
 * @description 工作流结构化图编辑器
 *
 * 以可键盘操作的节点清单和连线规则编辑冻结图契约，不执行节点、不预测服务端校验结果。
 */
import { Alert, Button, Input, Select, Tag, Tooltip } from "antd";
import { GitBranch, Plus, Save, Send, ShieldCheck, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import type { WorkflowDetail, WorkflowGraph } from "@/api/services/workflows";

import { graphViolationLabels, nodeTypeOptions, type WorkflowNodeType } from "../config";

type WorkflowNode = WorkflowGraph["nodes"][number];
type WorkflowEdge = WorkflowGraph["edges"][number];

/** 图编辑器依赖的当前草稿、权限和写操作。 */
export interface WorkflowGraphEditorProps {
  /** 服务端返回的定义、草稿和发布指针。 */
  detail: WorkflowDetail;
  /** 是否允许替换草稿。 */
  canUpdate: boolean;
  /** 是否允许冻结并发布有效草稿。 */
  canPublish: boolean;
  /** 任一草稿或发布写操作进行中。 */
  isMutating: boolean;
  /** 保存当前本地图和服务端修订号。 */
  onSave: (graph: WorkflowGraph, revision: number) => void;
  /** 请求服务端重新校验当前已保存草稿。 */
  onValidate: () => void;
  /** 发布当前服务端草稿修订。 */
  onPublish: (revision: number) => void;
}

interface NodeEditorProps {
  /** 当前节点的稳定 ID、类型、名称和配置。 */
  node: WorkflowNode;
  /** 是否为整张图的唯一入口节点。 */
  isEntry: boolean;
  /** 是否允许修改节点和入口。 */
  canEdit: boolean;
  /** 尚未提交到图对象的配置 JSON 文本。 */
  configText: string;
  /** 配置文本是否不能解析为 JSON 对象。 */
  configError: boolean;
  /** 将当前节点设为入口。 */
  onEntry: () => void;
  /** 更新节点公开字段。 */
  onChange: (node: WorkflowNode) => void;
  /** 更新本地配置文本。 */
  onConfigText: (value: string) => void;
  /** 删除节点和引用它的连线。 */
  onRemove: () => void;
}

/** 编辑单个节点的类型、名称、声明式配置和入口身份。 */
function NodeEditor(props: NodeEditorProps) {
  const option = nodeTypeOptions.find((item) => item.value === props.node.node_type);
  return (
    <article className="rounded-ui border border-solid border-border-soft bg-surface px-4 py-3">
      <div className="flex items-start justify-between gap-3 phone-down:flex-col">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Tag color={props.isEntry ? "green" : "default"}>{props.isEntry ? "入口" : "节点"}</Tag>
            <code className="truncate text-xs text-text-muted">{props.node.node_id}</code>
          </div>
          <p className="mb-0 mt-2 text-xs text-text-muted">{option?.description}</p>
        </div>
        <div className="flex gap-2">
          {!props.isEntry && props.canEdit && (
            <Button size="small" onClick={props.onEntry}>
              设为入口
            </Button>
          )}
          {props.canEdit && (
            <Tooltip title="删除节点">
              <Button
                danger
                type="text"
                size="small"
                icon={<Trash2 size={16} />}
                aria-label={`删除节点 ${props.node.name}`}
                onClick={props.onRemove}
              />
            </Tooltip>
          )}
        </div>
      </div>
      <div className="mt-3 grid grid-cols-[minmax(150px,0.7fr)_minmax(180px,1fr)] gap-3 phone-down:grid-cols-1">
        <label className="text-xs font-650 text-text-muted">
          节点类型
          <Select
            className="mt-1 w-full"
            value={props.node.node_type}
            disabled={!props.canEdit}
            options={nodeTypeOptions.map(({ value, label }) => ({ value, label }))}
            onChange={(nodeType: WorkflowNodeType) =>
              props.onChange({ ...props.node, node_type: nodeType })
            }
          />
        </label>
        <label className="text-xs font-650 text-text-muted">
          节点名称
          <Input
            className="mt-1"
            value={props.node.name}
            disabled={!props.canEdit}
            maxLength={120}
            onChange={(event) => props.onChange({ ...props.node, name: event.target.value })}
          />
        </label>
      </div>
      <label className="mt-3 block text-xs font-650 text-text-muted">
        声明式配置 JSON
        <Input.TextArea
          className="mt-1 font-mono"
          value={props.configText}
          disabled={!props.canEdit}
          rows={3}
          status={props.configError ? "error" : undefined}
          aria-invalid={props.configError}
          onChange={(event) => props.onConfigText(event.target.value)}
        />
      </label>
    </article>
  );
}

interface EdgeEditorProps {
  /** 当前有向边及其可选分支条件。 */
  edge: WorkflowEdge;
  /** 供起点和终点选择使用的当前节点集合。 */
  nodes: readonly WorkflowNode[];
  /** 是否允许修改或删除连线。 */
  canEdit: boolean;
  /** 更新连线字段。 */
  onChange: (edge: WorkflowEdge) => void;
  /** 删除当前连线。 */
  onRemove: () => void;
}

/** 编辑一条有向边和可选条件分支键。 */
function EdgeEditor(props: EdgeEditorProps) {
  const options = props.nodes.map((node) => ({ value: node.node_id, label: node.name }));
  return (
    <div className="grid grid-cols-[minmax(130px,1fr)_24px_minmax(130px,1fr)_minmax(120px,0.8fr)_44px] items-center gap-2 compact-down:grid-cols-1">
      <Select
        value={props.edge.source_node_id}
        disabled={!props.canEdit}
        options={options}
        aria-label="连线起点"
        onChange={(value) => props.onChange({ ...props.edge, source_node_id: value })}
      />
      <GitBranch className="text-text-muted compact-down:hidden" size={17} />
      <Select
        value={props.edge.target_node_id}
        disabled={!props.canEdit}
        options={options}
        aria-label="连线终点"
        onChange={(value) => props.onChange({ ...props.edge, target_node_id: value })}
      />
      <Input
        value={props.edge.condition_key ?? ""}
        disabled={!props.canEdit}
        placeholder="条件键（可选）"
        aria-label="连线条件键"
        onChange={(event) =>
          props.onChange({ ...props.edge, condition_key: event.target.value || null })
        }
      />
      {props.canEdit && (
        <Tooltip title="删除连线">
          <Button
            danger
            type="text"
            icon={<Trash2 size={16} />}
            aria-label={`删除连线 ${props.edge.edge_id}`}
            onClick={props.onRemove}
          />
        </Tooltip>
      )}
    </div>
  );
}

/** 组合节点、连线、校验和发布动作，并保持未保存本地编辑与服务端事实分离。 */
export function WorkflowGraphEditor(props: WorkflowGraphEditorProps) {
  const [graph, setGraph] = useState<WorkflowGraph>(props.detail.draft.graph);
  const [configTexts, setConfigTexts] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      props.detail.draft.graph.nodes.map((node) => [
        node.node_id,
        JSON.stringify(node.config ?? {}, null, 2),
      ]),
    ),
  );

  // 本地 JSON 可以暂时无效，但在保存前必须全部解析为对象，不能把字符串伪装成节点配置。
  const configErrors = useMemo(
    () =>
      new Set(
        graph.nodes.flatMap((node) => {
          try {
            const value: unknown = JSON.parse(configTexts[node.node_id] ?? "{}");
            return value && typeof value === "object" && !Array.isArray(value)
              ? []
              : [node.node_id];
          } catch {
            return [node.node_id];
          }
        }),
      ),
    [configTexts, graph.nodes],
  );

  const changeNode = (node: WorkflowNode) =>
    setGraph({
      ...graph,
      nodes: graph.nodes.map((item) => (item.node_id === node.node_id ? node : item)),
    });
  const removeNode = (nodeId: string) =>
    setGraph({
      ...graph,
      nodes: graph.nodes.filter((item) => item.node_id !== nodeId),
      edges: graph.edges.filter(
        (edge) => edge.source_node_id !== nodeId && edge.target_node_id !== nodeId,
      ),
    });
  const addNode = () => {
    const id = `node-${crypto.randomUUID().slice(0, 8)}`;
    setGraph({
      ...graph,
      nodes: [...graph.nodes, { node_id: id, node_type: "condition", name: "新节点", config: {} }],
    });
    setConfigTexts((current) => ({ ...current, [id]: "{}" }));
  };
  const changeEdge = (edge: WorkflowEdge) =>
    setGraph({
      ...graph,
      edges: graph.edges.map((item) => (item.edge_id === edge.edge_id ? edge : item)),
    });
  const addEdge = () => {
    const first = graph.nodes[0]?.node_id ?? "";
    const last = graph.nodes.at(-1)?.node_id ?? "";
    setGraph({
      ...graph,
      edges: [
        ...graph.edges,
        {
          edge_id: `edge-${crypto.randomUUID().slice(0, 8)}`,
          source_node_id: first,
          target_node_id: last,
          condition_key: null,
        },
      ],
    });
  };
  const save = () => {
    if (configErrors.size) return;
    props.onSave(
      {
        ...graph,
        nodes: graph.nodes.map((node) => ({
          ...node,
          config: JSON.parse(configTexts[node.node_id] ?? "{}") as Record<string, unknown>,
        })),
      },
      props.detail.draft.revision,
    );
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3 border-0 border-b border-solid border-border-soft pb-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="m-0 text-lg text-text-strong">{props.detail.workflow.name}</h2>
            <Tag>草稿 r{props.detail.draft.revision}</Tag>
            {props.detail.publication && (
              <Tag color="green">发布代次 {props.detail.publication.generation}</Tag>
            )}
          </div>
          <p className="mb-0 mt-2 text-sm text-text-muted">
            {props.detail.workflow.description || "未填写工作流说明"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {props.canUpdate && (
            <>
              <Button
                icon={<ShieldCheck size={16} />}
                loading={props.isMutating}
                onClick={props.onValidate}
              >
                校验
              </Button>
              <Button
                icon={<Save size={16} />}
                loading={props.isMutating}
                disabled={configErrors.size > 0}
                onClick={save}
              >
                保存草稿
              </Button>
            </>
          )}
          {props.canPublish && (
            <Button
              type="primary"
              icon={<Send size={16} />}
              loading={props.isMutating}
              disabled={props.detail.draft.validation_errors.length > 0}
              onClick={() => props.onPublish(props.detail.draft.revision)}
            >
              发布
            </Button>
          )}
        </div>
      </div>

      {props.detail.draft.validation_errors.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message={`草稿有 ${props.detail.draft.validation_errors.length} 项校验问题`}
          description={props.detail.draft.validation_errors
            .map((item) => graphViolationLabels[item.code] ?? item.code)
            .join("；")}
        />
      )}
      {configErrors.size > 0 && (
        <Alert type="error" showIcon message="节点配置必须是有效 JSON 对象后才能保存" />
      )}

      <section aria-labelledby="workflow-nodes-title">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h3 id="workflow-nodes-title" className="m-0 text-sm text-text-strong">
            节点 {graph.nodes.length}/100
          </h3>
          {props.canUpdate && (
            <Button icon={<Plus size={16} />} onClick={addNode}>
              添加节点
            </Button>
          )}
        </div>
        <div className="grid grid-cols-2 gap-3 desktop-down:grid-cols-1">
          {graph.nodes.map((node) => (
            <NodeEditor
              key={node.node_id}
              node={node}
              isEntry={graph.entry_node_id === node.node_id}
              canEdit={props.canUpdate}
              configText={configTexts[node.node_id] ?? "{}"}
              configError={configErrors.has(node.node_id)}
              onEntry={() => setGraph({ ...graph, entry_node_id: node.node_id })}
              onChange={changeNode}
              onConfigText={(value) =>
                setConfigTexts((current) => ({ ...current, [node.node_id]: value }))
              }
              onRemove={() => removeNode(node.node_id)}
            />
          ))}
        </div>
      </section>

      <section aria-labelledby="workflow-edges-title">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h3 id="workflow-edges-title" className="m-0 text-sm text-text-strong">
            连线 {graph.edges.length}/200
          </h3>
          {props.canUpdate && (
            <Button icon={<Plus size={16} />} onClick={addEdge}>
              添加连线
            </Button>
          )}
        </div>
        <div className="flex flex-col gap-2 rounded-ui border border-solid border-border-soft bg-surface-subtle p-3">
          {graph.edges.map((edge) => (
            <EdgeEditor
              key={edge.edge_id}
              edge={edge}
              nodes={graph.nodes}
              canEdit={props.canUpdate}
              onChange={changeEdge}
              onRemove={() =>
                setGraph({
                  ...graph,
                  edges: graph.edges.filter((item) => item.edge_id !== edge.edge_id),
                })
              }
            />
          ))}
          {graph.edges.length === 0 && (
            <p className="m-0 py-4 text-center text-sm text-text-muted">尚未配置连线</p>
          )}
        </div>
      </section>
    </div>
  );
}
