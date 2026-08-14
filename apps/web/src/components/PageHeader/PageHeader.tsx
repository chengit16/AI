import type { ReactNode } from "react";

interface PageHeaderProps {
  title: string;
  description: string;
  eyebrow?: string;
  actions?: ReactNode;
}

/**
 * 统一页面标题、说明和主操作区的响应式布局。
 *
 * 窄屏下操作区换行并占满可用宽度，调用方仍负责按钮权限与具体业务行为。
 */
export function PageHeader({ title, description, eyebrow, actions }: PageHeaderProps) {
  return (
    <header className="mb-6 flex min-h-[76px] items-start justify-between gap-6 nav-mobile:flex-col nav-mobile:gap-4">
      <div>
        {eyebrow && <p className="mb-2 mt-0 text-xs font-750 text-brand">{eyebrow}</p>}
        <h1 className="m-0 text-[28px] leading-[1.3] text-text-strong nav-mobile:text-2xl">
          {title}
        </h1>
        <p className="mb-0 mt-2 max-w-[680px] leading-[1.65] text-text-muted">{description}</p>
      </div>
      {actions && (
        <div className="flex items-center gap-2 nav-mobile:w-full nav-mobile:flex-wrap">
          {actions}
        </div>
      )}
    </header>
  );
}
