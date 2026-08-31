/** @description 团队聚合授权策略传播窗口的有限重试测试。 */
import { describe, expect, it } from "vitest";

import { PlatformApiError } from "@/api/client";

import { shouldRetryTeamQuery, teamQueryRetryDelay } from "./useTeamManagement";

describe("P6B-02 团队聚合查询重试", () => {
  it("只重试服务端明确标记的授权策略暂不可用", () => {
    const propagating = new PlatformApiError(
      503,
      "POLICY_UNAVAILABLE",
      "授权策略版本正在传播",
      true,
    );

    expect(shouldRetryTeamQuery(0, propagating)).toBe(true);
    expect(shouldRetryTeamQuery(3, propagating)).toBe(true);
    expect(shouldRetryTeamQuery(4, propagating)).toBe(false);
    expect(shouldRetryTeamQuery(0, new PlatformApiError(403, "POLICY_DENIED", "拒绝", false))).toBe(
      false,
    );
    expect(
      shouldRetryTeamQuery(0, new PlatformApiError(503, "DEPENDENCY_UNAVAILABLE", "失败", true)),
    ).toBe(false);
    expect(shouldRetryTeamQuery(0, new Error("未知错误"))).toBe(false);
  });

  it("使用有上限的指数退避", () => {
    expect([0, 1, 2, 3, 4].map(teamQueryRetryDelay)).toEqual([500, 1_000, 2_000, 4_000, 4_000]);
  });
});
