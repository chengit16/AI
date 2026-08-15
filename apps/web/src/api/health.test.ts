/** @description 健康探针正常、依赖降级与异常响应边界测试。 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { getPlatformHealth } from "@/api/health";

describe("平台健康探针", () => {
  afterEach(() => vi.restoreAllMocks());

  it("保留 503 Readiness 响应中的依赖降级详情", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          service: "ai-platform-api",
          status: "degraded",
          version: "0.0.0",
          environment: "local",
          checks: { api: "ok", postgres: "degraded" },
        }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(getPlatformHealth()).resolves.toMatchObject({
      status: "degraded",
      checks: { postgres: "degraded" },
    });
  });

  it("拒绝缺少结构化健康事实的异常响应", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "synthetic failure" }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(getPlatformHealth()).rejects.toThrow("健康检查失败：HTTP 500");
  });
});
