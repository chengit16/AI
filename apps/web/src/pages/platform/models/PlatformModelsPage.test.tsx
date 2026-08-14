/** @description 平台模型治理页面权限、供应商和运行配置交互测试。 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ModelProvider } from "@/api/services/platformModels";

import { ProviderTable } from "./components/ProviderTable";

const provider: ModelProvider = {
  provider_id: "90000000-0000-4000-8000-000000000701",
  provider_key: "synthetic_primary",
  display_name: "合成 GPT 主模型",
  adapter_kind: "openai_compatible",
  base_url: "https://gateway.synthetic.example/v1",
  probe_model_id: "synthetic-model",
  location: "external",
  declared_capabilities: ["generation", "streaming"],
  policy_review_status: "approved",
  max_security_level: "INTERNAL",
  retention_days: 0,
  training_usage_allowed: false,
  policy_url: null,
  policy_version: "synthetic-v1",
  policy_reviewed_at: "2026-08-14T10:00:00Z",
  probe_status: "passed",
  probed_capabilities: ["generation", "streaming"],
  last_probe_error_code: null,
  last_probed_at: "2026-08-14T10:01:00Z",
  status: "draft",
  created_at: "2026-08-14T09:00:00Z",
  updated_at: "2026-08-14T10:01:00Z",
  version: 3,
};

describe("P1D-07 平台模型配置表格", () => {
  afterEach(cleanup);

  it("展示政策与探测事实，并从动作菜单触发启用命令", async () => {
    const onAction = vi.fn();
    render(
      <ProviderTable
        items={[provider]}
        isLoading={false}
        isMutating={false}
        onRotateCredential={vi.fn()}
        onReviewPolicy={vi.fn()}
        onAction={onAction}
      />,
    );

    expect(screen.getByText("已批准")).toBeInTheDocument();
    expect(screen.getByText("探测通过")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开 合成 GPT 主模型 操作菜单" }));
    fireEvent.click(await screen.findByText("启用供应商"));
    expect(onAction).toHaveBeenCalledWith(provider.provider_id, "activate");
  });
});
