/** @description 模型供应商、探测、数据政策和能力标签展示配置。 */
export const providerStatus = {
  draft: { label: "待配置", color: "default" },
  active: { label: "已启用", color: "success" },
  disabled: { label: "已停用", color: "default" },
} as const;

/** 供应商数据保存与外发政策人工复核状态。 */
export const policyStatus = {
  pending: { label: "待审核", color: "warning" },
  approved: { label: "已批准", color: "success" },
  rejected: { label: "已拒绝", color: "error" },
} as const;

/** 固定合成提示能力探测状态。 */
export const probeStatus = {
  not_run: { label: "未探测", color: "default" },
  passed: { label: "探测通过", color: "success" },
  failed: { label: "探测失败", color: "error" },
} as const;

/** 服务端模型能力标识的中文展示名称。 */
export const capabilityLabels = {
  generation: "生成",
  streaming: "流式输出",
  tools: "工具调用",
  structured_output: "结构化输出",
} as const;

/** 使用当前浏览器时区格式化服务端 ISO 时间；空值显示占位符。 */
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
