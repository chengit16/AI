import { cn } from "@/utils/cn";

interface PlatformMarkProps {
  /** 侧栏收起时仅保留稳定尺寸的品牌符号。 */
  compact?: boolean;
  /** 品牌名称所在表面，移动抽屉使用浅色，其余深色品牌区使用深色。 */
  surface?: "dark" | "light";
}

/**
 * 展示平台品牌标识。
 *
 * 组件只负责品牌呈现，不承担首页导航行为；这样可同时用于桌面侧栏、移动抽屉和登录页。
 */
export function PlatformMark({ compact = false, surface = "dark" }: PlatformMarkProps) {
  return (
    <div className="flex min-w-0 items-center gap-3 whitespace-nowrap" aria-label="AI 智能平台">
      <span className="grid h-9 w-9 flex-none place-items-center rounded-ui bg-accent text-[13px] font-800 text-nav-bg">
        AI
      </span>
      {!compact && (
        <span
          className={cn(
            "text-base font-700",
            surface === "dark" ? "text-nav-text-strong" : "text-text-strong",
          )}
        >
          智能平台
        </span>
      )}
    </div>
  );
}
