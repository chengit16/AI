/** @description P4-11 工具进度 SSE 的整数游标、重连和协议门禁测试。 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { configureApiClient } from "@/api/client";

import { streamToolRun, ToolRunSseProtocolError } from "./toolRunsSse";

const WORKSPACE_ID = "10000000-0000-4000-8000-000000000411";
const RUN_ID = "20000000-0000-4000-8000-000000000411";

function eventFrame(cursor: number, runState: string, id = String(cursor)) {
  return `id: ${id}\nevent: tool.run.updated\ndata: ${JSON.stringify({
    schema_version: 1,
    workspace_id: WORKSPACE_ID,
    run_id: RUN_ID,
    cursor,
    event_type: "tool.run.updated",
    run_state: runState,
    step_id: null,
    step_state: null,
    attempt_id: null,
    tool_call_id: null,
    error_code: null,
    occurred_at: "2026-08-17T08:00:00Z",
  })}\n\n`;
}

function streamResponse(body: string) {
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(body));
        controller.close();
      },
    }),
    { headers: { "content-type": "text/event-stream" } },
  );
}

describe("工具进度 SSE 客户端", () => {
  afterEach(() => vi.restoreAllMocks());

  it("从详情最新游标恢复，并在断线后携带已确认的新游标", async () => {
    configureApiClient(
      () => ({ workspaceId: WORKSPACE_ID, csrfToken: null }),
      () => undefined,
    );
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(streamResponse(eventFrame(6, "running")))
      .mockResolvedValueOnce(streamResponse(eventFrame(7, "completed")));

    const events = [];
    for await (const event of streamToolRun(WORKSPACE_ID, RUN_ID, {
      lastCursor: 5,
      maxReconnects: 1,
    })) {
      events.push(event);
    }

    expect(events.map((event) => event.cursor)).toEqual([6, 7]);
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Last-Event-ID")).toBe("5");
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get("Last-Event-ID")).toBe("6");
  });

  it("拒绝跳号或与 SSE 帧不一致的游标", async () => {
    configureApiClient(
      () => ({ workspaceId: WORKSPACE_ID, csrfToken: null }),
      () => undefined,
    );
    vi.spyOn(globalThis, "fetch").mockResolvedValue(streamResponse(eventFrame(8, "completed")));

    const consume = async () => {
      for await (const event of streamToolRun(WORKSPACE_ID, RUN_ID, { lastCursor: 5 })) {
        expect(event.cursor).toBe(8);
      }
    };
    await expect(consume()).rejects.toBeInstanceOf(ToolRunSseProtocolError);
  });
});
