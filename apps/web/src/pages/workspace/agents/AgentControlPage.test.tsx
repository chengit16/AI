/** @description Agent 控制台错误清空、知识范围绑定、草稿校验和候选动作测试。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentCandidateControl, AgentDetail } from "@/api/services/agents";
import type { KnowledgeBaseSummary } from "@/api/services/knowledge";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";

import AgentControlPage from ".";
import { AgentDialogs } from "./components/AgentDialogs";
import { AgentDraftEditor } from "./components/AgentDraftEditor";
import { AgentReleasePipeline } from "./components/AgentReleasePipeline";
import { useAgentControl } from "./useAgentControl";

vi.mock("./useAgentControl", () => ({ useAgentControl: vi.fn() }));
vi.mock("@/hooks/useWorkspaceMenuNavigation", () => ({
  useWorkspaceMenuNavigation: vi.fn(),
}));

const detail: AgentDetail = {
  agent: {
    agent_id: "a1000000-0000-4000-8000-000000000001",
    workspace_id: "a1000000-0000-4000-8000-000000000002",
    agent_key: "synthetic-agent",
    name: "合成制度 Agent",
    description: "仅用于 P3-11 前端测试",
    status: "active",
    created_by_account_id: "a1000000-0000-4000-8000-000000000003",
    created_at: "2026-08-16T08:00:00Z",
    updated_at: "2026-08-16T08:00:00Z",
    version: 1,
  },
  draft: {
    draft_id: "a2000000-0000-4000-8000-000000000001",
    agent_id: "a1000000-0000-4000-8000-000000000001",
    revision: 2,
    status: "editing",
    configuration: { prompt_version_id: "synthetic-version" },
    config_hash: "a".repeat(64),
    updated_at: "2026-08-16T08:00:00Z",
  },
  knowledge_scope_versions: [
    {
      knowledge_scope_version_id: "a5000000-0000-4000-8000-000000000001",
      name: "合成制度知识范围",
      knowledge_base_ids: ["d1000000-0000-4000-8000-000000000001"],
      scope_hash: "d".repeat(64),
      created_at: "2026-08-16T08:00:00Z",
    },
  ],
};

const knowledgeBases: KnowledgeBaseSummary[] = [
  {
    knowledge_base_id: "d1000000-0000-4000-8000-000000000001",
    name: "合成制度知识库",
    description: "仅用于 Agent 页面测试",
    default_visibility: "workspace",
    default_security_level: "INTERNAL",
    updated_at: "2026-08-16T08:00:00Z",
  },
];

const failedCandidate: AgentCandidateControl = {
  candidate: {
    candidate_id: "a3000000-0000-4000-8000-000000000001",
    agent_id: detail.agent.agent_id,
    draft_revision: 2,
    candidate_hash: "b".repeat(64),
    config_hash: "a".repeat(64),
    status: "test_failed",
    created_at: "2026-08-16T08:00:00Z",
    updated_at: "2026-08-16T08:05:00Z",
    version: 2,
  },
  evaluation: {
    evaluation_run_id: "a4000000-0000-4000-8000-000000000001",
    candidate_id: "a3000000-0000-4000-8000-000000000001",
    status: "failed",
    evaluator_version: "deterministic-v1",
    evidence_level: "core_functional",
    total_cases: 5,
    passed_cases: 4,
    failed_cases: 1,
    timeout_cases: 0,
    skipped_cases: 0,
    result_hash: "c".repeat(64),
    completed_at: "2026-08-16T08:05:00Z",
    checks: [],
  },
  approval: null,
};

function query(data?: unknown, overrides: Record<string, unknown> = {}) {
  return {
    data,
    error: null,
    isError: false,
    isLoading: false,
    refetch: vi.fn(),
    ...overrides,
  };
}

function mutation() {
  return { isPending: false, mutate: vi.fn(), mutateAsync: vi.fn() };
}

function pageModel(overrides: Record<string, unknown> = {}) {
  return {
    agents: query([detail]),
    candidates: query([failedCandidate]),
    releases: query([]),
    knowledgeBases: query(knowledgeBases),
    create: mutation(),
    saveDraft: mutation(),
    bindKnowledgeScope: mutation(),
    archive: mutation(),
    requestRelease: mutation(),
    evaluate: mutation(),
    requestApproval: mutation(),
    publish: mutation(),
    refreshPipeline: vi.fn(),
    ...overrides,
  };
}

describe("P3-11 Agent 控制台", () => {
  afterEach(cleanup);

  it("Agent 列表查询失败时不继续显示旧 Agent", () => {
    vi.mocked(useAgentControl).mockReturnValue(
      pageModel({
        agents: query([detail], { isError: true, error: new Error("synthetic failure") }),
      }) as never,
    );
    vi.mocked(useWorkspaceMenuNavigation).mockReturnValue({
      visiblePermissionCodes: new Set<string>(),
    } as never);

    render(
      <MemoryRouter initialEntries={[`/workspace/agents?agent=${detail.agent.agent_id}`]}>
        <AgentControlPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("Agent 控制台未能加载")).toBeInTheDocument();
    expect(screen.queryByText("合成制度 Agent")).not.toBeInTheDocument();
  });

  it("候选查询失败时清空旧测试和审批流水", () => {
    vi.mocked(useAgentControl).mockReturnValue(
      pageModel({
        candidates: query([failedCandidate], {
          isError: true,
          error: new Error("synthetic failure"),
        }),
      }) as never,
    );
    vi.mocked(useWorkspaceMenuNavigation).mockReturnValue({
      visiblePermissionCodes: new Set(["agent.page.access"]),
    } as never);

    render(
      <MemoryRouter
        initialEntries={[`/workspace/agents?agent=${detail.agent.agent_id}&view=pipeline`]}
      >
        <AgentControlPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("发布流水未能加载")).toBeInTheDocument();
    expect(screen.queryByText("测试未通过")).not.toBeInTheDocument();
  });

  it("无效 JSON 会阻止草稿保存", () => {
    render(
      <AgentDraftEditor
        detail={detail}
        canUpdate
        canArchive={false}
        canReadKnowledgeBases
        knowledgeBases={knowledgeBases}
        isKnowledgeLoading={false}
        isKnowledgeError={false}
        isMutating={false}
        onSave={vi.fn()}
        onBindKnowledgeScope={vi.fn()}
        onReloadKnowledgeBases={vi.fn()}
        onArchive={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText("完整配置 JSON"), { target: { value: "[]" } });
    expect(screen.getByRole("button", { name: "保存草稿" })).toBeDisabled();
    expect(screen.getByText("配置必须是有效 JSON 对象后才能保存。")).toBeInTheDocument();
  });

  it("从当前冻结版本还原知识库并提交下一草稿 revision", () => {
    const onBindKnowledgeScope = vi.fn();
    render(
      <AgentDraftEditor
        detail={detail}
        canUpdate
        canArchive={false}
        canReadKnowledgeBases
        knowledgeBases={knowledgeBases}
        isKnowledgeLoading={false}
        isKnowledgeError={false}
        isMutating={false}
        onSave={vi.fn()}
        onBindKnowledgeScope={onBindKnowledgeScope}
        onReloadKnowledgeBases={vi.fn()}
        onArchive={vi.fn()}
      />,
    );

    expect(screen.getByText("1 个知识库")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "冻结并保存范围" }));
    expect(onBindKnowledgeScope).toHaveBeenCalledWith(
      "合成制度 Agent 知识范围 r3",
      [knowledgeBases[0].knowledge_base_id],
      detail.draft.configuration,
      detail.draft.revision,
    );
  });

  it("新空间默认使用基础配置创建首个 Agent", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(<AgentDialogs open isCreating={false} onClose={onClose} onCreate={onCreate} />);

    fireEvent.change(screen.getByPlaceholderText("例如：合成制度问答助手"), {
      target: { value: "合成基础 Agent" },
    });
    fireEvent.click(screen.getByRole("button", { name: /创\s*建/ }));

    await waitFor(() =>
      expect(onCreate).toHaveBeenCalledWith({
        name: "合成基础 Agent",
        description: null,
        use_starter_configuration: true,
      }),
    );
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("测试失败候选只能按权限重新执行测试", () => {
    const onEvaluate = vi.fn();
    render(
      <AgentReleasePipeline
        items={[failedCandidate]}
        draftRevision={2}
        isAgentActive
        isLoading={false}
        canRequest={false}
        canEvaluate
        canRequestApproval
        canPublish
        isMutating={false}
        onRequest={vi.fn()}
        onEvaluate={onEvaluate}
        onRequestApproval={vi.fn()}
        onPublish={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "执行测试" }));
    expect(onEvaluate).toHaveBeenCalledWith(failedCandidate.candidate.candidate_id);
    expect(screen.queryByRole("button", { name: "发起审批" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "发布 Release" })).not.toBeInTheDocument();
  });
});
