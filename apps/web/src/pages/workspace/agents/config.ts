/** @description Agent 控制台状态、时间和 JSON 配置的纯前端展示规则。 */
import type { AgentCandidateControl } from "@/api/services/agents";

/** 候选状态对应的中文标签和 Ant Design Tag 颜色。 */
export const candidateStatus = {
  created: { label: "待测试", color: "default" },
  testing: { label: "测试中", color: "processing" },
  test_failed: { label: "测试未通过", color: "error" },
  ready_for_approval: { label: "待发起审批", color: "warning" },
  approval_pending: { label: "审批中", color: "processing" },
  approved: { label: "待发布", color: "success" },
  rejected: { label: "审批驳回", color: "error" },
  released: { label: "已发布", color: "success" },
  superseded: { label: "已失效", color: "default" },
} as const;

/** 五类固定测试代码的稳定中文名称。 */
export const evaluationCheckLabel: Record<string, string> = {
  functional: "核心功能",
  authorization: "越权防护",
  prompt_injection: "提示注入",
  citation: "引用完整性",
  output_contract: "输出契约",
};

/** 将服务端时间格式化为当前浏览器可读时间。 */
export function formatAgentTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

/** 把草稿对象转换为便于审阅和编辑的稳定缩进 JSON。 */
export function formatConfiguration(configuration: Record<string, unknown>) {
  return JSON.stringify(configuration, null, 2);
}

/**
 * 解析完整 Agent 配置，并拒绝数组、null 和其他非对象 JSON。
 *
 * 前端校验只提供即时反馈；未知字段、资源版本和权限仍由后端严格复核。
 */
export function parseConfiguration(value: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

/** 返回候选当前可执行的下一步，不跳过测试或审批门禁。 */
export function candidateNextAction(item: AgentCandidateControl) {
  if (["created", "test_failed"].includes(item.candidate.status)) return "evaluate";
  if (item.candidate.status === "ready_for_approval") return "approve";
  if (item.candidate.status === "approved") return "publish";
  return null;
}
