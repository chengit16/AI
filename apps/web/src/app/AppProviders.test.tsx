/** @description 应用级 Provider 的中文本地化与可访问标签回归测试。 */
import { cleanup, render, screen } from "@testing-library/react";
import { Input } from "antd";
import { afterEach, describe, expect, it } from "vitest";

import { AppProviders } from "@/app/AppProviders";

describe("应用级 Provider", () => {
  afterEach(cleanup);

  it("为 Ant Design 控件提供中文辅助技术标签", () => {
    render(
      <AppProviders>
        <Input.Password aria-label="合成密码" />
      </AppProviders>,
    );

    expect(screen.getByRole("button", { name: "显示" })).toBeInTheDocument();
  });
});
