/** UnoCSS 配置测试，验证项目复合断点会进入真实生成产物。 */
import { createGenerator } from "unocss";
import { describe, expect, it } from "vitest";

import unoConfig from "../../uno.config";

describe("UnoCSS 项目 Variant", () => {
  it("为导航移动态保留包含 720px 的旧断点边界", async () => {
    const generator = await createGenerator(unoConfig);
    const { css } = await generator.generate("nav-mobile:hidden");

    expect(css).toContain("@media (max-width: 720px)");
    expect(css).toMatch(/display:\s*none;/);
  });

  it("为移动横屏生成同时约束宽度和高度的媒体查询", async () => {
    const generator = await createGenerator(unoConfig);
    const { css } = await generator.generate("landscape-mobile:hidden");

    expect(css).toContain("@media (max-width: 900px) and (max-height: 500px)");
    expect(css).toMatch(/display:\s*none;/);
  });
});
