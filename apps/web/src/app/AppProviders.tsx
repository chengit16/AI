/** @description 应用级 Ant Design、TanStack Query 与错误边界 Provider 编排。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntdApp, ConfigProvider } from "antd";
import type { PropsWithChildren } from "react";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: 1, staleTime: 15_000 },
    mutations: { retry: false },
  },
});

/** 提供统一主题、全局消息上下文和共享 QueryClient。 */
export function AppProviders({ children }: PropsWithChildren) {
  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: "#176b52",
          colorInfo: "#176b52",
          colorSuccess: "#2d7a59",
          colorWarning: "#b56f18",
          colorError: "#b43a3a",
          borderRadius: 6,
          controlHeight: 44,
          fontFamily: '"Avenir Next", "PingFang SC", "Microsoft YaHei", sans-serif',
        },
      }}
    >
      <AntdApp>
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      </AntdApp>
    </ConfigProvider>
  );
}
