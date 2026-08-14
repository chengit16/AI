/** @description 知识文档、入库任务、可见性和敏感级别展示配置。 */
import type { IngestionJob, KnowledgeDocumentSummary } from "@/api/services/knowledge";

/** 文档最新版本状态到标签和颜色的完整映射。 */
export const documentStatus = {
  draft: { label: "待解析", color: "default" },
  ready: { label: "待发布", color: "processing" },
  published: { label: "已发布", color: "success" },
  superseded: { label: "已替换", color: "default" },
} as const satisfies Record<
  KnowledgeDocumentSummary["latest_version"]["status"],
  { label: string; color: string }
>;

/** 入库任务状态到标签和颜色的完整映射。 */
export const ingestionStatus = {
  queued: { label: "排队中", color: "default" },
  running: { label: "处理中", color: "processing" },
  retry_wait: { label: "等待重试", color: "warning" },
  succeeded: { label: "已完成", color: "success" },
  failed: { label: "失败", color: "error" },
} as const satisfies Record<IngestionJob["status"], { label: string; color: string }>;

/** 文档可见范围的中文标签。 */
export const visibilityLabels = {
  private: "仅创建者",
  workspace: "整个空间",
  departments: "指定部门",
} as const;

/** 与服务端敏感级别顺序一致的中文标签。 */
export const securityLevelLabels = {
  PUBLIC: "公开",
  INTERNAL: "内部",
  CONFIDENTIAL: "机密",
  RESTRICTED: "严格限制",
} as const;

/** 使用当前浏览器时区格式化知识事实更新时间。 */
export function formatTimestamp(value: string | null) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
