/** @description 运营状态、操作和时间的中文展示配置。 */
import type { IndexCommand } from "@/api/services/operations";

/** 将后端稳定状态映射为一致的中文标签和语义颜色。 */
export const statusPresentation: Record<string, { label: string; color: string }> = {
  queued: { label: "排队中", color: "processing" },
  pending: { label: "待处理", color: "processing" },
  running: { label: "执行中", color: "processing" },
  publishing: { label: "发布中", color: "processing" },
  retry_wait: { label: "等待重试", color: "warning" },
  succeeded: { label: "成功", color: "success" },
  completed: { label: "已完成", color: "success" },
  published: { label: "已发布", color: "success" },
  failed: { label: "失败", color: "error" },
  dead_letter: { label: "死信", color: "error" },
  cancelled: { label: "已取消", color: "default" },
  timed_out: { label: "已超时", color: "error" },
};

/** 固定三类索引维护命令的展示文案、说明和服务端确认词。 */
export const indexCommandPresentation: Record<
  IndexCommand,
  { label: string; confirmation: string; description: string }
> = {
  inspection: {
    label: "巡检并修复",
    confirmation: "RUN_INDEX_INSPECTION",
    description: "复核当前发布引用，优先恢复完整候选，必要时排队安全重建。",
  },
  full_rebuild: {
    label: "全量重建",
    confirmation: "REBUILD_WORKSPACE_INDEX",
    description: "从当前已发布文档创建新构建，完整提交前继续保留健康旧索引。",
  },
  cleanup: {
    label: "清理失败索引",
    confirmation: "DELETE_UNRECOVERABLE_INDEX_CHUNKS",
    description: "只删除恢复预算耗尽且从未对检索可见的 Chunk。",
  },
};

/** 将 ISO 时间转换为本地短格式，无法解析时保持可识别占位。 */
export function formatOperationsTime(value: string | null | undefined): string {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间无效";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

/** 将状态映射为稳定中文标签和 Ant Design 颜色。 */
export function presentStatus(status: string) {
  return statusPresentation[status] ?? { label: status, color: "default" };
}
