/** @description P4-11 工具任务旧详情清空、危险确认和权限裁剪测试。 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ToolRunDetail } from "@/api/services/tools";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";

import ToolRunHistoryPage from ".";
import { useToolRunHistory } from "./useToolRunHistory";

vi.mock("./useToolRunHistory", () => ({ useToolRunHistory: vi.fn() }));
vi.mock("@/hooks/useWorkspaceMenuNavigation", () => ({
  useWorkspaceMenuNavigation: vi.fn(),
}));

const detail: ToolRunDetail = {
  run: {
    run_id: "10000000-0000-4000-8000-000000000411",
    service_id: "20000000-0000-4000-8000-000000000411",
    service_name: "合成高风险工具服务",
    agent_release_id: "30000000-0000-4000-8000-000000000411",
    agent_release_version: 3,
    state: "waiting_confirmation",
    max_steps: 1,
    max_attempts_per_step: 2,
    max_execution_seconds: 300,
    max_cost_microunits: 0,
    requested_by_account_id: "40000000-0000-4000-8000-000000000411",
    step_count: 1,
    completed_step_count: 0,
    total_cost_microunits: 0,
    pending_confirmation_count: 1,
    cancel_requested_at: null,
    deadline_at: "2026-08-17T09:00:00Z",
    created_at: "2026-08-17T08:00:00Z",
    updated_at: "2026-08-17T08:01:00Z",
    completed_at: null,
    version: 4,
  },
  latest_cursor: 5,
  steps: [
    {
      step_id: "50000000-0000-4000-8000-000000000411",
      sequence_no: 1,
      tool_id: "60000000-0000-4000-8000-000000000411",
      tool_version: 1,
      tool_key: "synthetic.write",
      display_name: "合成写入",
      access_mode: "write",
      risk_level: "high",
      canonical_arguments_hash: "a".repeat(64),
      state: "waiting_confirmation",
      timeout_seconds: 30,
      max_attempts: 1,
      max_result_bytes: 1024,
      current_attempt_no: null,
      created_at: "2026-08-17T08:00:00Z",
      updated_at: "2026-08-17T08:01:00Z",
      confirmation: {
        confirmation_id: "70000000-0000-4000-8000-000000000411",
        approval_instance_id: "80000000-0000-4000-8000-000000000411",
        mode: "personal_owner",
        state: "pending",
        risk_level: "high",
        confirmation_hash: "b".repeat(64),
        expires_at: "2026-08-17T09:00:00Z",
        resolved_at: null,
        can_respond: true,
        version: 1,
      },
      attempts: [],
    },
  ],
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

function pageModel(overrides: Record<string, unknown> = {}) {
  return {
    runs: query([detail.run]),
    detailQuery: query(detail),
    detail,
    liveEvent: null,
    streamError: null,
    respond: { isPending: false, mutate: vi.fn() },
    cancel: { isPending: false, mutate: vi.fn() },
    ...overrides,
  };
}

describe("P4-11 工具任务历史页面", () => {
  afterEach(cleanup);

  it("详情查询失败时不展示缓存中的旧任务", () => {
    vi.mocked(useToolRunHistory).mockReturnValue(
      pageModel({ detailQuery: query(detail, { isError: true }), detail }) as never,
    );
    vi.mocked(useWorkspaceMenuNavigation).mockReturnValue({
      visiblePermissionCodes: new Set<string>(),
    } as never);

    render(
      <MemoryRouter initialEntries={[`/workspace/tool-runs?run=${detail.run.run_id}`]}>
        <ToolRunHistoryPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("任务详情未能加载")).toBeInTheDocument();
    expect(screen.queryByText("冻结步骤")).not.toBeInTheDocument();
  });

  it("高风险批准前展示重新授权和可能执行的明确说明", () => {
    vi.mocked(useToolRunHistory).mockReturnValue(pageModel() as never);
    vi.mocked(useWorkspaceMenuNavigation).mockReturnValue({
      visiblePermissionCodes: new Set(["tool.confirmation.respond", "tool.run.cancel"]),
    } as never);

    render(
      <MemoryRouter initialEntries={[`/workspace/tool-runs?run=${detail.run.run_id}`]}>
        <ToolRunHistoryPage />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "批准" }));

    expect(screen.getByText("批准此工具调用？")).toBeInTheDocument();
    expect(screen.getByText(/重新核验当前权限/)).toBeInTheDocument();
    expect(screen.getByText(/可能执行已冻结参数/)).toBeInTheDocument();
  });

  it("没有动作权限时只展示确认事实", () => {
    vi.mocked(useToolRunHistory).mockReturnValue(pageModel() as never);
    vi.mocked(useWorkspaceMenuNavigation).mockReturnValue({
      visiblePermissionCodes: new Set(["tool.run.read"]),
    } as never);

    render(
      <MemoryRouter initialEntries={[`/workspace/tool-runs?run=${detail.run.run_id}`]}>
        <ToolRunHistoryPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("个人确认")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "批准" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "取消任务" })).not.toBeInTheDocument();
  });
});
