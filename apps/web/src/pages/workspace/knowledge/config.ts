/** @description 知识文档、入库任务、可见性和敏感级别展示配置。 */
import type {
  IngestionJob,
  KnowledgeDocumentDetail,
  KnowledgeDocumentSummary,
} from "@/api/services/knowledge";

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
  cancelled: { label: "已取消", color: "default" },
  timed_out: { label: "已超时", color: "error" },
} as const satisfies Record<IngestionJob["status"], { label: string; color: string }>;

/** 索引构建状态到详情页标签的完整映射。 */
export const indexStatus = {
  queued: { label: "等待索引", color: "default" },
  embedding_running: { label: "向量处理中", color: "processing" },
  embedding_retry_wait: { label: "向量等待重试", color: "warning" },
  index_queued: { label: "等待写入", color: "default" },
  index_running: { label: "索引写入中", color: "processing" },
  index_retry_wait: { label: "索引等待重试", color: "warning" },
  ready: { label: "索引就绪", color: "success" },
  active: { label: "检索中", color: "green" },
  retired: { label: "已退役", color: "default" },
  failed: { label: "索引失败", color: "error" },
  dead_letter: { label: "需人工处理", color: "error" },
} as const satisfies Record<
  NonNullable<KnowledgeDocumentDetail["versions"][number]["index"]>["status"],
  { label: string; color: string }
>;

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
