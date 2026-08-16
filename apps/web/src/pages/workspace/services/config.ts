/** @description 服务发布页面的类型、状态和时间展示规则。 */

/** 三类服务出口的稳定中文名称。 */
export const serviceTypeLabel: Record<string, string> = {
  custom_knowledge_agent: "知识 Agent",
  scenario_application: "场景应用",
  open_api: "Open API",
};

/** 服务生命周期状态的中文标签与颜色。 */
export const serviceStatus: Record<string, { label: string; color: string }> = {
  active: { label: "运行中", color: "success" },
  suspended: { label: "已暂停", color: "warning" },
  archived: { label: "已归档", color: "default" },
};

/** Route 模式的中文标签与颜色。 */
export const routeMode: Record<string, { label: string; color: string }> = {
  active: { label: "正式", color: "success" },
  canary: { label: "灰度", color: "processing" },
  rollback: { label: "已回滚", color: "warning" },
};

/** 将服务端时间格式化为浏览器本地可读时间。 */
export function formatServiceTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
