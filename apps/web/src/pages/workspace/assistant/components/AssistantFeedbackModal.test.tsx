/** @description 助手反馈弹窗的问题标签校验和提交交互回归测试。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AssistantFeedbackModal } from "./AssistantFeedbackModal";

describe("P1E-06 助手反馈弹窗", () => {
  afterEach(cleanup);

  it("提交需要改进反馈时携带已选问题标签和说明", async () => {
    const onSubmit = vi.fn();
    render(
      <AssistantFeedbackModal
        open
        message={{ message_id: "90000000-0000-4000-8000-000000000806" }}
        loading={false}
        initial={null}
        onClose={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    fireEvent.click(screen.getByRole("checkbox", { name: "缺少来源" }));
    fireEvent.change(screen.getByPlaceholderText("补充说明 (可选)"), {
      target: { value: "合成验收反馈：当前回答没有可查看来源。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith("90000000-0000-4000-8000-000000000806", {
        rating: "unhelpful",
        issue_codes: ["missing_source"],
        comment: "合成验收反馈：当前回答没有可查看来源。",
      }),
    );
  });
});
