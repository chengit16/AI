/** @description 统一状态组件的标题层级与播报语义回归测试。 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { StateView } from "@/components/StateView/StateView";

describe("统一页面状态", () => {
  afterEach(cleanup);

  it("页面内部状态默认使用二级标题", () => {
    render(<StateView kind="empty" title="暂无数据" description="当前列表为空。" />);

    expect(screen.getByRole("heading", { level: 2, name: "暂无数据" })).toBeInTheDocument();
  });

  it("整页守卫状态可显式使用一级标题", () => {
    render(
      <StateView
        kind="denied"
        headingLevel={1}
        title="没有访问权限"
        description="当前账号不能访问此页面。"
      />,
    );

    expect(screen.getByRole("heading", { level: 1, name: "没有访问权限" })).toBeInTheDocument();
  });
});
