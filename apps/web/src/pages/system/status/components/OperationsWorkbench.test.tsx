/** @description 验证运营工作台无权限、错误、空态和危险操作确认。 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useOperationsWorkbench } from "../useOperationsWorkbench";
import { IndexOperationsPanel } from "./IndexOperationsPanel";
import { OperationsWorkbench } from "./OperationsWorkbench";

vi.mock("../useOperationsWorkbench", () => ({
  useOperationsWorkbench: vi.fn(),
}));

function query(data?: unknown, overrides: Record<string, boolean> = {}) {
  return {
    data,
    isError: false,
    isFetching: false,
    isLoading: false,
    ...overrides,
  };
}

function workbenchModel(overrides: Record<string, unknown> = {}) {
  const mutation = { isPending: false, mutate: vi.fn(), mutateAsync: vi.fn() };
  return {
    overview: query({
      checked_at: "2026-08-16T08:00:00Z",
      ingestion: [],
      index_versions: [],
      index_requests: [],
      outbox: [],
      lifecycle_active_count: 0,
    }),
    jobs: query([]),
    indexRequests: query([]),
    indexRuns: query([]),
    outbox: query([]),
    integrationInspection: query(),
    audit: query([]),
    usage: query([]),
    lifecycle: query([]),
    cancelJob: mutation,
    retryJob: mutation,
    requestIndex: mutation,
    replayOutbox: mutation,
    exportWorkspace: mutation,
    executeRetention: mutation,
    purgeWorkspace: mutation,
    refresh: vi.fn(),
    ...overrides,
  };
}

describe("受控运营工作台", () => {
  it("没有读取权限时不展示旧运营数据", () => {
    vi.mocked(useOperationsWorkbench).mockReturnValue(workbenchModel() as never);

    render(
      <OperationsWorkbench
        workspaceId="synthetic-workspace"
        workspaceName="合成空间"
        permissions={new Set()}
      />,
    );

    expect(screen.getByText("当前角色没有运营记录权限")).toBeInTheDocument();
    expect(screen.queryByText("管理任务恢复、索引维护、事件投递和数据生命周期。")).toBeNull();
  });

  it("统一展示查询错误，避免残留部分旧事实", () => {
    vi.mocked(useOperationsWorkbench).mockReturnValue(
      workbenchModel({ jobs: query([], { isError: true }) }) as never,
    );

    render(
      <OperationsWorkbench
        workspaceId="synthetic-workspace"
        workspaceName="合成空间"
        permissions={new Set(["operations.records.read"])}
      />,
    );

    expect(screen.getByText("运营数据暂时无法加载")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新加载" })).toBeInTheDocument();
  });

  it("已授权但没有任务时展示明确空态", () => {
    vi.mocked(useOperationsWorkbench).mockReturnValue(workbenchModel() as never);

    render(
      <OperationsWorkbench
        workspaceId="synthetic-workspace"
        workspaceName="合成空间"
        permissions={new Set(["operations.records.read"])}
      />,
    );

    expect(screen.getByText("暂无入库任务")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "索引 0" })).toBeInTheDocument();
  });

  it("清理索引必须输入服务端固定确认词后才提交", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <IndexOperationsPanel
        requests={[]}
        runs={[]}
        isLoading={false}
        isSubmitting={false}
        permissions={new Set(["operations.index.cleanup"])}
        onSubmit={onSubmit}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /清理失败索引/ }));
    fireEvent.change(screen.getByLabelText(/输入 DELETE_UNRECOVERABLE_INDEX_CHUNKS 确认/), {
      target: { value: "DELETE_UNRECOVERABLE_INDEX_CHUNKS" },
    });
    fireEvent.click(screen.getByRole("button", { name: "登记请求" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith({
        command: "cleanup",
        reasonCode: "OPERATOR_REQUEST",
        confirmation: "DELETE_UNRECOVERABLE_INDEX_CHUNKS",
      });
    });
  });
});
