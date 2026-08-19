/** @description 平台模型治理页面权限、供应商和运行配置交互测试。 */
import { App } from "antd";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AiRuntimeConfig, ModelProvider } from "@/api/services/platformModels";

import { ProviderDialogs } from "./components/ProviderDialogs";
import { ProviderTable } from "./components/ProviderTable";
import { RuntimeTable } from "./components/RuntimeTable";
import PlatformModelsPage from ".";
import { usePlatformModels } from "./usePlatformModels";

vi.mock("./usePlatformModels", () => ({ usePlatformModels: vi.fn() }));

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

const runtime: AiRuntimeConfig = {
  runtime_config_version_id: "90000000-0000-4000-8000-000000000702",
  version_number: 2,
  display_name: "P5-04 质量配置",
  content_hash: "a".repeat(64),
  system_prompt_template: "只依据已授权证据回答。",
  system_prompt_hash: "b".repeat(64),
  components: {
    chunking: "recursive-cjk-v1",
    embedding: "deterministic-hash-1024-v1",
    index_schema: "index-v1",
    reranker: "bge-reranker-v1",
    retrieval: "hybrid-rrf-v1",
    source_ranking: "source-priority-v1",
    safety: "rag-safety-v2",
    data_source_interface: "data-source-v1",
    relevance_grader_interface: "relevance-grader-v1",
    multimodal_router_interface: "multimodal-router-v1",
  },
  policy: {
    attempt_timeout_ms: 60_000,
    total_timeout_ms: 120_000,
    max_attempts_per_route: 1,
    max_prompt_characters: 32_000,
    max_output_tokens: 2_048,
    max_response_characters: 64_000,
    circuit_failure_threshold: 3,
    circuit_recovery_ms: 30_000,
    rule_degradation_message: "当前模型暂不可用，请稍后重试",
    max_estimated_cost_microunits: 5_000_000,
  },
  routes: [
    {
      route_id: "90000000-0000-4000-8000-000000000703",
      provider_id: provider.provider_id,
      provider_configuration_version: provider.version,
      priority: 1,
      model_id: "gpt-5.6-sol",
      location: "external",
      capabilities: ["generation", "streaming"],
      input_price_microunits_per_million_tokens: 0,
      output_price_microunits_per_million_tokens: 0,
      currency: "CNY",
    },
  ],
  created_by_account_id: "90000000-0000-4000-8000-000000000704",
  created_at: "2026-08-19T08:00:00Z",
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

  it("从只读详情核对运行配置身份、路由和零价格边界", async () => {
    render(
      <RuntimeTable
        items={[runtime]}
        providers={[{ ...provider, status: "active" }]}
        currentId={runtime.runtime_config_version_id}
        isLoading={false}
        isActivating={false}
        onActivate={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "查看 P5-04 质量配置 详情" }));
    const dialog = await screen.findByRole("dialog", { name: "运行配置详情" });

    expect(within(dialog).getByText("gpt-5.6-sol")).toBeInTheDocument();
    expect(within(dialog).getByText("合成 GPT 主模型")).toBeInTheDocument();
    expect(within(dialog).getByText("synthetic_primary")).toBeInTheDocument();
    expect(within(dialog).getByText("存在价格为 0 的路由")).toBeInTheDocument();
    expect(within(dialog).queryByText(runtime.system_prompt_template)).not.toBeInTheDocument();
  });

  it("当前发布指针失败时仍展示已创建的运行配置", async () => {
    const query = (data: unknown, overrides: Record<string, unknown> = {}) => ({
      data,
      error: null,
      isError: false,
      isLoading: false,
      refetch: vi.fn(),
      ...overrides,
    });
    const mutation = () => ({ isPending: false, mutate: vi.fn(), mutateAsync: vi.fn() });
    vi.mocked(usePlatformModels).mockReturnValue({
      providers: query([{ ...provider, status: "active" }]),
      runtimeConfigs: query([runtime]),
      currentRuntime: query(undefined, {
        error: new Error("synthetic current failure"),
        isError: true,
      }),
      createProvider: mutation(),
      rotateCredential: mutation(),
      reviewPolicy: mutation(),
      providerAction: mutation(),
      createRuntime: mutation(),
      activateRuntime: mutation(),
    } as never);

    render(
      <App>
        <PlatformModelsPage />
      </App>,
    );
    fireEvent.click(screen.getByRole("tab", { name: "运行配置 1" }));

    expect(await screen.findByText("当前发布状态未能加载")).toBeInTheDocument();
    expect(screen.getByText("P5-04 质量配置")).toBeInTheDocument();
    expect(screen.queryByText("运行配置未能加载")).not.toBeInTheDocument();
  });
});
