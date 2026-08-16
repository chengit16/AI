/** @description P3-12 Release 运营页面的安全状态、晋级结论与 URL 窗口测试。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";

import type { AgentOperationsReport } from "@/api/services/agentOperations";
import type { ServiceDeployment } from "@/api/services/services";

import AgentOperationsPage from ".";
import { useAgentOperations } from "./useAgentOperations";

vi.mock("./useAgentOperations", () => ({ useAgentOperations: vi.fn() }));

const serviceId = "b1000000-0000-4000-8000-000000000312";
const deployment: ServiceDeployment = {
  service: {
    service_id: serviceId,
    agent_id: "b2000000-0000-4000-8000-000000000312",
    service_key: "synthetic-operations",
    name: "合成制度运营服务",
    service_type: "custom_knowledge_agent",
    status: "active",
    version: 2,
    updated_at: "2026-08-16T08:00:00Z",
  },
  access_policy: {
    access_policy_version_id: "b3000000-0000-4000-8000-000000000312",
    version: 1,
    visibility: "workspace",
    allowed_department_ids: [],
    allowed_account_ids: [],
    policy_hash: "a".repeat(64),
  },
  route: {
    route_id: "b4000000-0000-4000-8000-000000000312",
    route_version: 3,
    route_mode: "canary",
    primary_release_id: "b5000000-0000-4000-8000-000000000312",
    canary_release_id: "b6000000-0000-4000-8000-000000000312",
    canary_percent: 20,
    previous_route_id: "b4000000-0000-4000-8000-000000000311",
    route_hash: "b".repeat(64),
    created_at: "2026-08-16T08:00:00Z",
  },
  publication: {
    route_id: "b4000000-0000-4000-8000-000000000312",
    generation: 3,
    published_at: "2026-08-16T08:00:00Z",
  },
};

const primary = {
  release_id: deployment.route.primary_release_id,
  release_version: 1,
  role: "primary" as const,
  run_count: 20,
  terminal_count: 20,
  completed_count: 20,
  failed_count: 0,
  success_rate_bps: 10_000,
  error_rate_bps: 0,
  degradation_rate_bps: 0,
  latency_p95_ms: 1_000,
  total_cost_microunits: 20_000,
  average_cost_microunits: 1_000,
  max_run_cost_microunits: 1_200,
  max_cost_budget_microunits: 2_000,
  feedback_count: 5,
  helpful_rate_bps: 10_000,
  feedback_quality_status: "measured" as const,
  offline_evaluation_status: "passed",
  offline_evaluation_score_bps: 9_500,
};

const report: AgentOperationsReport = {
  service_id: serviceId,
  service_name: deployment.service.name,
  service_status: "active",
  route_id: deployment.route.route_id,
  route_version: 3,
  route_mode: "canary",
  canary_percent: 20,
  window_started_at: "2026-08-15T08:00:00Z",
  window_ended_at: "2026-08-16T08:00:00Z",
  minimum_terminal_samples: 5,
  minimum_feedback_samples: 3,
  primary,
  comparison: {
    ...primary,
    release_id: deployment.route.canary_release_id!,
    release_version: 2,
    role: "canary",
    completed_count: 7,
    failed_count: 3,
    success_rate_bps: 7_000,
    error_rate_bps: 3_000,
    degradation_rate_bps: 3_000,
    latency_p95_ms: 12_000,
    total_cost_microunits: 30_000,
    average_cost_microunits: 3_000,
    max_run_cost_microunits: 3_000,
    helpful_rate_bps: 6_000,
  },
  alerts: [
    {
      code: "ERROR_RATE_HIGH",
      severity: "critical",
      release_role: "canary",
      metric: "error_rate_bps",
      observed_value: 3_000,
      threshold_value: 1_000,
      blocks_promotion: true,
    },
  ],
  promotion: {
    status: "blocked",
    allowed: false,
    policy_version: "agent-operations-v1",
    reason_codes: ["ERROR_RATE_HIGH"],
    evidence_hash: "c".repeat(64),
  },
  ai_quality_status: "not_configured",
  online_llm_grading: false,
};

function query(data?: unknown, overrides: Record<string, unknown> = {}) {
  return {
    data,
    error: null,
    isError: false,
    isFetching: false,
    isLoading: false,
    refetch: vi.fn(),
    ...overrides,
  };
}

function pageModel(overrides: Record<string, unknown> = {}) {
  return {
    services: query([deployment]),
    report: query(report),
    ...overrides,
  };
}

describe("P3-12 Release 运营页面", () => {
  afterEach(cleanup);

  it("服务清单失败时不继续展示旧 Route 报告", () => {
    vi.mocked(useAgentOperations).mockReturnValue(
      pageModel({
        services: query([deployment], {
          isError: true,
          error: new Error("合成服务查询失败"),
        }),
      }) as never,
    );

    render(
      <MemoryRouter initialEntries={[`/workspace/agent-operations?service=${serviceId}`]}>
        <AgentOperationsPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("Release 运营页面未能加载")).toBeInTheDocument();
    expect(screen.queryByText("阻断晋级")).not.toBeInTheDocument();
    expect(screen.queryByText(deployment.service.name)).not.toBeInTheDocument();
  });

  it("展示后端阻断结论、结构化告警和未配置 AI 质量状态", () => {
    vi.mocked(useAgentOperations).mockReturnValue(pageModel() as never);

    render(
      <MemoryRouter initialEntries={[`/workspace/agent-operations?service=${serviceId}&window=24`]}>
        <AgentOperationsPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("阻断晋级")).toBeInTheDocument();
    expect(screen.getAllByText("错误率超过上限").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("未配置")).toBeInTheDocument();
    expect(screen.getAllByText("30%").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/prompt/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/trace/i)).not.toBeInTheDocument();
  });

  it("切换窗口后使用 URL 中的新窗口重新查询", async () => {
    vi.mocked(useAgentOperations).mockReturnValue(pageModel() as never);
    render(
      <MemoryRouter initialEntries={[`/workspace/agent-operations?service=${serviceId}&window=24`]}>
        <AgentOperationsPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByText("48 小时"));

    await waitFor(() => {
      expect(useAgentOperations).toHaveBeenLastCalledWith(serviceId, 48);
    });
  });
});
