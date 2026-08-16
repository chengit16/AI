import react from "@vitejs/plugin-react";
import UnoCSS from "unocss/vite";
import { defineConfig } from "vitest/config";

/**
 * Web 开发、测试和生产构建的统一 Vite 配置。
 *
 * UnoCSS 必须先于 React 插件扫描源码，生产构建与开发环境共用同一份配置，
 * 防止条件类名只在本地开发时生效。
 */
export default defineConfig({
  plugins: [UnoCSS(), react()],
  resolve: {
    alias: { "@": new URL("./src", import.meta.url).pathname },
  },
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: "vendor-antd", test: /node_modules\/(?:antd|@ant-design)/ },
            { name: "vendor-icons", test: /node_modules\/lucide-react/ },
          ],
        },
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 3000,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    // 本地统一门禁会与 Python、容器等检查共享资源，限制并发可避免 jsdom 初始化争用造成随机超时。
    maxWorkers: 2,
    setupFiles: "./src/test/setup.ts",
  },
});
