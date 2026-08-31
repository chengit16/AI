/** @description 团队成员搜索与多维筛选栏。 */
import { Button, Input, Select } from "antd";
import { RotateCcw, Search } from "lucide-react";

import type { TeamManagement } from "@/api/services/teamManagement";

import type { TeamFilters } from "../types";

interface MemberFiltersProps {
  /** 当前团队聚合，用于构造组织和角色筛选选项。 */
  snapshot: TeamManagement;
  /** URL 中的当前筛选条件。 */
  filters: TeamFilters;
  /** 更新单项筛选并同步 URL。 */
  onChange: (key: keyof TeamFilters, value: string) => void;
  /** 清除全部筛选。 */
  onReset: () => void;
}

/** 以可换行工具栏提供成员搜索、状态、组织和角色筛选。 */
export function MemberFilters({ snapshot, filters, onChange, onReset }: MemberFiltersProps) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-b-solid border-border px-5 py-4">
      <Input
        allowClear
        className="min-w-56 flex-1"
        prefix={<Search size={15} />}
        placeholder="搜索姓名或登录名"
        value={filters.query}
        onChange={(event) => onChange("query", event.target.value)}
      />
      <Select
        allowClear
        className="w-30"
        placeholder="成员状态"
        value={filters.status || undefined}
        options={[
          { value: "active", label: "启用" },
          { value: "disabled", label: "已停用" },
          { value: "left", label: "已移除" },
        ]}
        onChange={(value?: string) => onChange("status", value ?? "")}
      />
      <Select
        allowClear
        className="w-36"
        placeholder="所属部门"
        value={filters.departmentId || undefined}
        options={snapshot.departments.map((item) => ({
          value: item.department_id,
          label: `${"— ".repeat(item.depth)}${item.name}`,
        }))}
        onChange={(value?: string) => onChange("departmentId", value ?? "")}
      />
      <Select
        allowClear
        className="w-32"
        placeholder="岗位"
        value={filters.positionId || undefined}
        options={snapshot.positions.map((item) => ({ value: item.position_id, label: item.name }))}
        onChange={(value?: string) => onChange("positionId", value ?? "")}
      />
      <Select
        allowClear
        className="w-32"
        placeholder="有效角色"
        value={filters.roleId || undefined}
        options={snapshot.roles.map((item) => ({ value: item.role_id, label: item.name }))}
        onChange={(value?: string) => onChange("roleId", value ?? "")}
      />
      <Button icon={<RotateCcw size={15} />} onClick={onReset}>
        重置
      </Button>
    </div>
  );
}
