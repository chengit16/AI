/** UnoCSS 配置测试，验证项目复合断点会进入真实生成产物。 */
import { createGenerator } from "unocss";
import { describe, expect, it } from "vitest";

import unoConfig from "../../uno.config";

describe("UnoCSS 项目 Variant", () => {
  it.each([
    ["phone-down", 560],
    ["form-down", 600],
    ["nav-mobile", 720],
    ["tablet-down", 820],
    ["compact-down", 900],
    ["desktop-down", 1024],
  ])("为 %s 保留包含 %i px 的旧断点边界", async (variant, width) => {
    const generator = await createGenerator(unoConfig);
    const { css } = await generator.generate(`${variant}:hidden`);

    expect(css).toContain(`@media (max-width: ${width}px)`);
    expect(css).toMatch(/display:\s*none;/);
  });

  it("为移动横屏生成同时约束宽度和高度的媒体查询", async () => {
    const generator = await createGenerator(unoConfig);
    const { css } = await generator.generate("landscape-mobile:hidden");

    expect(css).toContain("@media (max-width: 900px) and (max-height: 500px)");
    expect(css).toMatch(/display:\s*none;/);
  });

  it.each([
    ["p-5", "--space-5"],
    ["gap-3", "--space-3"],
  ])("让 %s 使用项目语义间距 %s", async (utility, token) => {
    const generator = await createGenerator(unoConfig);
    const { css } = await generator.generate(utility);

    expect(css).toContain(`var(${token})`);
  });
});
