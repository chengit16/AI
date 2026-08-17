/** @description 展示危险操作的确认与后端重授权边界。 */
import { Alert, Tag } from "antd";
import { ShieldAlert } from "lucide-react";

import type { OperationsControlTower } from "@/api/services/operations";

interface DangerousOperationsPanelProps {
  /** 仅展示既有受控接口的二次确认与后端重授权要求。 */
  items: OperationsControlTower["dangerous_operations"];
}

/** 这里只读展示治理约束；任何执行仍必须进入原有受控命令接口。 */
export function DangerousOperationsPanel({ items }: DangerousOperationsPanelProps) {
  return (
    <section className="ui-surface-panel mt-6 overflow-hidden" aria-labelledby="dangerous-title">
      <div className="border-0 border-b border-b-solid border-border-soft px-5 py-4 phone-down:px-4">
        <div className="flex items-center gap-3">
          <ShieldAlert className="text-warning" size={19} aria-hidden="true" />
          <div>
            <h2 id="dangerous-title" className="m-0 text-lg text-text-strong">
              危险操作边界
            </h2>
            <p className="mb-0 mt-1 text-sm text-text-muted">
              控制台不直接执行变更；确认文本、当前权限和工作空间归属由后端再次核验。
            </p>
          </div>
        </div>
      </div>
      <div className="grid gap-3 px-5 py-4 phone-down:px-4">
        {items.map((item) => (
          <div
            className="flex min-w-0 flex-wrap items-center justify-between gap-3 border-0 border-b border-b-solid border-border-soft pb-3 last:border-0 last:pb-0"
            key={item.permission_code}
          >
            <div className="min-w-0">
              <p className="m-0 font-650 text-text-strong">{item.operation}</p>
              <code className="mt-1 block break-all text-xs text-text-muted">
                {item.permission_code}
              </code>
            </div>
            <div className="flex flex-wrap gap-2">
              <Tag color="warning">二次确认</Tag>
              <Tag color="blue">后端重授权</Tag>
            </div>
          </div>
        ))}
      </div>
      <Alert
        className="!mx-5 !mb-5 phone-down:!mx-4"
        type="info"
        showIcon
        message="状态为 blocked、not_run 或 not_configured 时，不能通过前端操作强行变更为通过。"
      />
    </section>
  );
}
