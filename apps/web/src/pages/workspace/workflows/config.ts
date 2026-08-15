/** @description 工作流节点、运行、审批状态和服务端图校验码展示配置。 */
import type { ApprovalInstance, WorkflowGraph, WorkflowRun } from "@/api/services/workflows";

/** 工作流节点类型。 */
export type WorkflowNodeType = WorkflowGraph["nodes"][number]["node_type"];

/** 六类受限节点的中文名称和用途。 */
export const nodeTypeOptions: Array<{
  value: WorkflowNodeType;
  label: string;
  description: string;
}> = [
  { value: "trigger", label: "触发器", description: "接收一次工作流输入" },
  { value: "condition", label: "条件", description: "按声明式条件选择分支" },
  { value: "knowledge_retrieval", label: "知识检索", description: "检索已授权知识" },
  { value: "model", label: "模型", description: "调用冻结的模型运行配置" },
  { value: "approval", label: "审批", description: "暂停并等待审批链完成" },
  { value: "result", label: "结果", description: "收敛并输出运行结果" },
];

/** 运行状态到中文标签和 Ant Design 颜色的完整映射。 */
export const runStatus = {
  queued: { label: "排队中", color: "default" },
  running: { label: "运行中", color: "processing" },
  waiting_approval: { label: "等待审批", color: "warning" },
  succeeded: { label: "已完成", color: "success" },
  failed: { label: "失败", color: "error" },
  cancelled: { label: "已取消", color: "default" },
} as const satisfies Record<WorkflowRun["status"], { label: string; color: string }>;

/** 审批实例状态到中文标签和 Ant Design 颜色的完整映射。 */
export const approvalStatus = {
  pending: { label: "待审批", color: "processing" },
  approved: { label: "已通过", color: "success" },
  rejected: { label: "已驳回", color: "error" },
  withdrawn: { label: "已撤回", color: "default" },
} as const satisfies Record<ApprovalInstance["status"], { label: string; color: string }>;

/** 服务端稳定图校验码的维护者可读说明。 */
export const graphViolationLabels: Record<string, string> = {
  GRAPH_SCHEMA_VERSION_UNSUPPORTED: "图协议版本不受支持",
  GRAPH_NODE_COUNT_INVALID: "节点数量不符合限制",
  GRAPH_EDGE_COUNT_EXCEEDED: "连线数量超过限制",
  GRAPH_NODE_ID_INVALID: "节点 ID 格式无效",
  GRAPH_NODE_ID_DUPLICATE: "节点 ID 重复",
  GRAPH_NODE_TYPE_INVALID: "节点类型不受支持",
  GRAPH_NODE_NAME_INVALID: "节点名称无效",
  GRAPH_EDGE_ID_INVALID: "连线 ID 格式无效",
  GRAPH_EDGE_ID_DUPLICATE: "连线 ID 重复",
  GRAPH_EDGE_DUPLICATE: "存在重复连线",
  GRAPH_EDGE_DANGLING: "连线引用了不存在的节点",
  GRAPH_EDGE_SELF_REFERENCE: "节点不能连接到自身",
  GRAPH_ENTRY_NODE_MISSING: "入口节点不存在",
  GRAPH_ENTRY_NOT_TRIGGER: "入口必须是触发器",
  GRAPH_TRIGGER_COUNT_INVALID: "必须且只能有一个触发器",
  GRAPH_TRIGGER_HAS_INCOMING: "触发器不能有入边",
  GRAPH_RESULT_MISSING: "至少需要一个结果节点",
  GRAPH_RESULT_HAS_OUTGOING: "结果节点不能有出边",
  GRAPH_CYCLE: "图中存在循环",
  GRAPH_NODE_UNREACHABLE: "存在入口不可达节点",
  GRAPH_NODE_WITHOUT_RESULT_PATH: "存在无法到达结果的路径",
};

/** 使用当前浏览器时区格式化工作流与审批事实时间。 */
export function formatWorkflowTime(value: string | null) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

/** 创建一个可直接校验和发布的最小触发器到结果图。 */
export function createStarterGraph(): WorkflowGraph {
  return {
    schema_version: 1,
    entry_node_id: "trigger-1",
    nodes: [
      { node_id: "trigger-1", node_type: "trigger", name: "开始", config: {} },
      { node_id: "result-1", node_type: "result", name: "完成", config: {} },
    ],
    edges: [
      {
        edge_id: "edge-1",
        source_node_id: "trigger-1",
        target_node_id: "result-1",
        condition_key: null,
      },
    ],
  };
}
