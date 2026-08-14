import { defineConfig, presetWind3 } from "unocss";
import type { Variant } from "unocss";

/** 创建包含临界像素的项目级 max-width Variant，避免 `lt-*` 自动减去 0.1px。 */
function maxWidthVariant(name: string, width: number): Variant {
  return {
    name,
    order: -1,
    match(matcher) {
      const prefix = `${name}:`;
      if (!matcher.startsWith(prefix)) {
        return undefined;
      }
      return {
        matcher: matcher.slice(prefix.length),
        parent: `@media (max-width: ${width}px)`,
      };
    },
  };
}

/**
 * Web 前端唯一的 UnoCSS 配置入口。
 *
 * 语义颜色和圆角继续引用 CSS Variables，避免 UnoCSS、Ant Design 和业务页面
 * 各自维护视觉常量。Preflight 必须关闭，全局 Reset 与可访问性规则仍由 global.css 负责。
 */
export default defineConfig({
  presets: [presetWind3({ preflight: false })],
  theme: {
    breakpoints: {
      phone: "560px",
      form: "600px",
      nav: "720px",
      tablet: "820px",
      compact: "900px",
      desktop: "1024px",
    },
    colors: {
      accent: "var(--color-accent)",
      border: "var(--color-border)",
      "border-soft": "var(--color-border-soft)",
      brand: "var(--color-brand)",
      "brand-border": "var(--color-brand-border)",
      "brand-soft": "var(--color-brand-soft)",
      canvas: "var(--color-canvas)",
      "danger-text": "var(--color-danger-text)",
      "nav-bg": "var(--color-nav-bg)",
      "nav-active": "var(--color-nav-active)",
      "nav-divider": "var(--color-nav-divider)",
      "nav-hover": "var(--color-nav-hover)",
      "nav-muted": "var(--color-nav-muted)",
      "nav-separator": "var(--color-nav-separator)",
      "nav-text": "var(--color-nav-text)",
      "nav-text-strong": "var(--color-nav-text-strong)",
      "status-banner": "var(--color-status-banner)",
      "status-banner-pending": "var(--color-status-banner-pending)",
      "status-detail": "var(--color-status-detail)",
      "status-foreground": "var(--color-status-foreground)",
      "status-label": "var(--color-status-label)",
      surface: "var(--color-surface)",
      "surface-subtle": "var(--color-surface-subtle)",
      text: "var(--color-text)",
      "text-muted": "var(--color-text-muted)",
      "text-strong": "var(--color-text-strong)",
      topbar: "var(--color-topbar)",
      warning: "var(--color-warning)",
    },
    borderRadius: {
      ui: "var(--radius-sm)",
      panel: "var(--radius-md)",
    },
    spacing: {
      1: "var(--space-1)",
      2: "var(--space-2)",
      3: "var(--space-3)",
      4: "var(--space-4)",
      5: "var(--space-5)",
      6: "var(--space-6)",
      8: "var(--space-8)",
    },
  },
  shortcuts: {
    "ui-icon-badge": "grid place-items-center rounded-panel bg-brand-soft text-brand",
    "ui-surface-panel": "border border-solid border-border rounded-panel bg-surface",
  },
  safelist: [],
  variants: [
    maxWidthVariant("phone-down", 560),
    maxWidthVariant("form-down", 600),
    maxWidthVariant("nav-mobile", 720),
    maxWidthVariant("tablet-down", 820),
    maxWidthVariant("compact-down", 900),
    maxWidthVariant("desktop-down", 1024),
    {
      name: "landscape-mobile",
      // 必须早于 Wind3 自带的 landscape Variant，否则复合前缀会被提前截获。
      order: -1,
      match(matcher) {
        const prefix = "landscape-mobile:";
        if (!matcher.startsWith(prefix)) {
          return undefined;
        }
        return {
          matcher: matcher.slice(prefix.length),
          // 现有移动横屏同时受宽度和高度约束，不能用单一宽度断点代替。
          parent: "@media (max-width: 900px) and (max-height: 500px)",
        };
      },
    },
  ],
});
