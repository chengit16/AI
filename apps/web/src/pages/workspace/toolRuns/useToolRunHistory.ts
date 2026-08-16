/** @description 工具任务列表、详情、确认命令与 SSE 恢复编排。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";
import { useEffect, useRef, useState } from "react";

import { errorMessage } from "@/api/client";
import {
  cancelToolRun,
  confirmToolCall,
  getToolRun,
  getToolRuns,
  rejectToolCall,
  type ToolRunDetail,
} from "@/api/services/tools";
import { streamToolRun, type ToolRunProgressEvent } from "@/api/toolRunsSse";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";

const TERMINAL_STATES = new Set(["completed", "failed", "cancelled", "timed_out"]);

/** 聚合历史页事实；任何详情查询失败都不继续暴露上一次成功结果。 */
export function useToolRunHistory(selectedRunId: string | null) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { workspaceId } = useCurrentWorkspace();
  const [liveEvent, setLiveEvent] = useState<ToolRunProgressEvent | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const detailRef = useRef<ToolRunDetail | null>(null);
  const cursorRef = useRef<{ runId: string; cursor: number } | null>(null);

  // 1. 列表只在存在非终态 Run 时轮询；详情失败立即清空，避免用户操作陈旧任务。
  const runs = useQuery({
    queryKey: ["tool-runs", workspaceId],
    queryFn: ({ signal }) => getToolRuns(workspaceId!, signal),
    enabled: Boolean(workspaceId),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.some((run) => !TERMINAL_STATES.has(run.state)) ? 3_000 : false,
  });
  const detailQuery = useQuery({
    queryKey: ["tool-run", workspaceId, selectedRunId],
    queryFn: ({ signal }) => getToolRun(workspaceId!, selectedRunId!, signal),
    enabled: Boolean(workspaceId && selectedRunId),
    retry: false,
  });
  const detail = detailQuery.isError ? null : (detailQuery.data ?? null);

  // 2. 详情快照建立当前 Run 的恢复游标，切换 Run 时不能沿用上一条流的进度。
  useEffect(() => {
    detailRef.current = detail;
    if (detail && cursorRef.current?.runId !== detail.run.run_id) {
      cursorRef.current = { runId: detail.run.run_id, cursor: detail.latest_cursor };
    }
  }, [detail]);

  const streamEnabled = Boolean(detail && !TERMINAL_STATES.has(detail.run.state));
  // 3. SSE 增量先更新缓存摘要，流结束后再回源收敛完整事实；切换或卸载会取消旧连接。
  useEffect(() => {
    if (!workspaceId || !selectedRunId || !streamEnabled || !detailRef.current) return;
    const controller = new AbortController();
    setStreamError(null);
    const startCursor =
      cursorRef.current?.runId === selectedRunId
        ? cursorRef.current.cursor
        : detailRef.current.latest_cursor;
    cursorRef.current = { runId: selectedRunId, cursor: startCursor };
    void (async () => {
      try {
        for await (const event of streamToolRun(workspaceId, selectedRunId, {
          signal: controller.signal,
          lastCursor: startCursor,
        })) {
          cursorRef.current = { runId: selectedRunId, cursor: event.cursor };
          setLiveEvent(event);
          queryClient.setQueryData<ToolRunDetail>(
            ["tool-run", workspaceId, selectedRunId],
            (current) => applyProgressEvent(current, event),
          );
        }
        await refreshRunFacts(queryClient, workspaceId, selectedRunId);
      } catch (error) {
        if (!controller.signal.aborted) setStreamError(errorMessage(error));
      }
    })();
    return () => controller.abort();
  }, [queryClient, selectedRunId, streamEnabled, workspaceId]);

  // 4. 确认和取消只更新精确详情与列表缓存，最终授权和状态仍由服务端重新判定。
  const respond = useMutation({
    mutationFn: (input: { confirmationId: string; action: "confirm" | "reject" }) =>
      input.action === "confirm"
        ? confirmToolCall(workspaceId!, selectedRunId!, input.confirmationId)
        : rejectToolCall(workspaceId!, selectedRunId!, input.confirmationId),
    onSuccess: async (value, input) => {
      queryClient.setQueryData(["tool-run", workspaceId, selectedRunId], value);
      await queryClient.invalidateQueries({ queryKey: ["tool-runs", workspaceId] });
      void message.success(
        input.action === "confirm" ? "确认已提交，正在重新核验权限" : "工具调用已驳回",
      );
    },
    onError: (error) => void message.error(errorMessage(error)),
  });
  const cancel = useMutation({
    mutationFn: () => cancelToolRun(workspaceId!, selectedRunId!),
    onSuccess: async (value) => {
      queryClient.setQueryData(["tool-run", workspaceId, selectedRunId], value);
      await queryClient.invalidateQueries({ queryKey: ["tool-runs", workspaceId] });
      void message.success("取消请求已提交");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });

  return { runs, detailQuery, detail, liveEvent, streamError, respond, cancel };
}

function applyProgressEvent(
  current: ToolRunDetail | undefined,
  event: ToolRunProgressEvent,
): ToolRunDetail | undefined {
  if (!current || current.run.run_id !== event.runId) return current;
  return {
    ...current,
    latest_cursor: event.cursor,
    run: { ...current.run, state: event.runState },
    steps: current.steps.map((step) =>
      step.step_id === event.stepId && event.stepState ? { ...step, state: event.stepState } : step,
    ),
  };
}

async function refreshRunFacts(
  queryClient: ReturnType<typeof useQueryClient>,
  workspaceId: string,
  runId: string,
) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ["tool-runs", workspaceId] }),
    queryClient.invalidateQueries({ queryKey: ["tool-run", workspaceId, runId] }),
  ]);
}
