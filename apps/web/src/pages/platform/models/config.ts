export const providerStatus = {
  draft: { label: "待配置", color: "default" },
  active: { label: "已启用", color: "success" },
  disabled: { label: "已停用", color: "default" },
} as const;

export const policyStatus = {
  pending: { label: "待审核", color: "warning" },
  approved: { label: "已批准", color: "success" },
  rejected: { label: "已拒绝", color: "error" },
} as const;

export const probeStatus = {
  not_run: { label: "未探测", color: "default" },
  passed: { label: "探测通过", color: "success" },
  failed: { label: "探测失败", color: "error" },
} as const;

export const capabilityLabels = {
  generation: "生成",
  streaming: "流式输出",
  tools: "工具调用",
  structured_output: "结构化输出",
} as const;

export function formatTimestamp(value: string | null) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
