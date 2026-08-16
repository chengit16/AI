/** @description Release 运营页面的稳定状态名称与指标格式化规则。 */

/** 页面允许查询的固定运营窗口，避免任意窗口造成缓存和数据库压力失控。 */
export const operationsWindowOptions = [24, 48, 168] as const;
/** 运营接口允许的固定小时窗口。 */
export type OperationsWindowHours = (typeof operationsWindowOptions)[number];

/** 晋级结论的中文状态、颜色和可执行语义。 */
export const promotionStatus = {
  passed: { label: "允许晋级", color: "success", tone: "success" },
  blocked: { label: "阻断晋级", color: "error", tone: "error" },
  insufficient_data: { label: "样本不足", color: "warning", tone: "warning" },
  not_applicable: { label: "无需判定", color: "default", tone: "neutral" },
} as const;

/** Release 在当前 Route 中的角色名称。 */
export const releaseRoleLabel: Record<string, string> = {
  primary: "正式版本",
  canary: "灰度版本",
  previous: "上一版本",
};

/** 后端稳定告警码的运维名称；未知新码保留原值以避免静默丢失。 */
export const alertCodeLabel: Record<string, string> = {
  INSUFFICIENT_TERMINAL_SAMPLES: "终态样本不足",
  ERROR_RATE_HIGH: "错误率超过上限",
  DEGRADATION_RATE_HIGH: "降级率超过上限",
  LATENCY_P95_HIGH: "P95 延迟超过上限",
  COST_BUDGET_EXCEEDED: "单次成本超过预算",
  QUALITY_FEEDBACK_LOW: "有帮助率低于下限",
  ERROR_RATE_REGRESSION: "错误率相对回归",
  DEGRADATION_RATE_REGRESSION: "降级率相对回归",
  LATENCY_REGRESSION: "P95 延迟相对回归",
  COST_REGRESSION: "平均成本相对回归",
  ROUTE_IDENTITY_CHANGED: "Route 身份已经变化",
  CANARY_ROUTE_REQUIRED: "当前 Route 不是灰度模式",
};

/** 指标字段的中文名称。 */
export const metricLabel: Record<string, string> = {
  terminal_count: "终态样本",
  error_rate_bps: "错误率",
  degradation_rate_bps: "降级率",
  latency_p95_ms: "P95 延迟",
  max_run_cost_microunits: "最高单次成本",
  average_cost_microunits: "平均单次成本",
  helpful_rate_bps: "有帮助率",
};

/** 将基点转换为百分比；空值明确表示尚未测量。 */
export function formatRate(value: number | null) {
  if (value === null) return "尚未测量";
  return `${(value / 100).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}%`;
}

/** 将毫秒转换为紧凑且可比较的运营时延。 */
export function formatLatency(value: number | null) {
  if (value === null) return "尚未测量";
  if (value < 1_000) return `${value.toLocaleString("zh-CN")} ms`;
  return `${(value / 1_000).toLocaleString("zh-CN", { maximumFractionDigits: 2 })} 秒`;
}

/** 成本契约未携带币种，因此只展示不产生误导的微单位原值。 */
export function formatCost(value: number | null) {
  return value === null ? "尚未测量" : `${value.toLocaleString("zh-CN")} 微单位`;
}

/** 按指标语义格式化告警的观测值与阈值。 */
export function formatMetricValue(metric: string, value: number) {
  if (metric.endsWith("_bps")) return formatRate(value);
  if (metric.endsWith("_ms")) return formatLatency(value);
  if (metric.endsWith("_microunits")) return formatCost(value);
  return value.toLocaleString("zh-CN");
}

/** 将服务端 UTC 时间转换为浏览器本地时间。 */
export function formatOperationsTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
