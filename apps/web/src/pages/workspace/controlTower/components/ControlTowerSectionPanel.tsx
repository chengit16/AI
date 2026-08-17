/** @description 展示一个治理分区的状态、阻断原因和低敏字段。 */
import { Card, Tag } from "antd";
import {
  CircleDollarSign,
  Layers3,
  Scale,
  ServerCog,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";

import type { OperationsControlTower } from "@/api/services/operations";

type Section = OperationsControlTower["sections"][number];

const iconBySection: Record<Section["key"], LucideIcon> = {
  quality: ShieldCheck,
  cost: CircleDollarSign,
  isolation: Layers3,
  compliance: Scale,
  private_instance: ServerCog,
};

const statusLabel: Record<Section["status"], string> = {
  passed: "已通过",
  blocked: "已阻断",
  not_run: "未执行",
  not_configured: "未配置",
  failed: "失败",
};

const statusColor: Record<Section["status"], string> = {
  passed: "success",
  blocked: "warning",
  not_run: "default",
  not_configured: "default",
  failed: "error",
};

interface ControlTowerSectionPanelProps {
  /** 当前治理分区的低敏快照，状态和阻断原因由后端冻结。 */
  section: Section;
}

/** 分区状态在视觉上突出阻断原因，同时避免展示外部环境标识和自由正文。 */
export function ControlTowerSectionPanel({ section }: ControlTowerSectionPanelProps) {
  const Icon = iconBySection[section.key];
  return (
    <Card
      className="h-full !rounded-ui !border-border-soft !shadow-none"
      styles={{ body: { padding: 20 } }}
      title={
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-ui bg-brand-soft text-brand">
            <Icon size={18} aria-hidden="true" />
          </span>
          <span className="text-base text-text-strong">{section.title}</span>
        </div>
      }
      extra={<Tag color={statusColor[section.status]}>{statusLabel[section.status]}</Tag>}
    >
      <p className="mb-4 mt-0 min-h-12 text-sm leading-[1.65] text-text-muted">{section.summary}</p>
      <dl className="m-0 grid gap-2 border-0 border-t border-t-solid border-border-soft pt-3">
        {section.facts.map((fact) => (
          <div
            className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-4 text-sm"
            key={`${section.key}-${fact.label}`}
          >
            <dt className="text-text-muted">{fact.label}</dt>
            <dd className="m-0 max-w-[16rem] text-right font-650 text-text-strong phone-down:max-w-[10rem]">
              <span className="break-words">{fact.value}</span>
              {fact.status !== "passed" && (
                <span className="ml-2 text-xs font-500 text-text-muted">
                  {statusLabel[fact.status]}
                </span>
              )}
            </dd>
          </div>
        ))}
      </dl>
      {section.reason_codes.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2" aria-label={`${section.title}阻断原因`}>
          {section.reason_codes.map((reason) => (
            <Tag key={reason}>{reason}</Tag>
          ))}
        </div>
      )}
    </Card>
  );
}
