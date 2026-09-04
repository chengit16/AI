/** @description 平台管理员资格 Hook 的低敏查询与业务数据隔离测试。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const platformApi = vi.hoisted(() => ({
  getPlatformAdministration: vi.fn(),
  getPlatformModelProviders: vi.fn(),
}));
vi.mock("@/api/services/platformModels", () => platformApi);

import { useSessionStore } from "@/store/session";

import { usePlatformAdministration } from "./usePlatformAdministration";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const Wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return { queryClient, Wrapper };
}

describe("平台管理员资格 Hook", () => {
  beforeEach(() => {
    useSessionStore.getState().clear();
    useSessionStore
      .getState()
      .setAuthenticated("account-id", "workspace-id", "synthetic-csrf-token");
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("普通成员只读取低敏资格，不探测管理员业务列表", async () => {
    platformApi.getPlatformAdministration.mockResolvedValue({
      is_platform_administrator: false,
    });
    const { queryClient, Wrapper } = createWrapper();
    const view = renderHook(() => usePlatformAdministration(), { wrapper: Wrapper });

    await waitFor(() => expect(view.result.current.administration.isSuccess).toBe(true));

    expect(view.result.current.isPlatformAdministrator).toBe(false);
    expect(view.result.current.isDenied).toBe(true);
    expect(platformApi.getPlatformAdministration).toHaveBeenCalledOnce();
    expect(platformApi.getPlatformModelProviders).not.toHaveBeenCalled();
    view.unmount();
    queryClient.clear();
  });

  it("管理员资格为真时开放平台入口体验状态", async () => {
    platformApi.getPlatformAdministration.mockResolvedValue({
      is_platform_administrator: true,
    });
    const { queryClient, Wrapper } = createWrapper();
    const view = renderHook(() => usePlatformAdministration(), { wrapper: Wrapper });

    await waitFor(() => expect(view.result.current.administration.isSuccess).toBe(true));

    expect(view.result.current.isPlatformAdministrator).toBe(true);
    expect(view.result.current.isDenied).toBe(false);
    expect(platformApi.getPlatformModelProviders).not.toHaveBeenCalled();
    view.unmount();
    queryClient.clear();
  });
});
