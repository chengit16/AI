/** @description 审计页签和筛选条件的 URL 状态。 */
import { useSearchParams } from "react-router";

import type { AuditFilters, AuditOutcome } from "@/api/services/permissionsAudit";

/** 权限与审计页面允许写入 URL 的稳定页签。 */
export type PermissionsAuditTab = "roles" | "audit";

/** 让页签、筛选、刷新和浏览器返回共享同一份 URL 事实。 */
export function useAuditFilters() {
  const [params, setParams] = useSearchParams();
  const tab: PermissionsAuditTab = params.get("tab") === "audit" ? "audit" : "roles";
  const filters: AuditFilters = {
    actorId: params.get("actor") || undefined,
    action: params.get("action") || undefined,
    resourceType: params.get("resource") || undefined,
    outcome: (params.get("outcome") as AuditOutcome | null) ?? undefined,
    occurredFrom: params.get("from") || undefined,
    occurredTo: params.get("to") || undefined,
  };
  const update = (values: Record<string, string | undefined>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(values)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next, { replace: true });
  };
  return {
    tab,
    filters,
    setTab: (value: PermissionsAuditTab) => update({ tab: value === "roles" ? undefined : value }),
    setFilter: (key: string, value?: string) => update({ [key]: value }),
    reset: () => setParams(tab === "audit" ? { tab: "audit" } : {}, { replace: true }),
  };
}
