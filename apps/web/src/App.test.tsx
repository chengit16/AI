import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

describe("运行中心", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("展示后端返回的健康状态", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          service: "ai-platform-api",
          status: "ok",
          version: "0.0.0",
          environment: "local",
          checks: { api: "ok", configuration: "ok" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <MemoryRouter initialEntries={["/status"]}>
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("基础服务运行正常")).toBeInTheDocument();
    expect(screen.getByText("平台 API")).toBeInTheDocument();
    expect(screen.getByText("配置中心")).toBeInTheDocument();
  });
});
