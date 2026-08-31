/** @description 团队成员筛选 URL 状态 Hook。 */
import { useSearchParams } from "react-router";

import type { TeamFilters } from "./types";

/** 让搜索和治理筛选支持刷新恢复、浏览器返回和链接分享。 */
export function useTeamFilters() {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters: TeamFilters = {
    query: searchParams.get("query") ?? "",
    status: searchParams.get("status") ?? "",
    departmentId: searchParams.get("department") ?? "",
    positionId: searchParams.get("position") ?? "",
    roleId: searchParams.get("role") ?? "",
  };

  const setFilter = (key: keyof TeamFilters, value: string) => {
    const parameter = {
      query: "query",
      status: "status",
      departmentId: "department",
      positionId: "position",
      roleId: "role",
    }[key];
    const next = new URLSearchParams(searchParams);
    if (value) next.set(parameter, value);
    else next.delete(parameter);
    setSearchParams(next, { replace: true });
  };

  return {
    filters,
    setFilter,
    resetFilters: () => setSearchParams({}, { replace: true }),
  };
}
