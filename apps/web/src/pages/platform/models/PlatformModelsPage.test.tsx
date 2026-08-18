/** @description 平台模型治理页面权限、供应商和运行配置交互测试。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ModelProvider } from "@/api/services/platformModels";

import { ProviderDialogs } from "./components/ProviderDialogs";
import { ProviderTable } from "./components/ProviderTable";

const provider: ModelProvider = {
  provider_id: "90000000-0000-4000-8000-000000000701",
  provider_key: "synthetic_primary",
  display_name: "合成 GPT 主模型",
  adapter_kind: "openai_compatible",
  wire_api: "chat_completions",
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
    expect(screen.getByText(/Chat Completions/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开 合成 GPT 主模型 操作菜单" }));
    fireEvent.click(await screen.findByText("启用供应商"));
    expect(onAction).toHaveBeenCalledWith(provider.provider_id, "activate");
  });

  it("创建 Codex 类中转时提交 Responses 协议事实", async () => {
    const onCreate = vi.fn().mockResolvedValue(provider);
    render(
      <ProviderDialogs
        createOpen
        credentialProvider={null}
        policyProvider={null}
        isSubmitting={false}
        onCloseCreate={vi.fn()}
        onCloseCredential={vi.fn()}
        onClosePolicy={vi.fn()}
        onCreate={onCreate}
        onRotateCredential={vi.fn()}
        onReviewPolicy={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText("显示名称"), { target: { value: "合成 Codex 中转" } });
    fireEvent.change(screen.getByLabelText("供应商标识"), { target: { value: "synthetic_codex" } });
    fireEvent.change(screen.getByLabelText("自定义 Base URL"), {
      target: { value: "https://gateway.synthetic.example" },
    });
    fireEvent.mouseDown(screen.getByLabelText("调用协议"));
    fireEvent.click(await screen.findByText("Responses（Codex）"));
    fireEvent.change(screen.getByLabelText("探测模型 ID"), {
      target: { value: "synthetic-codex" },
    });
    fireEvent.change(screen.getByLabelText("API Key"), {
      target: { value: "synthetic-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: /创\s*建/ }));

    await waitFor(() =>
      expect(onCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          provider_key: "synthetic_codex",
          wire_api: "responses",
          base_url: "https://gateway.synthetic.example",
        }),
      ),
    );
  });
});
