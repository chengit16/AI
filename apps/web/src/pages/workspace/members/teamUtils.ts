/** @description 团队筛选、状态和审计文案的纯函数。 */
import type { TeamManagement, TeamMember } from "@/api/services/teamManagement";

import type { TeamFilters } from "./types";

const statusLabels = { active: "启用", disabled: "已停用", left: "已移除" } as const;
const invitationLabels = {
  pending: "待接受",
  accepted: "已接受",
  cancelled: "已撤销",
  expired: "已过期",
} as const;
const auditLabels: Record<string, string> = {
  "workspace.member.invite": "创建成员邀请",
  "workspace.invitation.cancel": "撤销成员邀请",
  "workspace.member.join": "成员加入企业",
  "workspace.member.disable": "停用成员",
  "workspace.member.activate": "恢复成员",
  "workspace.member.remove": "移除成员",
  "workspace.member.configuration.update": "更新成员组织与角色",
  "organization.member.assign": "更新成员组织归属",
};

/** 把成员生命周期状态转换为稳定的中文页面文案。 */
export function memberStatusLabel(status: TeamMember["status"]) {
  return statusLabels[status];
}

/** 把邀请的有效状态转换为稳定的中文页面文案。 */
export function invitationStatusLabel(status: TeamManagement["invitations"][number]["status"]) {
  return invitationLabels[status];
}

/** 优先显示团队治理动作中文名，未知动作保留原始稳定标识。 */
export function auditActionLabel(action: string) {
  return auditLabels[action] ?? action;
}

/** 在浏览器对完整 Owner 读模型筛选，不改变服务端授权和字段裁剪结果。 */
export function filterMembers(members: readonly TeamMember[], filters: TeamFilters) {
  const query = filters.query.trim().toLocaleLowerCase("zh-CN");
  return members.filter((member) => {
    const matchesQuery =
      !query ||
      member.display_name?.toLocaleLowerCase("zh-CN").includes(query) ||
      member.login_name?.toLocaleLowerCase("zh-CN").includes(query);
    return (
      matchesQuery &&
      (!filters.status || member.status === filters.status) &&
      (!filters.departmentId || member.department_ids.includes(filters.departmentId)) &&
      (!filters.positionId || member.position_ids.includes(filters.positionId)) &&
      (!filters.roleId || member.effective_roles.some((role) => role.role_id === filters.roleId))
    );
  });
}

/** 按中文二十四小时制格式化审计时间，缺失事实时不伪造活跃时间。 */
export function formatTeamTime(value: string | null) {
  if (!value) return "暂无活动记录";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
