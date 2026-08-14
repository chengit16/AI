/** @description P1E-06 SSE 客户端游标、重连和顺序门禁测试。 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { configureApiClient } from "@/api/client";

import { AssistantSseProtocolError, streamAssistantRun } from "./assistantSse";

const WORKSPACE_ID = "10000000-0000-4000-8000-000000000806";
const CONVERSATION_ID = "20000000-0000-4000-8000-000000000806";
const RUN_ID = "30000000-0000-4000-8000-000000000806";

function eventFrame(
  eventId: string,
  eventType: string,
  sequenceNo: number,
  payload: object,
  id = eventId,
) {
  return `${id ? `id: ${id}\n` : ""}event: ${eventType}\ndata: ${JSON.stringify({
    event_id: eventId,
    event_type: eventType,
    schema_version: 1,
    workspace_id: WORKSPACE_ID,
    conversation_id: CONVERSATION_ID,
    message_id: "40000000-0000-4000-8000-000000000806",
    run_id: RUN_ID,
    sequence_no: sequenceNo,
    occurred_at: "2026-08-15T12:00:00Z",
    expires_at: "2026-08-16T12:00:00Z",
    trace_id: "a".repeat(32),
    payload,
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

describe("助手 SSE 客户端", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("断线后带上最后持久化游标重连，snapshot 不覆盖游标", async () => {
    configureApiClient(
      () => ({ workspaceId: WORKSPACE_ID, csrfToken: null }),
      () => undefined,
    );
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        streamResponse(eventFrame("event-1", "message.delta", 1, { delta: "第一段" })),
      )
      .mockResolvedValueOnce(
        streamResponse(eventFrame("event-2", "message.delta", 2, { delta: "完整答案" })),
      )
      .mockResolvedValueOnce(
        streamResponse(eventFrame("snapshot-id", "message.snapshot", 2, { text: "完整答案" }, "")),
      );

    const events = [];
    for await (const event of streamAssistantRun(WORKSPACE_ID, CONVERSATION_ID, RUN_ID, {
      maxReconnects: 2,
    })) {
      events.push(event);
    }

    expect(events.map((event) => event.eventType)).toEqual([
      "message.delta",
      "message.delta",
      "message.snapshot",
    ]);
    expect(events[2]?.persisted).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get("Last-Event-ID")).toBe("event-1");
  });

  it("持久化事件序号回退时立即拒绝展示", async () => {
    configureApiClient(
      () => ({ workspaceId: WORKSPACE_ID, csrfToken: null }),
      () => undefined,
    );
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      streamResponse(
        eventFrame("event-1", "message.delta", 1, { delta: "第一段" }) +
          eventFrame("event-0", "message.delta", 1, { delta: "重复" }),
      ),
    );

    const consume = async () => {
      for await (const event of streamAssistantRun(WORKSPACE_ID, CONVERSATION_ID, RUN_ID)) {
        expect(event.sequenceNo).toBe(1);
      }
    };
    await expect(consume()).rejects.toBeInstanceOf(AssistantSseProtocolError);
  });

  it("连接建立失败后仍使用原游标重连", async () => {
    configureApiClient(
      () => ({ workspaceId: WORKSPACE_ID, csrfToken: null }),
      () => undefined,
    );
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("synthetic network failure"))
      .mockResolvedValueOnce(
        streamResponse(eventFrame("event-2", "message.completed", 2, { text: "已恢复" })),
      );

    const events = [];
    for await (const event of streamAssistantRun(WORKSPACE_ID, CONVERSATION_ID, RUN_ID, {
      lastEventId: "event-1",
      maxReconnects: 1,
    })) {
      events.push(event);
    }

    expect(events).toHaveLength(1);
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get("Last-Event-ID")).toBe("event-1");
  });
});
