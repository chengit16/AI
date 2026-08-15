/** @description 页面加载、空数据、错误和无权限状态的统一可访问反馈组件。 */
import { AlertTriangle, Inbox, LockKeyhole } from "lucide-react";
import type { ReactNode } from "react";

interface StateViewProps {
  /** 状态类别，决定图标和辅助技术播报语义。 */
  kind: "empty" | "error" | "denied";
  /** 可独立理解的状态标题。 */
  title: string;
  /** 状态原因或可恢复方式说明。 */
  description: string;
  /** 可选恢复、重试或导航动作。 */
  action?: ReactNode;
  /** 整页状态使用一级标题，页面内部状态保持二级标题。 */
  headingLevel?: 1 | 2;
}

const icons = { empty: Inbox, error: AlertTriangle, denied: LockKeyhole };

/**
 * 呈现空数据、加载错误和无权限三类稳定页面状态。
 *
 * 错误状态使用 `alert` 供辅助技术主动播报，其他状态使用 `status`，避免普通空态打断用户。
 */
export function StateView({ kind, title, description, action, headingLevel = 2 }: StateViewProps) {
  const Icon = icons[kind];
  const Heading = headingLevel === 1 ? "h1" : "h2";
  return (
    <section
      className="ui-surface-panel flex min-h-45 items-center justify-center gap-4 p-8 text-text"
      role={kind === "error" ? "alert" : "status"}
    >
      <span className="ui-icon-badge h-11 w-11 flex-none">
        <Icon size={22} aria-hidden="true" />
      </span>
      <div>
        <Heading className="m-0 text-[17px]">{title}</Heading>
        <p className="mb-0 mt-2 max-w-130 leading-[1.65] text-text-muted">{description}</p>
        {action && <div className="mt-4">{action}</div>}
      </div>
    </section>
  );
}
