/** @description 团队管理页面筛选和成员编辑表单类型。 */

/** 可分享的成员筛选条件。 */
export interface TeamFilters {
  query: string;
  status: string;
  departmentId: string;
  positionId: string;
  roleId: string;
}

/** 成员抽屉一次提交的组织和直接角色配置。 */
export interface TeamMemberFormValues {
  departmentIds: string[];
  primaryDepartmentId?: string;
  positionIds: string[];
  directRoleIds: string[];
}
