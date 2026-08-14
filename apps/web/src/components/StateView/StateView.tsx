import { AlertTriangle, Inbox, LockKeyhole } from "lucide-react";
import type { ReactNode } from "react";

interface StateViewProps {
  kind: "empty" | "error" | "denied";
  title: string;
  description: string;
  action?: ReactNode;
}

const icons = { empty: Inbox, error: AlertTriangle, denied: LockKeyhole };

/**
 * 呈现空数据、加载错误和无权限三类稳定页面状态。
 *
 * 错误状态使用 `alert` 供辅助技术主动播报，其他状态使用 `status`，避免普通空态打断用户。
 */
export function StateView({ kind, title, description, action }: StateViewProps) {
  const Icon = icons[kind];
  return (
    <section
      className="ui-surface-panel flex min-h-45 items-center justify-center gap-4 p-8 text-text"
      role={kind === "error" ? "alert" : "status"}
    >
      <span className="ui-icon-badge h-11 w-11 flex-none">
        <Icon size={22} aria-hidden="true" />
      </span>
      <div>
        <h2 className="m-0 text-[17px]">{title}</h2>
        <p className="mb-0 mt-2 max-w-130 leading-[1.65] text-text-muted">{description}</p>
        {action && <div className="mt-4">{action}</div>}
      </div>
    </section>
  );
}
