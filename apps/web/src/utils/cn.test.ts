/** @description UnoCSS 静态类名合并工具测试。 */
import { describe, expect, it } from "vitest";

import { cn } from "./cn";

/** 验证条件类名组合不会把无效状态写入最终 DOM。 */
describe("cn", () => {
  it("合并静态、条件和嵌套类名", () => {
    expect(cn("base", { active: true, disabled: false }, ["compact"])).toBe("base active compact");
  });
});
