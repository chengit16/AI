import { defineConfig, presetWind3 } from "unocss";

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
      "brand-soft": "var(--color-brand-soft)",
      canvas: "var(--color-canvas)",
      "nav-bg": "var(--color-nav-bg)",
      "nav-active": "var(--color-nav-active)",
      "nav-divider": "var(--color-nav-divider)",
      "nav-hover": "var(--color-nav-hover)",
      "nav-muted": "var(--color-nav-muted)",
      "nav-text": "var(--color-nav-text)",
      "nav-text-strong": "var(--color-nav-text-strong)",
      surface: "var(--color-surface)",
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
  },
  shortcuts: {
    "ui-icon-badge": "grid place-items-center rounded-panel bg-brand-soft text-brand",
    "ui-muted": "text-text-muted",
    "ui-surface-panel": "border border-border rounded-panel bg-surface",
  },
  safelist: [],
  variants: [
    {
      name: "nav-mobile",
      order: -1,
      match(matcher) {
        const prefix = "nav-mobile:";
        if (!matcher.startsWith(prefix)) {
          return undefined;
        }
        return {
          matcher: matcher.slice(prefix.length),
          // 导航旧基线包含 720px 边界，不能使用生成到 719.9px 的 lt-nav 代替。
          parent: "@media (max-width: 720px)",
        };
      },
    },
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
